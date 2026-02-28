param(
  [string]$WslDistro = "Ubuntu-24.04",
  [string]$WriterHealthUrl = "http://127.0.0.1:8790/internal/healthz",
  [string]$PythonExe = "python",
  [switch]$NoDryRun
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
$scriptDir = Join-Path $toolRoot "scripts"
$orchestrator = Join-Path $scriptDir "feishu_kb_orchestrator.py"
$reportDir = Join-Path $toolRoot "deploy\backups\feishu-cutover"
$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$reportPath = Join-Path $reportDir "verify-$timestamp.json"

if (-not (Test-Path $orchestrator)) {
  throw "Orchestrator script not found: $orchestrator"
}

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
    $v = $pair[1].Trim().Trim([char]34).Trim([char]39)
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

function Run-OrchestratorCase {
  param(
    [string]$CaseId,
    [string]$Text,
    [bool]$EnableSkill,
    [bool]$EnableLink,
    [bool]$UseDryRun
  )

  $sourceTime = (Get-Date).ToUniversalTime().ToString("o")
  $eventRef = "verify-$CaseId-$([Guid]::NewGuid().ToString('N'))"
  $args = @(
    $orchestrator,
    "--text", $Text,
    "--event-ref", $eventRef,
    "--source-ref", "verify-feishu-cutover",
    "--source-time", $sourceTime
  )
  if ($UseDryRun) {
    $args += "--dry-run"
  }

  $raw = & $PythonExe @args 2>&1
  if ($LASTEXITCODE -ne 0) {
    throw "Case $CaseId failed to execute: $raw"
  }

  $json = $null
  try {
    $json = ($raw | Out-String | ConvertFrom-Json -Depth 30)
  }
  catch {
    throw "Case $CaseId returned non-JSON output: $raw"
  }

  if ($json.status -notin @("success", "partial")) {
    throw "Case $CaseId status is not success/partial: $($json.status)"
  }
  if (-not $EnableSkill -and @($json.reply_segments).Count -ne 1) {
    throw "Case $CaseId reply_segments should be 1 for ingest-only flow."
  }
  if ($EnableSkill -and -not $json.intent.skill) {
    throw "Case $CaseId should trigger skill, but intent.skill=false"
  }
  if ($EnableLink -and @($json.intent.urls).Count -lt 1) {
    throw "Case $CaseId should contain at least one URL"
  }

  return [ordered]@{
    case = $CaseId
    dry_run = $UseDryRun
    status = [string]$json.status
    event_ref = [string]$json.event_ref
    reply = [string]$json.reply
    reply_segments = @($json.reply_segments)
    intent = $json.intent
    run_log = [string]$json.run_log
  }
}

Write-Host "[verify] Step 1/4 Validate runtime settings..."
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

Write-Host "[verify] Step 2/4 Validate gateway and Feishu channel..."
$gatewayStatus = Invoke-WslCommand "openclaw status --deep" -IgnoreError
if ($gatewayStatus -notmatch "Gateway\s+\|\s+reachable") {
  throw "Gateway is not reachable."
}
if ($gatewayStatus -notmatch "Feishu\s+\|\s+ON\s+\|\s+OK") {
  throw "Feishu channel is not ON/OK."
}

Write-Host "[verify] Step 3/4 Validate writer health..."
$writerHealth = $null
try {
  $writerHealth = Invoke-RestMethod -Uri $WriterHealthUrl -Method Get -TimeoutSec 8
}
catch {
  throw "Writer health check failed: $($_.Exception.Message)"
}

$dryRun = -not $NoDryRun
Write-Host "[verify] Step 4/4 Run orchestrator validation cases..."
$cases = @()
$cases += Run-OrchestratorCase -CaseId "A-quote" -Text "Quote check: done is better than perfect." -EnableSkill:$false -EnableLink:$false -UseDryRun:$dryRun
$cases += Run-OrchestratorCase -CaseId "B-link" -Text "https://example.com/a" -EnableSkill:$false -EnableLink:$true -UseDryRun:$dryRun
$cases += Run-OrchestratorCase -CaseId "C-weak-skill" -Text "Use wechat-batch to generate a conversion review article." -EnableSkill:$true -EnableLink:$false -UseDryRun:$dryRun
$cases += Run-OrchestratorCase -CaseId "D-strong-skill" -Text "/skill wechat platform=wechat brief=Write a conversion review article." -EnableSkill:$true -EnableLink:$false -UseDryRun:$dryRun
$cases += Run-OrchestratorCase -CaseId "E-mixed" -Text "Use xhs-dual to generate an emotional conflict note and reference https://example.com/b" -EnableSkill:$true -EnableLink:$true -UseDryRun:$dryRun

New-Item -ItemType Directory -Force -Path $reportDir | Out-Null
$report = [ordered]@{
  timestamp = $timestamp
  dry_run = $dryRun
  writer_health_url = $WriterHealthUrl
  writer_health = $writerHealth
  required_settings = $resolved
  gateway_status_excerpt = ($gatewayStatus -split "`n" | Select-Object -First 80)
  cases = $cases
}
$report | ConvertTo-Json -Depth 30 | Set-Content -Path $reportPath -Encoding UTF8

$finalMessages = @(
  "",
  "=== Verify Passed ===",
  "Report: $reportPath",
  "Dry-run: $dryRun",
  "",
  "Send these 5 Feishu acceptance messages:",
  "A) quote: done is better than perfect.",
  "B) link: https://example.com/a",
  "C) weak skill: Use wechat-batch to generate a conversion review article.",
  "D) strong skill: /skill wechat platform=wechat brief=Write a conversion review article.",
  "E) mixed: Use xhs-dual to generate a note and reference https://example.com/b"
)
foreach ($msg in $finalMessages) {
  Write-Host $msg
}
