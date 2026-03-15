param(
  [switch]$CheckOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$appDir = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$defaultCodexModel = 'gpt-5.4'
$defaultReasoningEffort = 'xhigh'

function Get-RequiredCommandPath {
  param(
    [Parameter(Mandatory = $true)][string]$Name
  )

  $cmd = Get-Command $Name -ErrorAction SilentlyContinue
  if (-not $cmd) {
    throw "未找到命令：$Name。请先安装并加入 PATH。"
  }
  return $cmd.Source
}

function Get-CodexCliCandidates {
  $userProfile = [Environment]::GetFolderPath('UserProfile')
  $roots = @(
    (Join-Path $userProfile '.vscode\extensions'),
    (Join-Path $userProfile '.vscode-insiders\extensions')
  )

  $candidates = @()
  foreach ($root in $roots) {
    if (-not (Test-Path -LiteralPath $root)) {
      continue
    }

    $extDirs = Get-ChildItem -LiteralPath $root -Directory -Filter 'openai.chatgpt-*' -ErrorAction SilentlyContinue
    foreach ($extDir in $extDirs) {
      $exePath = Join-Path $extDir.FullName 'bin\windows-x86_64\codex.exe'
      if (Test-Path -LiteralPath $exePath) {
        $file = Get-Item -LiteralPath $exePath
        $candidates += [pscustomobject]@{
          Path = $exePath
          LastWriteTime = $file.LastWriteTime
        }
      }
    }
  }

  return @($candidates | Sort-Object LastWriteTime -Descending)
}

function Test-CodexLaunchPathUsable {
  param(
    [string]$Value
  )

  if ([string]::IsNullOrWhiteSpace($Value)) {
    return $false
  }

  $trimmed = $Value.Trim()
  if ([System.IO.Path]::IsPathRooted($trimmed)) {
    return (Test-Path -LiteralPath $trimmed)
  }

  $cmd = Get-Command $trimmed -ErrorAction SilentlyContinue
  return [bool]$cmd
}

function Resolve-CodexLaunchPath {
  $candidates = Get-CodexCliCandidates
  if ($candidates.Count -gt 0) {
    return $candidates[0].Path
  }

  $cmd = Get-Command codex -ErrorAction SilentlyContinue
  if ($cmd) {
    return 'codex'
  }

  throw "未找到 Codex CLI。请确认 VSCode ChatGPT 扩展已安装，或把 codex 加入 PATH。已搜索：$([Environment]::GetFolderPath('UserProfile'))\\.vscode\\extensions\\openai.chatgpt-*"
}

function Ensure-NodeModules {
  param(
    [Parameter(Mandatory = $true)][string]$NpmPath,
    [Parameter(Mandatory = $true)][string]$WorkingDirectory
  )

  $modulesDir = Join-Path $WorkingDirectory 'node_modules'
  if (Test-Path -LiteralPath $modulesDir) {
    Write-Host "依赖已存在：$modulesDir"
    return
  }

  Write-Host '未检测到 node_modules，正在执行 npm install...'
  Push-Location $WorkingDirectory
  try {
    & $NpmPath 'install'
  }
  finally {
    Pop-Location
  }
}

function Get-ElectronSettingsPath {
  $appData = [Environment]::GetFolderPath('ApplicationData')
  $userDataDir = Join-Path $appData 'content-codex-desktop'
  if (-not (Test-Path -LiteralPath $userDataDir)) {
    New-Item -ItemType Directory -Path $userDataDir -Force | Out-Null
  }
  return Join-Path $userDataDir 'settings.json'
}

function Read-JsonObject {
  param(
    [Parameter(Mandatory = $true)][string]$Path
  )

  if (-not (Test-Path -LiteralPath $Path)) {
    return [pscustomobject]@{}
  }

  $raw = [System.IO.File]::ReadAllText($Path, [System.Text.Encoding]::UTF8)
  if ([string]::IsNullOrWhiteSpace($raw)) {
    return [pscustomobject]@{}
  }

  $obj = $raw | ConvertFrom-Json -ErrorAction Stop
  if (-not $obj) {
    return [pscustomobject]@{}
  }
  return $obj
}

function Save-JsonObject {
  param(
    [Parameter(Mandatory = $true)][string]$Path,
    [Parameter(Mandatory = $true)][psobject]$Object
  )

  $json = $Object | ConvertTo-Json -Depth 100
  $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
  [System.IO.File]::WriteAllText($Path, $json, $utf8NoBom)
}

function Merge-DefaultSettings {
  param(
    [Parameter(Mandatory = $true)][psobject]$Settings,
    [Parameter(Mandatory = $true)][string]$CodexLaunchPath
  )

  $dict = @{}
  $skipNames = @('IsFixedSize', 'IsReadOnly', 'IsSynchronized', 'Keys', 'Values', 'SyncRoot', 'Count')

  if ($Settings -is [System.Collections.IDictionary]) {
    foreach ($key in $Settings.Keys) {
      if ($skipNames -contains [string]$key) { continue }
      $dict[[string]$key] = $Settings[$key]
    }
  }
  else {
    foreach ($p in $Settings.PSObject.Properties) {
      if ($skipNames -contains $p.Name) { continue }
      $dict[$p.Name] = $p.Value
    }
  }

  if (-not $dict.ContainsKey('engine') -or [string]::IsNullOrWhiteSpace([string]$dict.engine)) {
    $dict.engine = 'codex'
  }

  $currentCodexPath = if ($dict.ContainsKey('codexPath')) { [string]$dict.codexPath } else { '' }
  if ([string]::IsNullOrWhiteSpace($currentCodexPath) -or -not (Test-CodexLaunchPathUsable -Value $currentCodexPath)) {
    $dict.codexPath = $CodexLaunchPath
  }

  # 用户已明确要求固定默认模型与推理强度
  $dict.defaultModel = $defaultCodexModel
  $dict.modelReasoningEffort = $defaultReasoningEffort

  return [pscustomobject]$dict
}

Write-Host "应用目录：$appDir"

$nodePath = Get-RequiredCommandPath -Name 'node'
$npmPath = Get-RequiredCommandPath -Name 'npm'
$codexLaunchPath = Resolve-CodexLaunchPath

Write-Host "node：$nodePath"
Write-Host "npm：$npmPath"
Write-Host "codex：$codexLaunchPath"

Ensure-NodeModules -NpmPath $npmPath -WorkingDirectory $appDir

$settingsFile = Get-ElectronSettingsPath
$existingSettings = Read-JsonObject -Path $settingsFile
$mergedSettings = Merge-DefaultSettings -Settings $existingSettings -CodexLaunchPath $codexLaunchPath
Save-JsonObject -Path $settingsFile -Object $mergedSettings

Write-Host "已写入默认设置：$settingsFile"

try {
  if ($codexLaunchPath -eq 'codex') {
    & codex login status | Out-Null
  }
  else {
    & $codexLaunchPath login status | Out-Null
  }
  Write-Host 'Codex 登录状态：可用'
}
catch {
  Write-Warning 'Codex 登录状态检测失败，应用仍可启动。请在终端执行 codex login status 检查。'
}

if ($CheckOnly) {
  Write-Host 'CheckOnly 模式：不启动 Electron。'
  exit 0
}

# Some terminals set ELECTRON_RUN_AS_NODE=1 globally, which breaks Electron app startup.
if ($env:ELECTRON_RUN_AS_NODE) {
  Write-Host "检测到 ELECTRON_RUN_AS_NODE=$($env:ELECTRON_RUN_AS_NODE)，启动前将其清除。"
  Remove-Item Env:ELECTRON_RUN_AS_NODE -ErrorAction SilentlyContinue
}

Push-Location $appDir
try {
  & $npmPath 'start'
}
finally {
  Pop-Location
}


