param(
  [string]$WslDistro = "Ubuntu-24.04",
  [string]$OpenClawConfigPath = "/root/.openclaw/openclaw.json",
  [string]$GatewayServiceName = "openclaw-gateway.service",
  [string]$WriterHealthUrl = "http://127.0.0.1:8790/internal/healthz",
  [switch]$SkipGatewayRestart
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$toolRoot = Get-ChildItem -Path $repoRoot -Directory |
  Where-Object { $_.Name -like "06-*" } |
  Where-Object { Test-Path (Join-Path $_.FullName "scripts") } |
  Select-Object -First 1 -ExpandProperty FullName
if (-not $toolRoot) {
  throw "Cannot locate tool root directory (expected 06-*/scripts)."
}
$deployDir = Join-Path $toolRoot "deploy"
$scriptDir = Join-Path $toolRoot "scripts"
$promptFile = Join-Path $deployDir "openclaw-feishu-routing-prompt.md"
$backupRoot = Join-Path $deployDir "backups\feishu-cutover"
$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$backupDir = Join-Path $backupRoot $timestamp

if (-not (Test-Path $promptFile)) {
  throw "Routing prompt not found: $promptFile"
}

New-Item -ItemType Directory -Force -Path $backupDir | Out-Null

function Invoke-WslCommand {
  param(
    [Parameter(Mandatory = $true)]
    [string]$Command,
    [switch]$IgnoreError
  )
  $lines = & wsl -d $WslDistro -- bash -lc $Command 2>&1
  $exitCode = $LASTEXITCODE
  $text = [string]::Join("`n", @($lines))
  if ($exitCode -ne 0 -and -not $IgnoreError) {
    throw "WSL command failed (exit=$exitCode): $Command`n$text"
  }
  return $text
}

function Read-EnvFileMap {
  param([string]$Path)
  $map = @{}
  if (-not (Test-Path $Path)) {
    return $map
  }
  Get-Content -Path $Path | ForEach-Object {
    $line = $_.Trim().TrimStart([char]0xFEFF)
    if (-not $line -or $line.StartsWith("#") -or -not $line.Contains("=")) {
      return
    }
    $pair = $line -split "=", 2
    if ($pair.Count -ne 2) {
      return
    }
    $k = $pair[0].Trim()
    $v = $pair[1].Trim().Trim('"').Trim("'")
    if ($k) {
      $map[$k] = $v
    }
  }
  return $map
}

function Merge-Setting {
  param(
    [string]$Key,
    [hashtable[]]$Maps
  )
  $processValueRaw = [System.Environment]::GetEnvironmentVariable($Key, "Process")
  $processValue = if ($null -eq $processValueRaw) { "" } else { [string]$processValueRaw }
  if ($processValue.Trim()) {
    return $processValue.Trim()
  }
  foreach ($m in $Maps) {
    if ($m.ContainsKey($Key) -and [string]$m[$Key] -ne "") {
      return [string]$m[$Key]
    }
  }
  return ""
}

function Save-Text {
  param(
    [string]$Path,
    [string]$Text
  )
  $parent = Split-Path -Parent $Path
  if (-not (Test-Path $parent)) {
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
  }
  Set-Content -Path $Path -Value $Text -Encoding UTF8
}

Write-Host "[cutover] Step 1/4 Backup current runtime config and status..."
$configRaw = Invoke-WslCommand "cat '$OpenClawConfigPath'"
Save-Text -Path (Join-Path $backupDir "openclaw.json.before") -Text $configRaw

$statusBefore = Invoke-WslCommand "openclaw status --deep" -IgnoreError
Save-Text -Path (Join-Path $backupDir "openclaw-status.before.txt") -Text $statusBefore

$serviceBefore = Invoke-WslCommand "systemctl status $GatewayServiceName --no-pager -n 0" -IgnoreError
Save-Text -Path (Join-Path $backupDir "gateway-service.before.txt") -Text $serviceBefore

$logsBefore = Invoke-WslCommand "journalctl -u $GatewayServiceName -n 20 --no-pager" -IgnoreError
Save-Text -Path (Join-Path $backupDir "gateway-logs.before.txt") -Text $logsBefore

$workspacePath = Invoke-WslCommand "python3 - <<'PY'
import json, pathlib
p = pathlib.Path('/root/.openclaw/openclaw.json')
workspace = '/root/.openclaw/workspace'
if p.exists():
    obj = json.loads(p.read_text(encoding='utf-8', errors='ignore'))
    workspace = obj.get('agents', {}).get('defaults', {}).get('workspace') or workspace
print(workspace)
PY"
$workspacePath = $workspacePath.Trim()
if (-not $workspacePath) {
  $workspacePath = "/root/.openclaw/workspace"
}

$remoteAgentsBefore = Invoke-WslCommand "if [ -f '$workspacePath/AGENTS.md' ]; then cat '$workspacePath/AGENTS.md'; fi" -IgnoreError
if ($remoteAgentsBefore.Trim()) {
  Save-Text -Path (Join-Path $backupDir "AGENTS.before.md") -Text $remoteAgentsBefore
}

$remoteRoutingBefore = Invoke-WslCommand "if [ -f '$workspacePath/FEISHU_ROUTING_PROMPT.md' ]; then cat '$workspacePath/FEISHU_ROUTING_PROMPT.md'; fi" -IgnoreError
if ($remoteRoutingBefore.Trim()) {
  Save-Text -Path (Join-Path $backupDir "FEISHU_ROUTING_PROMPT.before.md") -Text $remoteRoutingBefore
}

Write-Host "[cutover] Step 2/4 Validate orchestrator runtime settings..."
$envMaps = @(
  (Read-EnvFileMap (Join-Path $scriptDir ".env.ingest-writer.local")),
  (Read-EnvFileMap (Join-Path $scriptDir ".env.ingest-writer")),
  (Read-EnvFileMap (Join-Path $scriptDir ".env.feishu"))
)

$requiredKeys = @(
  "INGEST_WRITER_BASE_URL",
  "INGEST_SHARED_TOKEN",
  "INGEST_HMAC_SECRET"
)

$resolved = [ordered]@{}
$missing = @()
foreach ($key in $requiredKeys) {
  $value = Merge-Setting -Key $key -Maps $envMaps
  if (-not $value) {
    $missing += $key
    continue
  }
  $resolved[$key] = $value
}

if ($missing.Count -gt 0) {
  throw "Missing required settings: $($missing -join ', ')"
}

$resolved["FEISHU_COMMANDER_WORKERS"] = (Merge-Setting -Key "FEISHU_COMMANDER_WORKERS" -Maps $envMaps)
if (-not $resolved["FEISHU_COMMANDER_WORKERS"]) { $resolved["FEISHU_COMMANDER_WORKERS"] = "2" }
$resolved["FEISHU_COMMANDER_MAX_RETRIES"] = (Merge-Setting -Key "FEISHU_COMMANDER_MAX_RETRIES" -Maps $envMaps)
if (-not $resolved["FEISHU_COMMANDER_MAX_RETRIES"]) { $resolved["FEISHU_COMMANDER_MAX_RETRIES"] = "1" }
$resolved["FEISHU_SKILL_MODEL"] = (Merge-Setting -Key "FEISHU_SKILL_MODEL" -Maps $envMaps)
if (-not $resolved["FEISHU_SKILL_MODEL"]) { $resolved["FEISHU_SKILL_MODEL"] = "gpt-5.3-codex" }
$resolved["FEISHU_REPLY_MAX_CHARS"] = (Merge-Setting -Key "FEISHU_REPLY_MAX_CHARS" -Maps $envMaps)
if (-not $resolved["FEISHU_REPLY_MAX_CHARS"]) { $resolved["FEISHU_REPLY_MAX_CHARS"] = "1500" }

if ([int]$resolved["FEISHU_COMMANDER_WORKERS"] -ne 2) {
  throw "FEISHU_COMMANDER_WORKERS must be 2, got: $($resolved['FEISHU_COMMANDER_WORKERS'])"
}
if ([int]$resolved["FEISHU_COMMANDER_MAX_RETRIES"] -ne 1) {
  throw "FEISHU_COMMANDER_MAX_RETRIES must be 1, got: $($resolved['FEISHU_COMMANDER_MAX_RETRIES'])"
}
if ($resolved["FEISHU_SKILL_MODEL"] -ne "gpt-5.3-codex") {
  throw "FEISHU_SKILL_MODEL must be gpt-5.3-codex, got: $($resolved['FEISHU_SKILL_MODEL'])"
}

Write-Host "[cutover] Step 3/4 Switch Feishu routing to orchestrator-only entry..."
$promptText = Get-Content -Path $promptFile -Raw -Encoding UTF8
$promptB64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($promptText))
$managedAgents = @(
  "# AGENTS.md (Managed)",
  "",
  "Feishu channel runtime.",
  "Treat FEISHU_ROUTING_PROMPT.md as highest priority.",
  "Do not free-form reply.",
  "Always call python 06-工具/scripts/feishu_kb_orchestrator.py first.",
  "Only return reply or reply_segments."
) -join "`n"
$agentsB64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($managedAgents + "`n`n" + $promptText + "`n"))

