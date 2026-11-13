@echo off
rem Launcher for the kkal bot on Windows. Double-click to start.
rem Log file: data\bot.log
chcp 65001 >nul
cd /d "%~dp0"

set "PY=py"
where py >nul 2>nul || set "PY=python"

echo Starting kkal bot from "%CD%"
echo Press Ctrl+C to stop.
echo.

"%PY%" bot.py
set "CODE=%ERRORLEVEL%"

echo.
if not "%CODE%"=="0" (
  echo Bot exited with code %CODE%. See data\bot.log for details.
) else (
  echo Bot stopped.
)
pause
