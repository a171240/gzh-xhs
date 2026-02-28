param(
  [string]$ListenHost = "0.0.0.0",
  [int]$Port = 8790,
  [switch]$Reload,
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

$envLocal = Join-Path $scriptDir ".env.ingest-writer.local"
$envDefault = Join-Path $scriptDir ".env.ingest-writer"
if (Test-Path $envLocal) {
  Import-EnvFile -Path $envLocal
}
elseif (Test-Path $envDefault) {
  Import-EnvFile -Path $envDefault
}

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

$required = @("INGEST_SHARED_TOKEN")
$missing = @()
foreach ($name in $required) {
  $value = [System.Environment]::GetEnvironmentVariable($name)
  if (-not $value) {
    $missing += $name
  }
}

Write-Host "[ingest-writer] repo: $repoRoot"
Write-Host "[ingest-writer] app-dir: $scriptDir"
Write-Host "[ingest-writer] python: $pythonCmd"
Write-Host "[ingest-writer] host: $ListenHost"
Write-Host "[ingest-writer] port: $Port"
Write-Host "[ingest-writer] endpoint: http://127.0.0.1:$Port/internal/healthz"

if ($missing.Count -gt 0) {
  Write-Warning "Missing env vars: $($missing -join ', ')"
}

if ($CheckOnly) {
  Write-Host "[ingest-writer] check-only mode, exit."
  exit 0
}

$reloadArgs = @()
if ($Reload) {
  $reloadArgs = @("--reload")
}

$uvicornArgs = @(
  "-m", "uvicorn",
  "ingest_writer_api:app",
  "--app-dir", $scriptDir,
  "--host", $ListenHost,
  "--port", "$Port"
) + $reloadArgs

if ($pythonCmd -eq "python" -or $pythonCmd -eq "py -3") {
  & cmd /c "$pythonCmd $($uvicornArgs -join ' ')"
}
else {
  & $pythonCmd @uvicornArgs
}