Invoke-WslCommand "python3 - <<'PY'
import base64
from pathlib import Path
workspace = Path(r'''$workspacePath''')
workspace.mkdir(parents=True, exist_ok=True)
prompt = base64.b64decode('$promptB64').decode('utf-8')
agents = base64.b64decode('$agentsB64').decode('utf-8')
(workspace / 'FEISHU_ROUTING_PROMPT.md').write_text(prompt, encoding='utf-8')
(workspace / 'AGENTS.md').write_text(agents, encoding='utf-8')
print((workspace / 'AGENTS.md').as_posix())
print((workspace / 'FEISHU_ROUTING_PROMPT.md').as_posix())
PY"

if (-not $SkipGatewayRestart) {
  Write-Host "[cutover] Restarting OpenClaw gateway..."
  Invoke-WslCommand "systemctl restart $GatewayServiceName" -IgnoreError | Out-Null
}

Write-Host "[cutover] Step 4/4 Runtime checks..."
$writerHealth = $null
$writerError = ""
try {
  $writerHealth = Invoke-RestMethod -Uri $WriterHealthUrl -Method Get -TimeoutSec 8
}
catch {
  $writerError = $_.Exception.Message
}

$statusAfter = Invoke-WslCommand "openclaw status --deep" -IgnoreError
Save-Text -Path (Join-Path $backupDir "openclaw-status.after.txt") -Text $statusAfter
$serviceAfter = Invoke-WslCommand "systemctl status $GatewayServiceName --no-pager -n 0" -IgnoreError
Save-Text -Path (Join-Path $backupDir "gateway-service.after.txt") -Text $serviceAfter
$logsAfter = Invoke-WslCommand "journalctl -u $GatewayServiceName -n 20 --no-pager" -IgnoreError
Save-Text -Path (Join-Path $backupDir "gateway-logs.after.txt") -Text $logsAfter

