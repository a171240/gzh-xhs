param(
  [switch]$StartPipeline = $false,
  [switch]$PipelineDryRun = $false,
  [switch]$PipelineForceBatch = $false,
  [int]$PipelinePollSec = 60,
  [switch]$StartAutomationScheduler = $false,
  [switch]$AutomationDryRun = $false,
  [int]$AutomationPollSec = 60
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$toolDir = Get-ChildItem -Path $repoRoot -Directory |
  Where-Object { Test-Path (Join-Path $_.FullName "scripts\feishu_kb_orchestrator.py") } |
  Select-Object -First 1 -ExpandProperty FullName
if (-not $toolDir) {
  throw "Cannot locate tool directory under repo root: $repoRoot"
}

$scriptsDir = Join-Path $toolDir "scripts"
$orchestrator = Join-Path $scriptsDir "feishu_kb_orchestrator.py"
$automationScheduler = Join-Path $scriptsDir "automation_scheduler.py"
$envLocal = Join-Path $scriptsDir ".env.ingest-writer.local"

$pipelineLogDir = Join-Path $toolDir "data\feishu-orchestrator\topic-pipeline"
$pipelineOutLog = Join-Path $pipelineLogDir "daemon-out.log"
$pipelineErrLog = Join-Path $pipelineLogDir "daemon-err.log"

$automationLogDir = Join-Path $toolDir "data\automation\scheduler"
$automationOutLog = Join-Path $automationLogDir "daemon-out.log"
$automationErrLog = Join-Path $automationLogDir "daemon-err.log"

function Import-EnvFile {
  param([string]$Path)
  if (-not (Test-Path $Path)) { return }
  Get-Content $Path | ForEach-Object {
    $line = $_.Trim().TrimStart([char]0xFEFF)
    if (-not $line -or $line.StartsWith("#")) { return }
    $pair = $line -split "=", 2
    if ($pair.Count -ne 2) { return }
    [System.Environment]::SetEnvironmentVariable($pair[0].Trim(), $pair[1].Trim(), "Process")
  }
}

function Resolve-PythonExe {
  $cmdPython = Get-Command python -ErrorAction SilentlyContinue
  if ($cmdPython) { return $cmdPython.Source }
  throw "Python executable not found in PATH."
}

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

Set-Location $repoRoot
Import-EnvFile -Path $envLocal

Write-Host "=== Local Hub ==="
Write-Host "Repo: $repoRoot"
Write-Host "Scripts: $scriptsDir"
Write-Host "Env: $envLocal"

if (-not $StartPipeline -and -not $StartAutomationScheduler) {
  Write-Host ""
  Write-Host "No daemon requested. Use -StartPipeline and/or -StartAutomationScheduler."
  exit 0
}

$pythonExe = Resolve-PythonExe

if ($StartPipeline) {
  if (-not (Test-Path $orchestrator)) {
    throw "Missing orchestrator: $orchestrator"
  }

  $existing = Get-PipelineProcess
  if ($existing -and $existing.Count -gt 0) {
    Write-Host ""
    Write-Host "Pipeline daemon already running:"
    foreach ($proc in $existing) { Write-Host "  pid=$($proc.ProcessId)" }
  }
  else {
    New-Item -ItemType Directory -Force -Path $pipelineLogDir | Out-Null
    $args = @(
      $orchestrator,
      "--pipeline-mode", "daemon",
      "--pipeline-poll-sec", [string]([Math]::Max(5, $PipelinePollSec))
    )
    if ($PipelineDryRun) { $args += "--pipeline-dry-run" }
    if ($PipelineForceBatch) { $args += "--pipeline-force-batch" }

    Start-Process `
      -FilePath $pythonExe `
      -ArgumentList $args `
      -WorkingDirectory $repoRoot `
      -WindowStyle Hidden `
      -RedirectStandardOutput $pipelineOutLog `
      -RedirectStandardError $pipelineErrLog | Out-Null

    Start-Sleep -Seconds 1
    $started = Get-PipelineProcess
    if (-not $started -or $started.Count -eq 0) {
      throw "Failed to start pipeline daemon. Check logs: $pipelineOutLog / $pipelineErrLog"
    }

    Write-Host ""
    Write-Host "Pipeline daemon started."
    foreach ($proc in $started) { Write-Host "  pid=$($proc.ProcessId)" }
    Write-Host "Out log: $pipelineOutLog"
    Write-Host "Err log: $pipelineErrLog"
  }
}

if ($StartAutomationScheduler) {
  if (-not (Test-Path $automationScheduler)) {
    throw "Missing automation scheduler: $automationScheduler"
  }

  $existing = Get-AutomationSchedulerProcess
  if ($existing -and $existing.Count -gt 0) {
    Write-Host ""
    Write-Host "Automation scheduler already running:"
    foreach ($proc in $existing) { Write-Host "  pid=$($proc.ProcessId)" }
  }
  else {
    New-Item -ItemType Directory -Force -Path $automationLogDir | Out-Null
    $args = @(
      $automationScheduler,
      "--poll-sec", [string]([Math]::Max(10, $AutomationPollSec))
    )
    if ($AutomationDryRun) { $args += "--dry-run" }

    Start-Process `
      -FilePath $pythonExe `
      -ArgumentList $args `
      -WorkingDirectory $repoRoot `
      -WindowStyle Hidden `
      -RedirectStandardOutput $automationOutLog `
      -RedirectStandardError $automationErrLog | Out-Null

    Start-Sleep -Seconds 1
    $started = Get-AutomationSchedulerProcess
    if (-not $started -or $started.Count -eq 0) {
      throw "Failed to start automation scheduler. Check logs: $automationOutLog / $automationErrLog"
    }

    Write-Host ""
    Write-Host "Automation scheduler started."
    foreach ($proc in $started) { Write-Host "  pid=$($proc.ProcessId)" }
    Write-Host "Out log: $automationOutLog"
    Write-Host "Err log: $automationErrLog"
  }
}
