@echo off
setlocal
call "%~dp0start_windows.bat" --setup %*
if errorlevel 1 exit /b 1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\create_windows_shortcut.ps1"
if errorlevel 1 (
    echo Desktop shortcut creation failed. You can still use start_windows.bat.
    if not defined LRE_NO_PAUSE pause
    exit /b 1
)
echo Setup complete. Double-click start_windows.bat or the icon in this folder.
if not defined LRE_NO_PAUSE pause
