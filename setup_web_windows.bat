@echo off
setlocal
chcp 65001 >nul
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\launch_web.ps1" -Mode setup
if errorlevel 1 (
    echo Web setup failed. See the error above.
    if not defined LRE_NO_PAUSE pause
    exit /b 1
)
if not defined LRE_NO_PAUSE pause
