# Reproducible real-service observations; reports never authorize calibrated mode.
param(
    [ValidateSet('Demo', 'Boundary', 'All')][string] $Dataset = 'All',
    [ValidateSet(600, 1200, 1800)][int[]] $Budgets = @(1200),
    [string] $ModelProfile = '',
    [string] $OutputDirectory = ''
)
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)

function Invoke-EvaluationDocker([string[]] $Command) {
    & docker.exe @Command
    if ($LASTEXITCODE -ne 0) { throw "Evaluation command failed: $($Command[0..([Math]::Min(2, $Command.Length - 1))] -join ' ')" }
}

$ready = Invoke-RestMethod -Uri 'http://localhost:8000/ready' -TimeoutSec 5
if ($ready.status -ne 'ready') { throw 'API is not ready. Complete local deployment first.' }
$runId = [Guid]::NewGuid().ToString('N')
$containerDir = "/tmp/v3-evaluation-$runId"
$outputDir = if ($OutputDirectory) { [System.IO.Path]::GetFullPath($OutputDirectory) }
             else { Join-Path (Get-Location) "data/evaluations/$runId" }
New-Item -ItemType Directory -Force -Path $outputDir | Out-Null
Invoke-EvaluationDocker @('compose', 'exec', '-T', 'api', 'python', '-c',
    "from pathlib import Path; Path('$containerDir/scripts').mkdir(parents=True)")
foreach ($script in @('evaluate_v3_demo.py', 'record_v3_boundary_runs.py')) {
    Invoke-EvaluationDocker @('compose', 'cp', "scripts/$script", "api:$containerDir/scripts/$script")
}
$environmentArgs = @('-e', "PYTHONPATH=${containerDir}:/app")
if ($ModelProfile) {
    if (-not (Test-Path -LiteralPath $ModelProfile -PathType Leaf)) { throw 'Model profile file does not exist.' }
    Invoke-EvaluationDocker @('compose', 'cp', $ModelProfile, "api:$containerDir/model-profile.json")
    $environmentArgs += @('-e', "RAG_V3_MODEL_PROFILE=$containerDir/model-profile.json")
}
$runtime = @('compose', 'exec', '-T') + $environmentArgs + @('api', 'python')
if ($Dataset -in @('Demo', 'All')) {
    $manifest = 'data/demo_workspace/XL-107/manifest.json'
    if (-not (Test-Path -LiteralPath $manifest)) { throw 'Seed the authored XL-107 demo before evaluating it.' }
    Invoke-EvaluationDocker @('compose', 'cp', $manifest, "api:$containerDir/demo-manifest.json")
    try {
        Invoke-EvaluationDocker ($runtime + @("$containerDir/scripts/evaluate_v3_demo.py",
            '--manifest', "$containerDir/demo-manifest.json", '--output', "$containerDir/demo-report.json",
            '--budgets') + @($Budgets | ForEach-Object { [string]$_ }))
    } finally {
        & docker.exe compose cp "api:$containerDir/demo-report.json" (Join-Path $outputDir 'demo-report.json')
    }
    if ($LASTEXITCODE -ne 0) { throw 'Unable to copy the demo report.' }
}
if ($Dataset -in @('Boundary', 'All')) {
    foreach ($budget in $Budgets) {
        $report = "boundary-$budget.json"
        try {
            Invoke-EvaluationDocker ($runtime + @("$containerDir/scripts/record_v3_boundary_runs.py",
                '--output', "$containerDir/$report", '--budget', [string]$budget))
        } finally {
            # Preserve any completed formats after a later format fails.
            & docker.exe compose cp "api:$containerDir/$report" (Join-Path $outputDir $report)
        }
        if ($LASTEXITCODE -ne 0) { throw 'Unable to copy the boundary report.' }
    }
}
Write-Output "Observations saved to $outputDir. Independent review and paired model validation remain required."
