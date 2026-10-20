$ErrorActionPreference = 'Stop'
$taskRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$env:PYTHONUTF8 = '1'
& (Join-Path $taskRoot '.venv\Scripts\python.exe') (Join-Path $PSScriptRoot 'create_windows_shortcut.py')
if ($LASTEXITCODE -ne 0) { throw 'Shortcut creation failed.' }
