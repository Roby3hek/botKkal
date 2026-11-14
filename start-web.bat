@echo off
rem Launcher for the kkal mini-app web server on Windows. Double-click to start.
rem Log file: data\web.log
chcp 65001 >nul
cd /d "%~dp0"

set "PY=py"
where py >nul 2>nul || set "PY=python"

if not exist "data" mkdir "data"

echo Starting kkal web server from "%CD%"
echo Local address: http://127.0.0.1:8080
echo For Telegram you need an https tunnel, see docs\windows.md, section 9.
echo Press Ctrl+C to stop. Keep this window open while the server runs.
echo.

>>"data\web.log" echo [%DATE% %TIME%] start-web.bat in "%CD%" python=%PY%
"%PY%" server.py --port 8080 2>>"data\web.err.log"
set "CODE=%ERRORLEVEL%"
>>"data\web.log" echo [%DATE% %TIME%] server.py exited with code %CODE%

if not "%CODE%"=="0" goto failed
goto done

:failed
echo.
echo Server exited with code %CODE%. Look at data\web.log
echo If it says the port is busy, close the other window or use another --port.

:done
echo.
echo Server stopped. The diary in data\kkal.db is saved.
pause
