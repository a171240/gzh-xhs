param(
  [string]$WslDistro = "Ubuntu-24.04",
  [int]$GatewayPort = 18789
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path

try {
  $listeners = Get-NetTCPConnection -State Listen -LocalPort 8790 -ErrorAction SilentlyContinue
  if ($listeners) {
    $pids = $listeners | Select-Object -ExpandProperty OwningProcess -Unique
    foreach ($procId in $pids) {
      Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
    }
  }
  $targets = Get-CimInstance Win32_Process | Where-Object {
    $_.CommandLine -and (
      $_.CommandLine -like "*ingest_writer_api:app*" -or
      $_.CommandLine -like "*run-ingest-writer-api.ps1*"
    )
  }
  foreach ($proc in $targets) {
    Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
  }
  Write-Host "[hub] Writer API background processes stopped."
}
catch {
  Write-Warning "[hub] failed stopping writer processes: $($_.Exception.Message)"
}

wsl -d $WslDistro -- bash -lc "tmux kill-session -t openclaw-gateway 2>/dev/null || true" | Out-Null
try {
  $gatewayListeners = Get-NetTCPConnection -State Listen -LocalPort $GatewayPort -ErrorAction SilentlyContinue
  if ($gatewayListeners) {
    $gatewayPids = $gatewayListeners | Select-Object -ExpandProperty OwningProcess -Unique
    $killErrors = @()
    foreach ($procId in $gatewayPids) {
      try {
        Stop-Process -Id $procId -Force -ErrorAction Stop
      }
      catch {
        $killErrors += "pid=$procId : $($_.Exception.Message)"
      }
    }
    $remaining = Get-NetTCPConnection -State Listen -LocalPort $GatewayPort -ErrorAction SilentlyContinue
    if ($remaining) {
      Write-Warning "[hub] gateway listener still active on port $GatewayPort (likely managed by external service)."
      if ($killErrors.Count -gt 0) {
        foreach ($err in $killErrors) {
          Write-Warning "[hub] $err"
        }
      }
    }
    else {
      Write-Host "[hub] OpenClaw gateway listeners stopped on port $GatewayPort."
    }
  }
  else {
    Write-Host "[hub] no OpenClaw gateway listener found on port $GatewayPort."
  }
}
catch {
  Write-Warning "[hub] failed stopping gateway listeners: $($_.Exception.Message)"
}

Write-Host "[hub] stop completed."
