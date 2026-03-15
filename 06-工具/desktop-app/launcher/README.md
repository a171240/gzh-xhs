# Desktop Launcher (Windows)

用于把 `desktop-app` 作为桌面应用快速启动，并默认接入 Codex。

## 一次性安装桌面快捷方式

在 PowerShell 执行：

```powershell
Set-Location "E:\公众号内容生成\desktop-app\launcher"
powershell -NoProfile -ExecutionPolicy Bypass -File .\install-desktop-shortcut.ps1
```

安装后桌面会出现 `内容生产桌面版.lnk`。

## 启动应用

- 直接双击桌面快捷方式。
- 启动入口文件：
  - `start-desktop-app.cmd`
  - `run-desktop-app.ps1`

## 启动脚本会做什么

- 检查 `node`、`npm`、`codex` 是否可用
- 检查 `desktop-app/node_modules`，缺失时自动执行 `npm install`
- 自动写入/补齐 Electron `settings.json`：
  - `engine = codex`
  - `codexPath = 自动扫描最新 VSCode ChatGPT 扩展中的 codex.exe`
  - `defaultModel = gpt-5.4`
  - `modelReasoningEffort = xhigh`（超高）
  - 若未扫描到，自动回退到 `codex`（PATH）

## 仅检查环境（不启动 GUI）

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\run-desktop-app.ps1 -CheckOnly
```

## 故障排查

1. 提示未找到 `codex`
- 先执行：`codex --version`
- 再执行：`codex login status`
- 若扩展刚升级，重新双击一次桌面图标即可自动刷新路径

2. 提示未找到 `node` 或 `npm`
- 确认 `node -v`、`npm -v` 在终端可执行
- 如不可用，重装 Node.js 并加入 PATH

3. 启动时报依赖缺失
- 在 `desktop-app` 目录手动执行：`npm install`
- 然后重新双击快捷方式