$checkpoint = [ordered]@{
  timestamp = $timestamp
  backup_dir = $backupDir
  wsl_distro = $WslDistro
  openclaw_config_path = $OpenClawConfigPath
  workspace_path = $workspacePath
  gateway_service = $GatewayServiceName
  writer_health_url = $WriterHealthUrl
  required_settings = $resolved
  writer_health_ok = [bool]$writerHealth
  writer_health_error = $writerError
  files = [ordered]@{
    openclaw_config_before = (Join-Path $backupDir "openclaw.json.before")
    status_before = (Join-Path $backupDir "openclaw-status.before.txt")
    status_after = (Join-Path $backupDir "openclaw-status.after.txt")
    logs_before = (Join-Path $backupDir "gateway-logs.before.txt")
    logs_after = (Join-Path $backupDir "gateway-logs.after.txt")
  }
}
$checkpointPath = Join-Path $backupDir "rollback-context.json"
$checkpoint | ConvertTo-Json -Depth 20 | Set-Content -Path $checkpointPath -Encoding UTF8

if (-not $writerHealth) {
  throw "Writer health check failed: $writerError"
}

if ($statusAfter -notmatch "Gateway\s+\|\s+reachable") {
  throw "Gateway is not reachable after cutover. See: $($checkpoint.files.status_after)"
}
if ($statusAfter -notmatch "Feishu\s+\|\s+ON\s+\|\s+OK") {
  throw "Feishu channel is not OK after cutover. See: $($checkpoint.files.status_after)"
}

Write-Host ""
Write-Host "=== Cutover Completed ==="
Write-Host "Backup directory: $backupDir"
Write-Host "Rollback context: $checkpointPath"
Write-Host "Writer health: OK ($WriterHealthUrl)"
Write-Host ""
Write-Host "Next:"
Write-Host "1) Run verify script: .\06-工具\deploy\verify-feishu-cutover.ps1 (or from the actual 06-* dir)"
Write-Host "2) Send 5 Feishu acceptance messages (A/B/C/D/E)"
Write-Host "3) Rollback if needed: .\06-工具\deploy\rollback-feishu-orchestrator.ps1 -BackupDir '$backupDir' (or from the actual 06-* dir)"
