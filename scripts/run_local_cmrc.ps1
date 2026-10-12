# Run a copied workspace backend with existing Docker retrieval/model settings.
param(
    [string] $Corpus = 'data/evaluations/cmrc2018-200',
    [string] $OutputDirectory = '',
    [ValidateSet(600, 1200, 1800)][int] $Budget = 1200,
    [int] $MaxCases = 0
)
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)
function Invoke-CmrcDocker([string[]] $Arguments) {
    & docker.exe @Arguments
    if ($LASTEXITCODE -ne 0) { throw 'CMRC Docker command failed.' }
}
$runId = [Guid]::NewGuid().ToString('N')
$containerDir = "/tmp/cmrc-$runId"
$outputDir = if ($OutputDirectory) { [IO.Path]::GetFullPath($OutputDirectory) }
             else { Join-Path (Get-Location) "data/evaluations/cmrc-$runId" }
if (Test-Path -LiteralPath $outputDir) { throw 'Output directory exists; choose a new run directory.' }
if ($MaxCases -lt 0) { throw 'MaxCases must be nonnegative.' }
$corpusPath = [IO.Path]::GetFullPath($Corpus)
if (-not (Test-Path -LiteralPath (Join-Path $corpusPath 'manifest.json'))) { throw 'Prepare the CMRC corpus first.' }
New-Item -ItemType Directory -Path $outputDir | Out-Null
Invoke-CmrcDocker @('compose', 'exec', '-T', 'api', 'python', '-c', "from pathlib import Path; Path('$containerDir').mkdir()")
# Copy checksum-covered code so the frozen hashes identify the current workspace.
foreach ($folder in @('backend', 'scripts', 'tests', 'alembic', 'skills', '.github', 'config')) {
    Invoke-CmrcDocker @('compose', 'cp', $folder, "api:$containerDir/$folder")
}
Invoke-CmrcDocker @('compose', 'cp', 'pyproject.toml', "api:$containerDir/pyproject.toml")
Invoke-CmrcDocker @('compose', 'cp', $corpusPath, "api:$containerDir/corpus")
$collectionFailed = $false
try {
    Invoke-CmrcDocker @('compose', 'exec', '-T', '-e', "PYTHONPATH=${containerDir}:/app", 'api', 'python',
        "$containerDir/scripts/evaluate_cmrc.py", 'run', '--corpus', "$containerDir/corpus", '--output',
        "$containerDir/run.json", '--budget', [string]$Budget, '--max-cases', [string]$MaxCases)
} catch {
    $collectionFailed = $true
    Write-Warning 'Collection reported failures; saved observations will still be scored.'
} finally {
    # Keep completed or failed observations and journal for offline scoring.
    & docker.exe compose cp "api:$containerDir/run.json" (Join-Path $outputDir 'run.json')
    & docker.exe compose cp "api:$containerDir/run.json.run" (Join-Path $outputDir 'run.json.run')
}
if (-not (Test-Path -LiteralPath (Join-Path $outputDir 'run.json'))) { throw 'No collection report was produced.' }
& .venv\Scripts\python.exe scripts/evaluate_cmrc.py score --corpus $corpusPath --runs (Join-Path $outputDir 'run.json') --output (Join-Path $outputDir 'score.json')
if ($LASTEXITCODE -ne 0 -or $collectionFailed) { throw 'CMRC report saved with failures; inspect score.md and raw observations.' }
Write-Output "CMRC report: $(Join-Path $outputDir 'score.md')"
