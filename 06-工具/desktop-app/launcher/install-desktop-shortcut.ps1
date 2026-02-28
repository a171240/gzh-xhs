Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$appDir = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$startCmd = (Resolve-Path (Join-Path $PSScriptRoot 'start-desktop-app.cmd')).Path
$preferredDesktop = 'D:\桌面'
$fallbackDesktop = [Environment]::GetFolderPath('Desktop')
$desktopDir = if (Test-Path -LiteralPath $preferredDesktop) { $preferredDesktop } else { $fallbackDesktop }
$shortcutPath = Join-Path $desktopDir '内容生产桌面版.lnk'
$iconPath = Join-Path $appDir 'node_modules\electron\dist\electron.exe'

$wsh = New-Object -ComObject WScript.Shell
$shortcut = $wsh.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $startCmd
$shortcut.WorkingDirectory = $appDir
$shortcut.WindowStyle = 1
$shortcut.Description = '内容生产桌面版（Codex）'
if (Test-Path -LiteralPath $iconPath) {
  $shortcut.IconLocation = "$iconPath,0"
}
$shortcut.Save()

Write-Host "已创建/更新桌面快捷方式：$shortcutPath"
Write-Host "启动入口：$startCmd"


