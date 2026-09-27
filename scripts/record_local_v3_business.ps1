# Record one source-pinned corpus with the actual local Docker models.
param(
    [ValidateSet(600, 1200, 1800)][int] $Budget = 1200,
    [string] $OutputDirectory = '',
    [string] $Cases = 'evaluations/v3/business/cases.jsonl',
    [switch] $UseWorkspaceBackend
)
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)
function Invoke-BusinessDocker([string[]] $Command) {
    & docker.exe @Command
    if ($LASTEXITCODE -ne 0) { throw 'Business evaluation Docker command failed.' }
}
$ready = Invoke-RestMethod 'http://localhost:8000/ready' -TimeoutSec 5
if ($ready.status -ne 'ready') { throw 'API is not ready.' }
$runId = [Guid]::NewGuid().ToString('N')
$containerDir = "/tmp/v3-business-$runId"
$outputDir = if ($OutputDirectory) { [IO.Path]::GetFullPath($OutputDirectory) }
             else { Join-Path (Get-Location) "data/evaluations/$runId" }
$corpus = Split-Path -Parent ([IO.Path]::GetFullPath($Cases))
New-Item -ItemType Directory -Force -Path $outputDir | Out-Null
Invoke-BusinessDocker @('compose', 'exec', '-T', 'api', 'python', '-c',
    "from pathlib import Path; Path('$containerDir/scripts').mkdir(parents=True)")
foreach ($script in @('evaluate_v3_demo.py', 'record_v3_boundary_runs.py', 'record_v3_business_runs.py')) {
    Invoke-BusinessDocker @('compose', 'cp', "scripts/$script", "api:$containerDir/scripts/$script")
}
Invoke-BusinessDocker @('compose', 'cp', $corpus, "api:$containerDir/corpus")
if ($UseWorkspaceBackend) {
    # This child process imports a separate copy; running API code is unchanged.
    Invoke-BusinessDocker @('compose', 'cp', 'backend', "api:$containerDir/backend")
}
try {
    Invoke-BusinessDocker @('compose', 'exec', '-T', '-e', "PYTHONPATH=${containerDir}:/app",
        'api', 'python', "$containerDir/scripts/record_v3_business_runs.py",
        '--cases', "$containerDir/corpus/$(Split-Path -Leaf $Cases)",
        '--output', "$containerDir/report.json", '--budget', [string]$Budget)
} finally {
    # A report exists only after SQL rollback and search cleanup both succeed.
    & docker.exe compose cp "api:$containerDir/report.json" (Join-Path $outputDir 'report.json')
}
if ($LASTEXITCODE -ne 0) { throw 'Unable to copy completed evaluation report.' }
Write-Output "Development observations saved to $outputDir. Labels await independent review."
