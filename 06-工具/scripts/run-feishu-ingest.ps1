param(
  [string]$ListenHost = "0.0.0.0",
  [int]$Port = 8787,
  [switch]$Reload,
  [switch]$CheckOnly
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Resolve-Path (Join-Path $scriptDir "..\..")
$toolRoot = Resolve-Path (Join-Path $scriptDir "..")
Set-Location $repoRoot

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
  throw "No available Python found. Please install Python or create url-reader venv."
}

$appDir = $scriptDir

Write-Host "[feishu-ingest] repo: $repoRoot"
Write-Host "[feishu-ingest] app-dir: $appDir"
Write-Host "[feishu-ingest] python: $pythonCmd"
Write-Host "[feishu-ingest] host: $ListenHost"
Write-Host "[feishu-ingest] port: $Port"
Write-Host "[feishu-ingest] endpoint: http://127.0.0.1:$Port/api/feishu/events"

$required = @("FEISHU_APP_ID", "FEISHU_APP_SECRET", "FEISHU_VERIFICATION_TOKEN")
$missing = @()
foreach ($name in $required) {
  $value = [System.Environment]::GetEnvironmentVariable($name)
  if (-not $value) {
    $missing += $name
  }
}

if ($missing.Count -gt 0) {
  Write-Warning "Missing env vars: $($missing -join ', ')"
}

if ($CheckOnly) {
  Write-Host "[feishu-ingest] check-only mode, exit."
  exit 0
}

$reloadArgs = @()
if ($Reload) {
  $reloadArgs = @("--reload")
}

$uvicornArgs = @(
  "-m", "uvicorn",
  "feishu_ingest_server:app",
  "--app-dir", $appDir,
  "--host", $ListenHost,
  "--port", "$Port"
) + $reloadArgs

if ($pythonCmd -eq "python" -or $pythonCmd -eq "py -3") {
  & cmd /c "$pythonCmd $($uvicornArgs -join ' ')"
}
else {
  & $pythonCmd @uvicornArgs
}
