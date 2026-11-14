@echo off
rem kkal bot launcher. ASCII only: cmd.exe reads .bat in the OEM code page.
rem Logs: data\bot.log (the bot itself), data\start.log (this launcher).
chcp 65001 >nul
cd /d "%~dp0"
set "PY=py"
where py >nul 2>nul || set "PY=python"
if not exist "data" mkdir "data"
>>"data\start.log" echo [%DATE% %TIME%] start-bot.bat in "%CD%" python=%PY%
echo Starting kkal bot from "%CD%"
echo Press Ctrl+C to stop. Keep this window open while the bot runs.
echo.
"%PY%" bot.py
set "CODE=%ERRORLEVEL%"
>>"data\start.log" echo [%DATE% %TIME%] bot.py exited with code %CODE%
if not "%CODE%"=="0" goto failed
echo.
echo Bot stopped. Data in data\kkal.db is saved.
goto done
:failed
echo.
echo Bot exited with code %CODE%. See data\bot.log for details.
:done
pause
