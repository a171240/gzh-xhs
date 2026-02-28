param(
  [string]$TaskName = "ContentHub-StartLocal",
  [switch]$RunNow
)

$ErrorActionPreference = "Stop"

$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).
  IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
  $args = @(
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-File", "`"$PSCommandPath`"",
    "-TaskName", "`"$TaskName`""
  )
  if ($RunNow) { $args += "-RunNow" }
  Start-Process -FilePath "powershell.exe" -ArgumentList $args -Verb RunAs | Out-Null
  Write-Host "[autostart] requesting admin permission..."
  exit 0
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$startScript = Join-Path $PSScriptRoot "start-local-hub.ps1"
if (-not (Test-Path $startScript)) {
  throw "start script not found: $startScript"
}

$arg = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$startScript`""
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $arg
$trigger = New-ScheduledTaskTrigger -AtLogOn
$principal = New-ScheduledTaskPrincipal -UserId $env:UserName -LogonType Interactive -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
Write-Host "[autostart] installed task: $TaskName"

if ($RunNow) {
  Start-ScheduledTask -TaskName $TaskName
  Write-Host "[autostart] started task immediately."
}
