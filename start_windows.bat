@echo off
setlocal
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" scripts\bootstrap.py %*
    goto finish
)
where py >nul 2>nul
if not errorlevel 1 (
    py -3.11 -c "import sys; assert sys.version_info[:2] == (3,11)" >nul 2>nul
    if not errorlevel 1 (
        py -3.11 scripts\bootstrap.py %*
        goto finish
    )
)
where python >nul 2>nul
if not errorlevel 1 (
    python scripts\bootstrap.py %*
    goto finish
)
echo Python 3.10-3.12 is required. Install Python and enable Add Python to PATH.
pause
exit /b 1
:finish
if errorlevel 1 (
    echo FFSC failed to start. See the error above.
    pause
    exit /b 1
)
exit /b 0
