param(
  [string]$BackupDir = "",
  [string]$WslDistro = "Ubuntu-24.04",
  [string]$OpenClawConfigPath = "/root/.openclaw/openclaw.json",
  [string]$GatewayServiceName = "openclaw-gateway.service"
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
$backupRoot = Join-Path $toolRoot "deploy\backups\feishu-cutover"

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

if (-not $BackupDir) {
  if (-not (Test-Path $backupRoot)) {
    throw "No backup root found: $backupRoot"
  }
  $latest = Get-ChildItem -Path $backupRoot -Directory |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1
  if (-not $latest) {
    throw "No backup directory found under: $backupRoot"
  }
  $BackupDir = $latest.FullName
}

if (-not (Test-Path $BackupDir)) {
  throw "BackupDir not found: $BackupDir"
}

$contextPath = Join-Path $BackupDir "rollback-context.json"
$configBeforePath = Join-Path $BackupDir "openclaw.json.before"
if (-not (Test-Path $configBeforePath)) {
  throw "Backup config missing: $configBeforePath"
}

$workspacePath = "/root/.openclaw/workspace"
$hadAgentsBefore = $false
$hadRoutingBefore = $false
if (Test-Path $contextPath) {
  $context = Get-Content -Path $contextPath -Raw -Encoding UTF8 | ConvertFrom-Json -Depth 20
  if ($context.workspace_path) {
    $workspacePath = [string]$context.workspace_path
  }
}

$agentsBeforePath = Join-Path $BackupDir "AGENTS.before.md"
$routingBeforePath = Join-Path $BackupDir "FEISHU_ROUTING_PROMPT.before.md"
if (Test-Path $agentsBeforePath) { $hadAgentsBefore = $true }
if (Test-Path $routingBeforePath) { $hadRoutingBefore = $true }

Write-Host "[rollback] 恢复 OpenClaw 配置文件..."
$configBeforeRaw = Get-Content -Path $configBeforePath -Raw -Encoding UTF8
$configB64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($configBeforeRaw))
Invoke-WslCommand "python3 - <<'PY'
import base64
from pathlib import Path
path = Path(r'''$OpenClawConfigPath''')
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(base64.b64decode('$configB64').decode('utf-8'), encoding='utf-8')
print(path.as_posix())
PY" | Out-Null

Write-Host "[rollback] 恢复路由提示词文件..."
if ($hadAgentsBefore) {
  $agentsBeforeRaw = Get-Content -Path $agentsBeforePath -Raw -Encoding UTF8
  $agentsB64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($agentsBeforeRaw))
  Invoke-WslCommand "python3 - <<'PY'
import base64
from pathlib import Path
workspace = Path(r'''$workspacePath''')
workspace.mkdir(parents=True, exist_ok=True)
(workspace / 'AGENTS.md').write_text(base64.b64decode('$agentsB64').decode('utf-8'), encoding='utf-8')
print((workspace / 'AGENTS.md').as_posix())
PY" | Out-Null
}
else {
  Invoke-WslCommand "rm -f '$workspacePath/AGENTS.md'" -IgnoreError | Out-Null
}

if ($hadRoutingBefore) {
  $routingBeforeRaw = Get-Content -Path $routingBeforePath -Raw -Encoding UTF8
  $routingB64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($routingBeforeRaw))
  Invoke-WslCommand "python3 - <<'PY'
import base64
from pathlib import Path
workspace = Path(r'''$workspacePath''')
workspace.mkdir(parents=True, exist_ok=True)
(workspace / 'FEISHU_ROUTING_PROMPT.md').write_text(base64.b64decode('$routingB64').decode('utf-8'), encoding='utf-8')
print((workspace / 'FEISHU_ROUTING_PROMPT.md').as_posix())
PY" | Out-Null
}
else {
  Invoke-WslCommand "rm -f '$workspacePath/FEISHU_ROUTING_PROMPT.md'" -IgnoreError | Out-Null
}

Write-Host "[rollback] 重启 gateway 并校验..."
Invoke-WslCommand "systemctl restart $GatewayServiceName" -IgnoreError | Out-Null
$statusAfter = Invoke-WslCommand "openclaw status --deep" -IgnoreError
$serviceAfter = Invoke-WslCommand "systemctl status $GatewayServiceName --no-pager -n 0" -IgnoreError
$logsAfter = Invoke-WslCommand "journalctl -u $GatewayServiceName -n 20 --no-pager" -IgnoreError

Set-Content -Path (Join-Path $BackupDir "openclaw-status.rollback.txt") -Value $statusAfter -Encoding UTF8
Set-Content -Path (Join-Path $BackupDir "gateway-service.rollback.txt") -Value $serviceAfter -Encoding UTF8
Set-Content -Path (Join-Path $BackupDir "gateway-logs.rollback.txt") -Value $logsAfter -Encoding UTF8

Write-Host ""
Write-Host "=== Rollback Completed ==="
Write-Host "Backup directory: $BackupDir"
Write-Host "Status snapshot: $(Join-Path $BackupDir 'openclaw-status.rollback.txt')"
Write-Host "Service snapshot: $(Join-Path $BackupDir 'gateway-service.rollback.txt')"
Write-Host "Log snapshot: $(Join-Path $BackupDir 'gateway-logs.rollback.txt')"
