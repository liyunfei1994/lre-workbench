@echo off
setlocal
chcp 65001 >nul
set "PYTHONUTF8=1"
cd /d "%~dp0"
if not exist ".venv-web\Scripts\python.exe" (
    echo Run setup_web_windows.bat first.
    pause
    exit /b 1
)
echo Independent browser pilot: initialize accounts locally on port 8766.
echo This command does not publish a public URL. See docs\WEB_BROWSER_PILOT.md.
".venv-web\Scripts\python.exe" -m webapp.run --port 8766 --database "user_data\browser-pilot\workspace.sqlite3" --private-workspace --open-browser
if errorlevel 1 (
    echo Pilot startup failed. See the error above.
    pause
    exit /b 1
)
