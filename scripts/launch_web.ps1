param([ValidateSet('start','setup','stop')][string]$Mode = 'start')
$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$candidates = @(
    (Join-Path $projectRoot '.venv-web\Scripts\python.exe'),
    (Join-Path $projectRoot '.venv\Scripts\python.exe')
)
if ($env:LRE_PYTHON) { $candidates = @($env:LRE_PYTHON) + $candidates }
if (Get-Command py -ErrorAction SilentlyContinue) {
    $pyPath = & py -3.14 -c 'import sys; print(sys.executable)' 2>$null
    if ($LASTEXITCODE -eq 0) { $candidates += $pyPath }
}
if (Get-Command python -ErrorAction SilentlyContinue) {
    $candidates += (Get-Command python).Source
}
foreach ($candidate in $candidates) {
    if (-not (Test-Path -LiteralPath $candidate)) { continue }
    $version = & $candidate -c 'import sys; print(chr(46).join(map(str,sys.version_info[:2])))' 2>$null
    if ($LASTEXITCODE -eq 0 -and $version -eq '3.14') {
        & $candidate -m webapp.bootstrap $Mode
        exit $LASTEXITCODE
    }
}
throw 'Python 3.14 not found. Install Python 3.14, or set LRE_PYTHON to its python.exe.'
