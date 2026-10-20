@echo off
setlocal
chcp 65001 >nul
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\launch_windows.ps1" --diagnose
set "LRE_RESULT=%ERRORLEVEL%"
if not defined LRE_NO_PAUSE pause
exit /b %LRE_RESULT%
