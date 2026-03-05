param(
    [Parameter(Mandatory = $true)]
    [string]$Url,
    [string]$RepoPath = "",
    [string]$PythonExe = "python",
    [int]$WaitSeconds = 300,
    [int]$PollInterval = 5,
    [int]$MinContentChars = 120
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($RepoPath)) {
    $RepoPath = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
}

$bridgePath = Join-Path $PSScriptRoot "bitable_link_bridge.py"
if (-not (Test-Path $bridgePath)) {
    throw "bitable_link_bridge.py not found: $bridgePath"
}

$scriptCandidates = Get-ChildItem -Path (Join-Path $RepoPath "06-*\scripts\link_to_quotes.py") -File -ErrorAction SilentlyContinue
if (-not $scriptCandidates -or $scriptCandidates.Count -eq 0) {
    throw "link_to_quotes.py not found under $RepoPath/06-*/scripts"
}
$linkScriptPath = $scriptCandidates[0].FullName

if ([string]::IsNullOrWhiteSpace($env:FEISHU_APP_ID) -or [string]::IsNullOrWhiteSpace($env:FEISHU_APP_SECRET)) {
    throw "Missing FEISHU_APP_ID or FEISHU_APP_SECRET in current PowerShell session."
}

# Keep extraction strictly Bitable-first for Douyin.
$env:INGEST_DOUYIN_SOURCE_MODE = "bitable_only"
$env:INGEST_DOUYIN_BITABLE_ENABLED = "true"
$env:INGEST_DOUYIN_BITABLE_READ_FIRST = "true"
$env:INGEST_DOUYIN_BITABLE_WRITE_BACK = "false"
$env:INGEST_DOUYIN_BITABLE_FALLBACK_FULL_SCAN = "true"
$env:INGEST_DOUYIN_STRICT_FULL_TEXT = "true"
$env:INGEST_DOUYIN_SUMMARY_BLOCK = "true"
$env:INGEST_LINK_MIN_CONTENT_CHARS = "$MinContentChars"

if ([string]::IsNullOrWhiteSpace($env:BITABLE_TEXT_FIELD)) { $env:BITABLE_TEXT_FIELD = "文案整理" }
if ([string]::IsNullOrWhiteSpace($env:BITABLE_TEXT_FALLBACK_FIELD)) { $env:BITABLE_TEXT_FALLBACK_FIELD = "文案出参" }

if ([string]::IsNullOrWhiteSpace($env:BITABLE_APP_TOKEN) -or [string]::IsNullOrWhiteSpace($env:BITABLE_TABLE_ID)) {
    throw "Missing BITABLE_APP_TOKEN or BITABLE_TABLE_ID in current PowerShell session."
}

Write-Host "[run-link-via-bitable-local] step1: ensure link exists in bitable and wait for extracted text..."
$bridgeOutput = & $PythonExe $bridgePath --url $Url --wait-seconds $WaitSeconds --poll-interval $PollInterval --min-content-chars $MinContentChars
if ($LASTEXITCODE -ne 0) {
    throw "bitable_link_bridge.py failed: $bridgeOutput"
}
Write-Host $bridgeOutput

Write-Host "[run-link-via-bitable-local] step2: ingest from bitable to local files..."
$isoNow = (Get-Date).ToString("yyyy-MM-ddTHH:mm:sszzz")
$args = @(
    $linkScriptPath,
    "--urls", $Url,
    "--source-time", $isoNow,
    "--source-ref", "local-bitable-auto",
    "--min-content-chars", "$MinContentChars",
    "--apply"
)

& $PythonExe @args
if ($LASTEXITCODE -ne 0) {
    throw "link_to_quotes.py failed with code $LASTEXITCODE"
}

Write-Host "[run-link-via-bitable-local] done"

