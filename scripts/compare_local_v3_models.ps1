# Sequential operational comparison. Does not train, select or publish a model.
param(
    [string] $CurrentReport = '',
    [switch] $SkipDownload
)
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)
$candidateCompose = @('compose', '-f', 'docker-compose.yml', '-f', 'docker-compose.v3-evaluation.yml', '--profile', 'v3-evaluation')

function Invoke-ComparisonDocker([string[]] $Command) {
    & docker.exe @Command
    if ($LASTEXITCODE -ne 0) { throw "Comparison command failed: $($Command[0..([Math]::Min(2, $Command.Length - 1))] -join ' ')" }
}

function Wait-ComparisonModel([string] $Service, [string[]] $Compose) {
    $containerId = [string](& docker.exe @Compose ps -aq $Service)
    if ($LASTEXITCODE -ne 0 -or -not $containerId.Trim()) { throw "Missing model service: $Service" }
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        $state = [string](& docker.exe inspect --format '{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{end}} {{.State.OOMKilled}}' $containerId.Trim())
        if ($LASTEXITCODE -ne 0) { throw "Unable to inspect $Service" }
        if ($state.Trim() -eq 'running healthy false') { return }
        if ($state -match 'exited|dead| true$') { throw "Model service failed: $Service ($state)" }
        Start-Sleep -Seconds 5
    }
    throw "Model service readiness timed out: $Service"
}

$ready = Invoke-RestMethod -Uri 'http://localhost:8000/ready' -TimeoutSec 5
if ($ready.status -ne 'ready') { throw 'Complete local deployment before comparing models.' }
Wait-ComparisonModel 'retrieval' @('compose', '--profile', 'rag')
Invoke-ComparisonDocker ($candidateCompose + @('config', '--quiet'))
$directory = Join-Path (Get-Location) ('data/evaluations/paired-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Force -Path $directory | Out-Null

if (-not $SkipDownload) {
    $profile = Get-Content -Raw -LiteralPath 'config/rag.v3.multilingual.example.json' | ConvertFrom-Json
    Invoke-ComparisonDocker @('compose', '--profile', 'model-setup', 'run', '--rm', '--no-deps',
        '-e', 'EMBEDDING_LOCAL_MODEL=BAAI/bge-m3', '-e', "EMBEDDING_REVISION=$($profile.embedding_revision)",
        '-e', 'EMBEDDING_DIMENSIONS=1024', '-e', 'RERANKER_LOCAL_MODEL=BAAI/bge-reranker-v2-m3',
        '-e', "RERANKER_REVISION=$($profile.reranker_revision)", '-e', 'HF_HUB_DISABLE_PROGRESS_BARS=1', 'model-download')
}
if ($CurrentReport) {
    Copy-Item -LiteralPath $CurrentReport -Destination (Join-Path $directory 'current.json')
} else {
    & (Join-Path $PSScriptRoot 'record_local_v3_evaluation.ps1') -Dataset Boundary -OutputDirectory (Join-Path $directory 'current')
    Copy-Item -LiteralPath (Join-Path $directory 'current/boundary-1200.json') -Destination (Join-Path $directory 'current.json')
}
$current = Get-Content -Raw -LiteralPath (Join-Path $directory 'current.json') | ConvertFrom-Json
$casesHash = (Get-FileHash -LiteralPath 'evaluations/v3/cases.jsonl' -Algorithm SHA256).Hash.ToLowerInvariant()
if (-not $current.complete -or -not $current.test_data_cleaned -or $current.metrics.observations -ne 192 -or $current.cases_sha256 -ne $casesHash) {
    throw 'Current observations must cover the exact frozen corpus and verified cleanup.'
}

$operationalStopped = $false
try {
    # The local engine has limited RAM; retain one loaded model pair at a time.
    $operationalStopped = $true
    Invoke-ComparisonDocker @('compose', '--profile', 'rag', 'stop', 'retrieval')
    Invoke-ComparisonDocker ($candidateCompose + @('up', '-d', '--build', '--no-deps', 'retrieval-evaluation'))
    Wait-ComparisonModel 'retrieval-evaluation' $candidateCompose
    & (Join-Path $PSScriptRoot 'record_local_v3_evaluation.ps1') -Dataset Boundary `
        -ModelProfile 'config/rag.v3.multilingual.example.json' -OutputDirectory (Join-Path $directory 'multilingual')
    $multilingual = Get-Content -Raw -LiteralPath (Join-Path $directory 'multilingual/boundary-1200.json') | ConvertFrom-Json
    if (-not $multilingual.complete -or -not $multilingual.test_data_cleaned -or $multilingual.cases_sha256 -ne $current.cases_sha256) {
        throw 'Candidate observations or cleanup are incomplete.'
    }
    $machine = & docker.exe info --format '{{json .NCPU}} {{json .MemTotal}}'
    if ($LASTEXITCODE -ne 0) { throw 'Unable to record the Docker machine profile.' }
    $comparison = [ordered]@{
        generated_at = [DateTime]::UtcNow.ToString('o')
        scope = 'Sequential synthetic rules-mode observations; no independently reviewed classifier or paired older baseline'
        release_approved = $false
        selected_model = $null
        cases_sha256 = $current.cases_sha256
        docker_cpu_and_memory = [string]$machine
        current = @{ identity = $current.identity; metrics = $current.metrics; per_format = $current.per_format }
        multilingual = @{ identity = $multilingual.identity; metrics = $multilingual.metrics; per_format = $multilingual.per_format }
        independent_review_completed = $false
        baseline_measured = $false
    } | ConvertTo-Json -Depth 20
    [System.IO.File]::WriteAllText((Join-Path $directory 'comparison.json'), $comparison,
                                 [System.Text.UTF8Encoding]::new($false))
} finally {
    if ($operationalStopped) {
        # Restoration is required even when warmup or any evaluation fails.
        try { Invoke-ComparisonDocker ($candidateCompose + @('stop', 'retrieval-evaluation')) }
        finally {
            Invoke-ComparisonDocker @('compose', '--profile', 'rag', 'up', '-d', '--no-deps', 'retrieval')
            Wait-ComparisonModel 'retrieval' @('compose', '--profile', 'rag')
        }
    }
}
Write-Output "Operational comparison saved to $directory. The configured retrieval model service is ready."
