# Exercise the backup-failure recovery path without calling Docker or changing data.
$ErrorActionPreference = 'Stop'
$calls = [System.Collections.Generic.List[string]]::new()
$scenario = 'backup'
$createdBackup = $null

function Invoke-FakeDocker {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]] $Parts)
    $command = $Parts -join ' '
    $calls.Add($command)
    $global:LASTEXITCODE = 0
    if ($command -eq 'compose config --format json') {
        return '{"services":{"postgres":{"environment":{"POSTGRES_DB":"test","POSTGRES_USER":"test"}}}}'
    }
    if ($command -eq 'compose ps -q postgres') { return 'fake-postgres' }
    if ($command -eq 'compose run --rm --no-deps migrate alembic heads') {
        return '0029_task_review_workflow (head)'
    }
    if ($command -like 'exec fake-postgres pg_dump *' -and $scenario -eq 'backup') {
        $global:LASTEXITCODE = 1
    }
    if ($command -like 'cp fake-postgres:*' -and $scenario -eq 'migration') {
        $script:createdBackup = $Parts[-1]
        [System.IO.File]::WriteAllBytes($createdBackup, (New-Object byte[] 2048))
    }
    if ($command -like 'exec fake-postgres psql *') { return @('0029', '1', '1') }
    if ($command -eq 'compose up --no-deps --force-recreate migrate' -and $scenario -eq 'migration') {
        $global:LASTEXITCODE = 1
    }
}

function docker { Invoke-FakeDocker @args }
function docker.exe { Invoke-FakeDocker @args }

try {
    . (Join-Path $PSScriptRoot 'deploy_local.ps1')
    throw 'Expected the simulated backup failure.'
} catch {
    if ($_.Exception.Message -notlike '*Docker command failed: exec fake-postgres pg_dump*') {
        throw
    }
}

$stopped = $calls.IndexOf('compose --profile agent stop web api worker indexer beat')
$failedBackup = -1
for ($index = 0; $index -lt $calls.Count; $index++) {
    if ($calls[$index] -like 'exec fake-postgres pg_dump *') { $failedBackup = $index }
}
$restarted = $calls.IndexOf('compose --profile agent start web api worker indexer beat')
if ($stopped -lt 0 -or $failedBackup -le $stopped -or $restarted -le $failedBackup) {
    throw "Writer recovery did not run after backup failure: $($calls -join '; ')"
}
Write-Output 'Simulated backup failure restarted all stopped writers.'

$calls.Clear()
$scenario = 'migration'
try {
    try {
        . (Join-Path $PSScriptRoot 'deploy_local.ps1')
        throw 'Expected the simulated migration failure.'
    } catch {
        if ($_.Exception.Message -notlike '*Docker command failed: compose up --no-deps*') {
            throw
        }
    }
    $migrated = $calls.IndexOf('compose up --no-deps --force-recreate migrate')
    $restored = -1
    for ($index = 0; $index -lt $calls.Count; $index++) {
        if ($calls[$index] -like 'exec fake-postgres pg_restore -U test -d test *') { $restored = $index }
    }
    $restarted = $calls.IndexOf('compose --profile agent start web api worker indexer beat')
    if ($migrated -lt 0 -or $restored -le $migrated -or $restarted -le $restored) {
        throw "Migration recovery did not restore and restart: $($calls -join '; ')"
    }
    Write-Output 'Simulated migration failure restored the backup and restarted writers.'
} finally {
    if ($createdBackup) {
        $target = [System.IO.Path]::GetFullPath($createdBackup)
        $backupRoot = [System.IO.Path]::GetFullPath((Join-Path (Split-Path $PSScriptRoot -Parent) 'backups'))
        if (-not $target.StartsWith($backupRoot + [System.IO.Path]::DirectorySeparatorChar,
                                   [System.StringComparison]::OrdinalIgnoreCase)) {
            throw 'Refusing to remove a backup outside the project backup directory.'
        }
        Remove-Item -LiteralPath $target
    }
}
