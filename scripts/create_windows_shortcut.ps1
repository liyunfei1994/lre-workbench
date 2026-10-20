$ErrorActionPreference = 'Stop'
$taskRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$taskShell = New-Object -ComObject WScript.Shell
$taskVersion = & (Join-Path $taskRoot '.venv\Scripts\python.exe') -c "import sys; sys.path.insert(0, sys.argv[1]); from version import APP_VERSION_LABEL; print(APP_VERSION_LABEL)" (Join-Path $taskRoot 'src')
if ($LASTEXITCODE -ne 0) { throw 'Could not read application version.' }
foreach ($taskDirectory in @($taskRoot)) {
    $taskLinkPath = Join-Path $taskDirectory ("火箭动力工作台 $taskVersion.lnk")
    $taskShortcut = $taskShell.CreateShortcut($taskLinkPath)
    $taskShortcut.TargetPath = Join-Path $env:SystemRoot 'System32\wscript.exe'
    $taskShortcut.Arguments = '"' + (Join-Path $taskRoot 'start_windows.vbs') + '"'
    $taskShortcut.WorkingDirectory = $taskRoot
    $taskShortcut.IconLocation = (Join-Path $taskRoot 'assets\app.ico') + ',0'
    $taskShortcut.Description = "液体火箭发动机数字样机设计与分析平台 · LRE Workbench $taskVersion"
    $taskShortcut.Save()
    & (Join-Path $taskRoot '.venv\Scripts\python.exe') (Join-Path $PSScriptRoot 'set_windows_shortcut_identity.py') $taskLinkPath
    if ($LASTEXITCODE -ne 0) { throw 'Taskbar identity registration failed.' }
    Write-Output "Created: $taskLinkPath"
}
