param(
  [string]$ScanDate = "",
  [string]$Date = "today",
  [ValidateSet("yesterday", "same_day")]
  [string]$Mode = "yesterday",
  [switch]$Overwrite,
  [switch]$DryRun,
  [int]$Limit = 0
)

$ErrorActionPreference = "Stop"
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Resolve-Path (Join-Path $scriptDir "..\..")
Set-Location $repoRoot

$pythonCandidates = @("python", "py -3")
$pythonCmd = $null
foreach ($candidate in $pythonCandidates) {
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

if (-not $pythonCmd) {
  throw "No available Python found."
}

$args = @(
  (Join-Path $scriptDir "benchmark_analysis_runner.py"),
  "--date", $Date,
  "--mode", $Mode
)

if ($ScanDate -and $ScanDate.Trim()) {
  $args += @("--scan-date", $ScanDate.Trim())
}
if ($Overwrite) {
  $args += "--overwrite"
}
if ($DryRun) {
  $args += "--dry-run"
}
if ($Limit -gt 0) {
  $args += @("--limit", "$Limit")
}

Write-Host "[benchmark-analysis] repo: $repoRoot"
Write-Host "[benchmark-analysis] python: $pythonCmd"
Write-Host "[benchmark-analysis] date: $Date mode: $Mode"
if ($ScanDate) { Write-Host "[benchmark-analysis] scan-date: $ScanDate" }
if ($Overwrite) { Write-Host "[benchmark-analysis] overwrite: true" }
if ($DryRun) { Write-Host "[benchmark-analysis] dry-run: true" }
if ($Limit -gt 0) { Write-Host "[benchmark-analysis] limit: $Limit" }

if ($pythonCmd -eq "py -3") {
  & py -3 @args
}
else {
  & $pythonCmd @args
}
