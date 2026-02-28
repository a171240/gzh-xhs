param(
  [int]$Hours = 24,
  [string[]]$ChatId = @(),
  [switch]$DryRun,
  [switch]$CheckOnly
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Resolve-Path (Join-Path $scriptDir "..\..")
$toolRoot = Resolve-Path (Join-Path $scriptDir "..")
Set-Location $repoRoot

function Import-EnvFile {
  param([string]$Path)
  if (-not (Test-Path $Path)) { return }
  Get-Content $Path | ForEach-Object {
    $line = $_.Trim().TrimStart([char]0xFEFF)
    if (-not $line -or $line.StartsWith("#")) { return }
    $pair = $line -split "=", 2
    if ($pair.Count -ne 2) { return }
    $name = $pair[0].Trim()
    $value = $pair[1].Trim()
    [System.Environment]::SetEnvironmentVariable($name, $value, "Process")
  }
}

Import-EnvFile -Path (Join-Path $scriptDir ".env.feishu.local")
Import-EnvFile -Path (Join-Path $scriptDir ".env.feishu")
Import-EnvFile -Path (Join-Path $scriptDir ".env.ingest-writer.local")
Import-EnvFile -Path (Join-Path $scriptDir ".env.ingest-writer")

$venvPython = Get-ChildItem -Path $toolRoot -Recurse -Filter python.exe -ErrorAction SilentlyContinue |
  Where-Object { $_.FullName -like "*url-reader*.venv*Scripts*python.exe" } |
  Select-Object -First 1 -ExpandProperty FullName

$pythonCandidates = @($venvPython, "python", "py -3") | Where-Object { $_ }
$pythonCmd = $null
foreach ($candidate in $pythonCandidates) {
  if ($candidate -eq "python" -or $candidate -eq "py -3") {
    try {
      & cmd /c "$candidate --version" | Out-Null
      if ($LASTEXITCODE -eq 0) {
        $pythonCmd = $candidate
        break
      }
    }
    catch {
      continue
    }
  }
  elseif (Test-Path $candidate) {
    $pythonCmd = $candidate
    break
  }
}

if (-not $pythonCmd) {
  throw "No available Python found."
}

$required = @("FEISHU_APP_ID", "FEISHU_APP_SECRET", "INGEST_SHARED_TOKEN")
$missing = @()
foreach ($name in $required) {
  $value = [System.Environment]::GetEnvironmentVariable($name)
  if (-not $value) {
    $missing += $name
  }
}

Write-Host "[feishu-backfill] repo: $repoRoot"
Write-Host "[feishu-backfill] python: $pythonCmd"
Write-Host "[feishu-backfill] hours: $Hours"
if ($DryRun) { Write-Host "[feishu-backfill] mode: dry-run" }

if ($missing.Count -gt 0) {
  Write-Warning "Missing env vars: $($missing -join ', ')"
}

if ($CheckOnly) {
  Write-Host "[feishu-backfill] check-only mode, exit."
  exit 0
}

$args = @(
  (Join-Path $scriptDir "feishu_backfill_pull.py"),
  "--hours", "$Hours"
)
foreach ($cid in $ChatId) {
  if ($cid -and $cid.Trim()) {
    $args += @("--chat-id", $cid.Trim())
  }
}
if ($DryRun) {
  $args += "--dry-run"
}

if ($pythonCmd -eq "python" -or $pythonCmd -eq "py -3") {
  & cmd /c "$pythonCmd $($args -join ' ')"
}
else {
  & $pythonCmd @args
}
