@echo off
setlocal
chcp 65001 >nul
set PYTHONUTF8=1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\launch_windows.ps1" %*
if errorlevel 1 (
    echo LRE Workbench failed to start. See the error above.
    if not defined LRE_NO_PAUSE pause
    exit /b 1
)
exit /b 0
