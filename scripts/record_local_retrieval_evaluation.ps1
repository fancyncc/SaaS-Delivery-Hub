# Reproducible real-service observations; reports never authorize calibrated mode.
param(
    [ValidateSet('Demo', 'Boundary', 'All')][string] $Dataset = 'All',
    [ValidateSet(600, 1200, 1800)][int[]] $Budgets = @(1200),
    [ValidateSet('md', 'txt', 'docx', 'csv', 'json', 'pdf', 'pptx', 'xlsx')]
    [string[]] $Formats = @('md', 'txt', 'docx', 'csv', 'json', 'pdf', 'pptx', 'xlsx'),
    [string] $ModelProfile = '',
    [string] $OutputDirectory = '',
    [switch] $UseWorkspaceBackend = $true
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
$containerDir = "/tmp/retrieval-evaluation-$runId"
$outputDir = if ($OutputDirectory) { [System.IO.Path]::GetFullPath($OutputDirectory) }
             else { Join-Path (Get-Location) "data/evaluations/$runId" }
New-Item -ItemType Directory -Force -Path $outputDir | Out-Null
Invoke-EvaluationDocker @('compose', 'exec', '-T', 'api', 'python', '-c',
    "from pathlib import Path; Path('$containerDir/scripts').mkdir(parents=True); Path('$containerDir/config').mkdir()")
foreach ($script in @('evaluate_retrieval.py', 'evaluate_retrieval_demo.py', 'record_retrieval_boundary_runs.py')) {
    Invoke-EvaluationDocker @('compose', 'cp', "scripts/$script", "api:$containerDir/scripts/$script")
}
Invoke-EvaluationDocker @('compose', 'cp', 'config/evaluation.json', "api:$containerDir/config/evaluation.json")
if ($UseWorkspaceBackend) {
    # The evaluator imports an isolated workspace copy. Running API code and
    # deployed indexes remain unchanged until the normal deployment completes.
    Invoke-EvaluationDocker @('compose', 'cp', 'backend', "api:$containerDir/backend")
}
$environmentArgs = @('-e', "PYTHONPATH=${containerDir}:/app")
if ($ModelProfile) {
    if (-not (Test-Path -LiteralPath $ModelProfile -PathType Leaf)) { throw 'Model profile file does not exist.' }
    Invoke-EvaluationDocker @('compose', 'cp', $ModelProfile, "api:$containerDir/model-profile.json")
    $environmentArgs += @('-e', "RETRIEVAL_MODEL_PROFILE=$containerDir/model-profile.json")
}
$runtime = @('compose', 'exec', '-T') + $environmentArgs + @('api', 'python')
if ($Dataset -in @('Demo', 'All')) {
    $manifest = 'data/demo_workspace/XL-107/manifest.json'
    if (-not (Test-Path -LiteralPath $manifest)) { throw 'Seed the authored XL-107 demo before evaluating it.' }
    Invoke-EvaluationDocker @('compose', 'cp', $manifest, "api:$containerDir/demo-manifest.json")
    try {
        Invoke-EvaluationDocker ($runtime + @("$containerDir/scripts/evaluate_retrieval_demo.py",
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
        Invoke-EvaluationDocker ($runtime + @("$containerDir/scripts/evaluate_retrieval.py", 'freeze',
            '--cases', '/app/evaluations/retrieval/cases.jsonl', '--output', "$containerDir/frozen-$budget.json",
            '--budget', [string]$budget))
        try {
            Invoke-EvaluationDocker ($runtime + @("$containerDir/scripts/record_retrieval_boundary_runs.py",
                '--cases', '/app/evaluations/retrieval/cases.jsonl', '--freeze', "$containerDir/frozen-$budget.json",
                '--output', "$containerDir/$report", '--budget', [string]$budget, '--formats') + $Formats)
        } finally {
            # Preserve any completed formats after a later format fails.
            & docker.exe compose cp "api:$containerDir/$report" (Join-Path $outputDir $report)
            & docker.exe compose cp "api:$containerDir/$report.run" (Join-Path $outputDir "$report.run")
        }
        if ($LASTEXITCODE -ne 0) { throw 'Unable to copy the boundary report.' }
    }
}
Write-Output "Observations saved to $outputDir. Independent review and paired model validation remain required."
