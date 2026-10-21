@echo off
setlocal
chcp 65001 >nul
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\launch_web.ps1" -Mode stop
if errorlevel 1 (
    if not defined LRE_NO_PAUSE pause
    exit /b 1
)
