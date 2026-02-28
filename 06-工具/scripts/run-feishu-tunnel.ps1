param(
  [int]$LocalPort = 8787,
  [string]$Hostname = "",
  [switch]$CheckOnly
)

$ErrorActionPreference = "Stop"

$cloudflaredCmd = Get-Command cloudflared -ErrorAction SilentlyContinue
$cloudflaredPath = if ($cloudflaredCmd) { $cloudflaredCmd.Source } else { "" }

if (-not $cloudflaredPath) {
  $wingetPath = Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Packages\Cloudflare.cloudflared_Microsoft.Winget.Source_8wekyb3d8bbwe\cloudflared.exe"
  if (Test-Path $wingetPath) {
    $cloudflaredPath = $wingetPath
  }
}

if (-not $cloudflaredPath) {
  throw "cloudflared not found. Install Cloudflare Tunnel client or reopen terminal to refresh PATH."
}

$localUrl = "http://127.0.0.1:$LocalPort"
Write-Host "[feishu-tunnel] local: $localUrl"
Write-Host "[feishu-tunnel] binary: $cloudflaredPath"

if ($Hostname) {
  Write-Host "[feishu-tunnel] hostname: $Hostname"
  Write-Host "[feishu-tunnel] callback: https://$Hostname/api/feishu/events"
}
else {
  Write-Host "[feishu-tunnel] callback: <cloudflared-output-url>/api/feishu/events"
}

if ($CheckOnly) {
  Write-Host "[feishu-tunnel] check-only mode, exit."
  exit 0
}

if ($Hostname) {
  & $cloudflaredPath tunnel --url $localUrl --hostname $Hostname
}
else {
  & $cloudflaredPath tunnel --url $localUrl
}
