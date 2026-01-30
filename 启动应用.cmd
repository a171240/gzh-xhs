@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0desktop-app"

where npm >nul 2>&1
if errorlevel 1 (
  echo 未检测到 npm，请先安装 Node.js。
  pause
  exit /b 1
)

if not exist node_modules (
  echo 正在安装依赖...
  npm install
)

echo 正在启动内容生成控制台...
start "内容生成控制台" cmd /c "npm run dev"
endlocal
