@echo off
setlocal
cd /d "%~dp0.."
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\start_dashboard.ps1"
if errorlevel 1 (
  echo.
  echo Dashboard launcher failed. Review the PowerShell error above.
  pause
)
endlocal
