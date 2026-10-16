# Record one source-pinned corpus with the actual local Docker models.
param(
    [ValidateSet(600, 1200, 1800)][int] $Budget = 1200,
    [string] $OutputDirectory = '',
    [string] $Cases = 'evaluations/retrieval/business/cases.jsonl',
    [switch] $UseWorkspaceBackend = $true
)
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)
function Invoke-BusinessDocker([string[]] $Command) {
    & docker.exe @Command
    if ($LASTEXITCODE -ne 0) { throw 'Business evaluation Docker command failed.' }
}
$ready = Invoke-RestMethod 'http://localhost:8080/ready' -TimeoutSec 5
if ($ready.status -ne 'ready') { throw 'API is not ready.' }
$runId = [Guid]::NewGuid().ToString('N')
$containerDir = "/tmp/retrieval-business-$runId"
$outputDir = if ($OutputDirectory) { [IO.Path]::GetFullPath($OutputDirectory) }
             else { Join-Path (Get-Location) "data/evaluations/$runId" }
$corpus = Split-Path -Parent ([IO.Path]::GetFullPath($Cases))
New-Item -ItemType Directory -Force -Path $outputDir | Out-Null
Invoke-BusinessDocker @('compose', 'exec', '-T', 'api', 'python', '-c',
    "from pathlib import Path; Path('$containerDir/scripts').mkdir(parents=True); Path('$containerDir/config').mkdir()")
foreach ($script in @('evaluate_retrieval.py', 'evaluate_retrieval_demo.py', 'record_retrieval_boundary_runs.py', 'record_retrieval_business_runs.py')) {
    Invoke-BusinessDocker @('compose', 'cp', "scripts/$script", "api:$containerDir/scripts/$script")
}
Invoke-BusinessDocker @('compose', 'cp', 'config/evaluation.json', "api:$containerDir/config/evaluation.json")
Invoke-BusinessDocker @('compose', 'cp', $corpus, "api:$containerDir/corpus")
if ($UseWorkspaceBackend) {
    # This child process imports a separate copy; running API code is unchanged.
    Invoke-BusinessDocker @('compose', 'cp', 'backend', "api:$containerDir/backend")
}
try {
    Invoke-BusinessDocker @('compose', 'exec', '-T', '-e', "PYTHONPATH=${containerDir}:/app",
        'api', 'python', "$containerDir/scripts/evaluate_retrieval.py", 'freeze',
        '--cases', "$containerDir/corpus/$(Split-Path -Leaf $Cases)",
        '--output', "$containerDir/frozen.json", '--budget', [string]$Budget)
    Invoke-BusinessDocker @('compose', 'exec', '-T', '-e', "PYTHONPATH=${containerDir}:/app",
        'api', 'python', "$containerDir/scripts/record_retrieval_business_runs.py",
        '--cases', "$containerDir/corpus/$(Split-Path -Leaf $Cases)",
        '--output', "$containerDir/report.json", '--freeze', "$containerDir/frozen.json", '--budget', [string]$Budget)
} finally {
    # Preserve failed/partial observations and the frozen inputs as well.
    & docker.exe compose cp "api:$containerDir/report.json" (Join-Path $outputDir 'report.json')
    & docker.exe compose cp "api:$containerDir/report.json.run" (Join-Path $outputDir 'report.json.run')
}
if ($LASTEXITCODE -ne 0) { throw 'Unable to copy completed evaluation report.' }
Write-Output "Development observations saved to $outputDir. Labels await independent review."
