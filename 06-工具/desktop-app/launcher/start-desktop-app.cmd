@echo off
setlocal

pushd "%~dp0.." >nul 2>&1
if errorlevel 1 (
  echo.
  echo Failed to enter desktop-app directory.
  echo Current script dir: %~dp0
  pause
  exit /b 1
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\launcher\run-desktop-app.ps1" %*
set "EXIT_CODE=%ERRORLEVEL%"

popd

if not "%EXIT_CODE%"=="0" (
  echo.
  echo Startup failed, exit code: %EXIT_CODE%
  echo See launcher\README.md for troubleshooting.
  pause
)

exit /b %EXIT_CODE%
