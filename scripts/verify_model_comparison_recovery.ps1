# Simulate failed candidate warmup; no Docker process or business data is touched.
$ErrorActionPreference = 'Stop'
$calls = [System.Collections.Generic.List[string]]::new()
$temporaryReport = Join-Path ([System.IO.Path]::GetTempPath()) ('v3-comparison-' + [Guid]::NewGuid().ToString('N') + '.json')

function docker.exe {
    $command = $args -join ' '
    $calls.Add($command)
    $global:LASTEXITCODE = 0
    if ($command -like '*ps -aq retrieval-evaluation') { return 'fake-candidate' }
    if ($command -like '*ps -aq retrieval') { return 'fake-current' }
    if ($command -like 'inspect *fake-candidate') { return 'exited starting true' }
    if ($command -like 'inspect *fake-current') { return 'running healthy false' }
}

function Invoke-RestMethod { return @{ status = 'ready' } }

try {
    $hash = (Get-FileHash -LiteralPath (Join-Path (Split-Path $PSScriptRoot -Parent) 'evaluations/v3/cases.jsonl') -Algorithm SHA256).Hash.ToLowerInvariant()
    @{ complete = $true; test_data_cleaned = $true; metrics = @{ observations = 192 }; cases_sha256 = $hash } |
        ConvertTo-Json | Set-Content -LiteralPath $temporaryReport -Encoding utf8
    try {
        & (Join-Path $PSScriptRoot 'compare_local_v3_models.ps1') -SkipDownload -CurrentReport $temporaryReport
        throw 'Expected candidate warmup to fail.'
    } catch {
        if ($_.Exception.Message -notlike '*Model service failed: retrieval-evaluation*') { throw }
    }
    $stopped = $calls.IndexOf('compose --profile rag stop retrieval')
    $candidateStopped = -1
    for ($index = 0; $index -lt $calls.Count; $index++) {
        if ($calls[$index] -like '*stop retrieval-evaluation') { $candidateStopped = $index }
    }
    $restored = $calls.IndexOf('compose --profile rag up -d --no-deps retrieval')
    if ($stopped -lt 0 -or $candidateStopped -le $stopped -or $restored -le $candidateStopped) {
        throw 'Candidate failure did not stop the candidate and restore the operational model.'
    }
    Write-Output 'Simulated candidate warmup failure restored and verified the operational model.'
} finally {
    Remove-Item -LiteralPath $temporaryReport -ErrorAction SilentlyContinue
}
