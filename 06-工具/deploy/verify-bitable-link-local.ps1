param(
    [Parameter(Mandatory = $true)]
    [string]$Url,
    [string]$SourceText = "",
    [string]$RepoPath = "",
    [string]$PythonExe = "python",
    [string]$SourceRef = "local-bitable-verify",
    [int]$MinContentChars = 120,
    [string]$BitableAppToken = "",
    [string]$BitableTableId = "",
    [string]$BitableViewId = "",
    [switch]$SkipApply
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($RepoPath)) {
    $RepoPath = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
}

$scriptCandidates = Get-ChildItem -Path (Join-Path $RepoPath "06-*\scripts\link_to_quotes.py") -File -ErrorAction SilentlyContinue
if (-not $scriptCandidates -or $scriptCandidates.Count -eq 0) {
    throw "link_to_quotes.py not found under $RepoPath/06-*/scripts"
}
$ScriptPath = $scriptCandidates[0].FullName

$env:INGEST_DOUYIN_SOURCE_MODE = "bitable_only"
$env:INGEST_DOUYIN_BITABLE_ENABLED = "true"
$env:INGEST_DOUYIN_BITABLE_READ_FIRST = "true"
$env:INGEST_DOUYIN_BITABLE_WRITE_BACK = "false"
$env:INGEST_DOUYIN_BITABLE_FALLBACK_FULL_SCAN = "true"
$env:INGEST_DOUYIN_STRICT_FULL_TEXT = "true"
$env:INGEST_DOUYIN_SUMMARY_BLOCK = "true"
$env:INGEST_LINK_MIN_CONTENT_CHARS = "$MinContentChars"

# Defaults aligned with current project Bitable
$defaultAppToken = "UrwobWA3JadzAcsLqJbc6CThnRd"
$defaultTableId = "tblr1mvEh1bFsUAS"
$defaultViewId = "vew5Oj8RIj"

if ([string]::IsNullOrWhiteSpace($BitableAppToken)) { $BitableAppToken = $defaultAppToken }
if ([string]::IsNullOrWhiteSpace($BitableTableId)) { $BitableTableId = $defaultTableId }
if ([string]::IsNullOrWhiteSpace($BitableViewId)) { $BitableViewId = $defaultViewId }

if ([string]::IsNullOrWhiteSpace($env:BITABLE_APP_TOKEN)) { $env:BITABLE_APP_TOKEN = $BitableAppToken }
if ([string]::IsNullOrWhiteSpace($env:BITABLE_TABLE_ID)) { $env:BITABLE_TABLE_ID = $BitableTableId }
if ([string]::IsNullOrWhiteSpace($env:BITABLE_VIEW_ID)) { $env:BITABLE_VIEW_ID = $BitableViewId }
if ([string]::IsNullOrWhiteSpace($env:BITABLE_TEXT_FIELD)) { $env:BITABLE_TEXT_FIELD = "文案整理" }
if ([string]::IsNullOrWhiteSpace($env:BITABLE_TEXT_FALLBACK_FIELD)) { $env:BITABLE_TEXT_FALLBACK_FIELD = "文案出参" }

if ([string]::IsNullOrWhiteSpace($env:FEISHU_APP_ID) -or [string]::IsNullOrWhiteSpace($env:FEISHU_APP_SECRET)) {
    throw "Missing FEISHU_APP_ID or FEISHU_APP_SECRET in current PowerShell session."
}
if ([string]::IsNullOrWhiteSpace($env:BITABLE_APP_TOKEN) -or [string]::IsNullOrWhiteSpace($env:BITABLE_TABLE_ID)) {
    throw "Missing BITABLE_APP_TOKEN or BITABLE_TABLE_ID."
}

Write-Host ("[verify-bitable-local] bitable app={0} table={1} view={2}" -f $env:BITABLE_APP_TOKEN, $env:BITABLE_TABLE_ID, $env:BITABLE_VIEW_ID)
Write-Host "[verify-bitable-local] bitable write-back disabled for local verification"

$isoNow = (Get-Date).ToString("yyyy-MM-ddTHH:mm:sszzz")

function Invoke-LinkRun {
    param(
        [switch]$Apply
    )
    $args = @(
        $ScriptPath,
        "--urls", $Url,
        "--source-time", $isoNow,
        "--source-ref", $SourceRef,
        "--min-content-chars", "$MinContentChars"
    )
    if (-not [string]::IsNullOrWhiteSpace($SourceText)) {
        $args += @("--source-text", $SourceText)
    }
    if ($Apply) {
        $args += "--apply"
    }
    else {
        $args += "--dry-run"
    }

    Write-Host ("[verify-bitable-local] run mode={0}" -f ($(if ($Apply) { "apply" } else { "dry-run" })))
    & $PythonExe @args
    if ($LASTEXITCODE -ne 0) {
        throw "link_to_quotes.py exited with code $LASTEXITCODE"
    }
}

Invoke-LinkRun

if (-not $SkipApply) {
    Invoke-LinkRun -Apply
}

Write-Host "[verify-bitable-local] done"
