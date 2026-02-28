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

function Get-PipelineProcess {
  $all = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue
  if (-not $all) { return @() }
  return $all | Where-Object {
    $_.CommandLine -and
    $_.CommandLine -like "*feishu_kb_orchestrator.py*" -and
    $_.CommandLine -like "*--pipeline-mode*" -and
    $_.CommandLine -like "*daemon*"
  }
}

function Get-AutomationSchedulerProcess {
  $all = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue
  if (-not $all) { return @() }
  return $all | Where-Object {
    $_.CommandLine -and
    $_.CommandLine -like "*automation_scheduler.py*" -and
    $_.CommandLine -notlike "*--once*"
  }
}

Write-Host "=== Local Hub Status ==="
Write-Host "Repo: $repoRoot"

Write-Host ""
Write-Host "[1] Pipeline daemon process"
$pipeline = Get-PipelineProcess
if ($pipeline -and $pipeline.Count -gt 0) {
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
$scheduler = Get-AutomationSchedulerProcess
if ($scheduler -and $scheduler.Count -gt 0) {
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
