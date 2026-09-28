# Repeatable local Docker release. Run from any directory with PowerShell 7.
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)

function Invoke-DockerChecked([string[]] $Command) {
    & docker.exe @Command
    if ($LASTEXITCODE -ne 0) { throw "Docker command failed: $($Command[0..([Math]::Min(2, $Command.Length - 1))] -join ' ')" }
}

$compose = docker compose config --format json | ConvertFrom-Json
$database = [string]$compose.services.postgres.environment.POSTGRES_DB
$owner = [string]$compose.services.postgres.environment.POSTGRES_USER
if ($database -notmatch '^[A-Za-z0-9_]+$' -or $owner -notmatch '^[A-Za-z0-9_]+$') {
    throw 'Database name or owner contains unsupported characters.'
}
$postgres = (& docker compose ps -q postgres | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or -not $postgres) {
    throw 'PostgreSQL container is not running.'
}

Invoke-DockerChecked @('compose', 'build', 'migrate')
$headOutput = & docker compose run --rm --no-deps migrate alembic heads
if ($LASTEXITCODE -ne 0) { throw 'Unable to resolve Alembic head from the release image.' }
$head = ($headOutput | Select-String '^([a-zA-Z0-9_]+) \(head\)$' | Select-Object -Last 1).Matches.Groups[1].Value
if (-not $head) { throw 'Alembic head was not reported by the release image.' }

$writerServices = @('web', 'api', 'worker', 'indexer', 'beat')
$writersStopped = $false
$backupVerified = $false
$migrationStarted = $false
$migrationComplete = $false
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$backupDir = Join-Path (Get-Location) 'backups'
$backup = Join-Path $backupDir "$database-$stamp.dump"
$archive = "/tmp/$database-$stamp.dump"

