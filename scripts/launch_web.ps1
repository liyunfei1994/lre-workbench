# Keep this source ASCII for Windows PowerShell 5.1.
param([ValidateSet('start','setup','stop')][string]$Mode = 'start')
$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$candidates = @()
if ($env:LRE_PYTHON) { $candidates += $env:LRE_PYTHON }
if ($env:LOCALAPPDATA) {
    $candidates += Join-Path $env:LOCALAPPDATA 'Programs\Python\Python314\python.exe'
}
if (Get-Command py -ErrorAction SilentlyContinue) {
    try {
        $pyPath = & py -3.14 -c 'import sys; print(sys.executable)' 2>$null
        if ($LASTEXITCODE -eq 0) { $candidates += $pyPath }
    } catch { } # A launcher without a registered Python must not block fallback.
}
if (Get-Command python -ErrorAction SilentlyContinue) {
    $candidates += (Get-Command python).Source
}
if ($env:ProgramFiles) { $candidates += Join-Path $env:ProgramFiles 'Python314\python.exe' }
if ($env:SystemDrive) { $candidates += Join-Path $env:SystemDrive 'Python314\python.exe' }
$candidates += @(
    (Join-Path $projectRoot '.venv-web\Scripts\python.exe'),
    (Join-Path $projectRoot '.venv\Scripts\python.exe')
)
foreach ($candidate in ($candidates | Select-Object -Unique)) {
    if (-not (Test-Path -LiteralPath $candidate)) { continue }
    try {
        $version = & $candidate -I -c "import sys,struct,sysconfig; print('LRE_OK' if sys.version_info[:2]==(3,14) and struct.calcsize('P')==8 and not sysconfig.get_config_var('Py_GIL_DISABLED') else 'LRE_UNSUPPORTED')" 2>$null
        $supported = $LASTEXITCODE -eq 0 -and $version -contains 'LRE_OK'
    } catch { $supported = $false }
    if ($supported) {
        & $candidate -m webapp.bootstrap $Mode
        exit $LASTEXITCODE
    }
}
throw 'Python 3.14 not found. Install Python 3.14, or set LRE_PYTHON to its python.exe.'
