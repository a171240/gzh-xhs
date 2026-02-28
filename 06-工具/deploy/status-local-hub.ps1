param()

$ErrorActionPreference = "Continue"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$toolDir = Get-ChildItem -Path $repoRoot -Directory |
  Where-Object { Test-Path (Join-Path $_.FullName "scripts\feishu_kb_orchestrator.py") } |
  Select-Object -First 1 -ExpandProperty FullName
if (-not $toolDir) {
  throw "Cannot locate tool directory under repo root: $repoRoot"
}

$pipelineHeartbeat = Join-Path $toolDir "data\feishu-orchestrator\topic-pipeline\heartbeat.json"
$pipelineStateFile = Join-Path $toolDir "data\feishu-orchestrator\topic-pipeline\state.json"
$automationHeartbeat = Join-Path $toolDir "data\automation\scheduler\heartbeat.json"
$writerHealthUrl = "http://127.0.0.1:8790/internal/healthz"
$writerEnvLocal = Join-Path $toolDir "scripts\.env.ingest-writer.local"

function Get-PipelineProcess {
  $all = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue
  if (-not $all) { return @() }
  $pattern = '(?i)feishu_kb_orchestrator\.py.+--pipeline-mode\s+daemon'
  return @(
    $all | Where-Object {
      $_.ProcessId -ne $PID -and
      [string]$_.CommandLine -match $pattern
    }
  )
}

function Get-AutomationSchedulerProcess {
  $all = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue
  if (-not $all) { return @() }
  return @(
    $all | Where-Object {
      $_.ProcessId -ne $PID -and
      [string]$_.CommandLine -match '(?i)automation_scheduler\.py' -and
      [string]$_.CommandLine -notmatch '(?i)--once'
    }
  )
}

Write-Host "=== Local Hub Status ==="
Write-Host "Repo: $repoRoot"

Write-Host ""
Write-Host "[1] Pipeline daemon process"
$pipeline = @(Get-PipelineProcess)
if ($pipeline.Count -gt 0) {
  foreach ($proc in $pipeline) { Write-Host "  running: pid=$($proc.ProcessId)" }
}
else {
  Write-Host "  not running"
}

Write-Host ""
Write-Host "[2] Pipeline heartbeat"
if (Test-Path $pipelineHeartbeat) {
  try {
    $payload = Get-Content -Path $pipelineHeartbeat -Raw -Encoding utf8 | ConvertFrom-Json
    Write-Host "  ts: $($payload.ts)"
    if ($payload.mode) { Write-Host "  mode: $($payload.mode)" }
    if ($payload.last_run_id) { Write-Host "  last_run_id: $($payload.last_run_id)" }
    if ($payload.last_status) {
      Write-Host "  last_status: $($payload.last_status)"
    }
    elseif ($payload.status) {
      Write-Host "  status: $($payload.status)"
    }
  }
  catch {
    Write-Host "  invalid heartbeat json: $($_.Exception.Message)"
  }
}
else {
  Write-Host "  missing: $pipelineHeartbeat"
}

Write-Host ""
Write-Host "[3] Pipeline state"
if (Test-Path $pipelineStateFile) {
  try {
    $state = Get-Content -Path $pipelineStateFile -Raw -Encoding utf8 | ConvertFrom-Json
    Write-Host "  first_round_completed: $($state.first_round_completed)"
    Write-Host "  baseline_ts: $($state.baseline_ts)"
    Write-Host "  last_batch_date: $($state.last_batch_date)"
  }
  catch {
    Write-Host "  invalid state json: $($_.Exception.Message)"
  }
}
else {
  Write-Host "  missing: $pipelineStateFile"
}

Write-Host ""
Write-Host "[4] Automation scheduler process"
$scheduler = @(Get-AutomationSchedulerProcess)
if ($scheduler.Count -gt 0) {
  foreach ($proc in $scheduler) { Write-Host "  running: pid=$($proc.ProcessId)" }
}
else {
  Write-Host "  not running"
}

Write-Host ""
Write-Host "[5] Automation scheduler heartbeat"
if (Test-Path $automationHeartbeat) {
  try {
    $payload = Get-Content -Path $automationHeartbeat -Raw -Encoding utf8 | ConvertFrom-Json
    Write-Host "  ts: $($payload.ts)"
    Write-Host "  mode: $($payload.mode)"
    Write-Host "  last_run_date: $($payload.last_run_date)"
    Write-Host "  last_status: $($payload.last_status)"
    Write-Host "  dry_run: $($payload.dry_run)"
  }
  catch {
    Write-Host "  invalid heartbeat json: $($_.Exception.Message)"
  }
}
else {
  Write-Host "  missing: $automationHeartbeat"
}

Write-Host ""
Write-Host "[6] Ingest writer health"
try {
  $resp = Invoke-RestMethod -Method Get -Uri $writerHealthUrl -TimeoutSec 3
  Write-Host "  endpoint: $writerHealthUrl"
  Write-Host "  status: $($resp.status)"
  if ($null -ne $resp.apply_mode) { Write-Host "  apply_mode: $($resp.apply_mode)" }
  if ($null -ne $resp.signature_required) { Write-Host "  signature_required: $($resp.signature_required)" }
  if ($resp.allowed_source_kinds) { Write-Host "  allowed_source_kinds: $([string]::Join(', ', $resp.allowed_source_kinds))" }
}
catch {
  Write-Host "  unavailable: $writerHealthUrl"
  Write-Host "  error: $($_.Exception.Message)"
}

Write-Host ""
Write-Host "[7] Ingest writer env check"
if (Test-Path $writerEnvLocal) {
  $tokenLine = Get-Content -Path $writerEnvLocal -ErrorAction SilentlyContinue |
    Where-Object { $_ -match '^\s*INGEST_SHARED_TOKEN\s*=' } |
    Select-Object -First 1
  $hmacLine = Get-Content -Path $writerEnvLocal -ErrorAction SilentlyContinue |
    Where-Object { $_ -match '^\s*INGEST_HMAC_SECRET\s*=' } |
    Select-Object -First 1

  $tokenValue = ""
  $hmacValue = ""
  if ($tokenLine) { $tokenValue = ($tokenLine -split '=', 2)[1].Trim() }
  if ($hmacLine) { $hmacValue = ($hmacLine -split '=', 2)[1].Trim() }

  Write-Host "  env_file: $writerEnvLocal"
  Write-Host "  INGEST_SHARED_TOKEN: $(if ($tokenValue) { 'set' } else { 'missing' })"
  Write-Host "  INGEST_HMAC_SECRET: $(if ($hmacValue) { 'set' } else { 'missing' })"
}
else {
  Write-Host "  missing: $writerEnvLocal"
}