try {
    $writersStopped = $true
    Invoke-DockerChecked (@('compose', '--profile', 'agent', 'stop') + $writerServices)
    New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
    Invoke-DockerChecked @('exec', $postgres, 'pg_dump', '-U', $owner, '-d', $database, '-Fc', '-f', $archive)
    Invoke-DockerChecked @('cp', "${postgres}:$archive", $backup)
    Invoke-DockerChecked @('exec', $postgres, 'pg_restore', '--list', $archive) | Out-Null
    if ((Get-Item -LiteralPath $backup).Length -lt 1024) { throw 'Backup file is unexpectedly small.' }

    $verifyDb = "release_verify_$stamp" -replace '-', '_'
    Invoke-DockerChecked @('exec', $postgres, 'createdb', '-U', $owner, $verifyDb)
    try {
        Invoke-DockerChecked @('exec', $postgres, 'pg_restore', '-U', $owner, '-d', $verifyDb, '--no-owner', '--exit-on-error', $archive)
        $query = 'SELECT version_num FROM alembic_version; SELECT count(*) FROM implementation_projects; SELECT count(*) FROM knowledge_documents'
        $original = (& docker exec $postgres psql -U $owner -d $database -Atc $query) -join '|'
        if ($LASTEXITCODE -ne 0) { throw 'Unable to inspect the source database.' }
        $restored = (& docker exec $postgres psql -U $owner -d $verifyDb -Atc $query) -join '|'
        if ($LASTEXITCODE -ne 0 -or $original -ne $restored) { throw 'Restored database does not match source revision and row counts.' }
    } finally {
        Invoke-DockerChecked @('exec', $postgres, 'dropdb', '-U', $owner, '--if-exists', $verifyDb)
    }
    $backupVerified = $true

    $migrationStarted = $true
    Invoke-DockerChecked @('compose', 'up', '--no-deps', '--force-recreate', 'migrate')
    $current = (& docker exec $postgres psql -U $owner -d $database -Atc 'SELECT version_num FROM alembic_version').Trim()
    if ($LASTEXITCODE -ne 0 -or $current -ne $head) { throw "Schema revision $current does not match $head." }
    $migrationComplete = $true

    Invoke-DockerChecked @('compose', '--profile', 'rag', '--profile', 'agent', 'up', '-d', '--build',
        'retrieval', 'api', 'worker', 'indexer', 'beat', 'web')
    $ready = $null
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        try {
            $ready = Invoke-RestMethod -Uri 'http://localhost:8000/ready' -TimeoutSec 5
            if ($ready.status -eq 'ready' -and $ready.schema_revision -eq $head) { break }
        } catch { }
        Start-Sleep -Seconds 2
    }
    if ($ready.status -ne 'ready' -or $ready.schema_revision -ne $head) { throw 'API readiness check failed.' }
    $retrievalId = (docker compose ps -q retrieval).Trim()
    $retrievalHealth = ''
    for ($attempt = 0; $attempt -lt 36; $attempt++) {
        $retrievalHealth = (& docker inspect --format '{{.State.Health.Status}}' $retrievalId).Trim()
        if ($retrievalHealth -eq 'healthy') { break }
        Start-Sleep -Seconds 5
    }
    if ($retrievalHealth -ne 'healthy') { throw 'Retrieval readiness check timed out.' }
    Invoke-DockerChecked @('compose', 'exec', '-T', 'retrieval', 'python', '-c',
        "import urllib.request; urllib.request.urlopen('http://localhost:8010/health/ready', timeout=10)")

    # Parser/model identity changes invalidate the derived V3 side index. The
    # single beat schedules the indexer in bounded batches; do not report a
    # synchronized release while old-identity documents are still pending.
    $v3Identity = (& docker compose exec -T api python -c 'from backend.rag_v3_index import identity; print(identity())').Trim()
    if ($LASTEXITCODE -ne 0 -or $v3Identity -notmatch '^v3-structure-1:[a-f0-9]{32}$') {
        throw 'Unable to resolve the deployed V3 index identity.'
    }
    $lastBackfillState = ''
    $v3Synchronized = $false
    for ($attempt = 0; $attempt -lt 180; $attempt++) {
        $query = "SELECT count(*) FILTER (WHERE phase='ready' AND identity='$v3Identity'), count(*) FILTER (WHERE phase='failed'), count(*) FROM rag_v3_documents;"
        $backfillState = ((& docker exec $postgres psql -U $owner -d $database -At -F '|' -c $query) -join '').Trim()
        if ($LASTEXITCODE -ne 0 -or $backfillState -notmatch '^\d+\|\d+\|\d+$') {
            throw 'Unable to inspect the V3 index backfill state.'
        }
        if ($backfillState -ne $lastBackfillState) {
            Write-Output "V3 backfill state (ready-current|failed|total): $backfillState"
            $lastBackfillState = $backfillState
        }
        $counts = $backfillState.Split('|') | ForEach-Object { [int]$_ }
        if ($counts[1] -gt 0) { throw "V3 backfill contains $($counts[1]) failed document(s)." }
        if ($counts[0] -eq $counts[2]) {
            $v3Synchronized = $true
            break
        }
        Start-Sleep -Seconds 5
    }
    if (-not $v3Synchronized) { throw 'V3 index backfill did not finish within 15 minutes.' }
    Write-Output "Local release ready at revision $head with synchronized V3 indexes. Verified backup: $backup"
} catch {
    $failure = $_
    if ($migrationStarted -and -not $migrationComplete -and $backupVerified) {
        Write-Warning "Migration failed. Restoring validated backup $backup."
        try {
            Invoke-DockerChecked @('exec', $postgres, 'dropdb', '-U', $owner, '--force', $database)
            Invoke-DockerChecked @('exec', $postgres, 'createdb', '-U', $owner, $database)
            Invoke-DockerChecked @('exec', $postgres, 'pg_restore', '-U', $owner, '-d', $database, '--no-owner', '--exit-on-error', $archive)
        } catch {
            throw "Release failed: $failure. Database restore also failed: $_. Writers remain stopped."
        }
    }
    if ($writersStopped) {
        try {
            Invoke-DockerChecked (@('compose', '--profile', 'agent', 'start') + $writerServices)
        } catch {
            throw "Release failed: $failure. Restarting services also failed: $_."
        }
    }
    throw $failure
}
