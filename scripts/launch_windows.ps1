# ASCII source is intentional for Windows PowerShell 5.1 compatibility.
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$LaunchArguments
)
$ErrorActionPreference = 'Stop'
$taskRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'

function Test-ProjectPython([string]$Executable, [string[]]$PrefixArguments = @()) {
    if (-not $Executable) { return $false }
    try {
        $taskExePath = $Executable
        if (-not [IO.File]::Exists($taskExePath)) {
            $taskResolvedCommand = Get-Command $Executable -ErrorAction SilentlyContinue
            if (-not $taskResolvedCommand) { return $false }
            $taskExePath = $taskResolvedCommand.Source
        }
        $taskExeStream = [IO.File]::OpenRead($taskExePath)
        try {
            if ($taskExeStream.ReadByte() -ne 77 -or $taskExeStream.ReadByte() -ne 90) { return $false }
        } finally { $taskExeStream.Dispose() }
        $taskProbe = & $Executable @PrefixArguments -I -c "import sys,struct; print('LRE_OK' if (3,11) <= sys.version_info[:2] <= (3,12) and struct.calcsize('P')==8 else 'LRE_UNSUPPORTED')" 2>$null
        return ($LASTEXITCODE -eq 0 -and $taskProbe -contains 'LRE_OK')
    } catch { return $false }
}

function Get-ManagedPython {
    $taskToolDir = Join-Path $taskRoot '.tools'
    $taskUv = Join-Path $taskToolDir 'uv.exe'
    if (-not (Test-Path -LiteralPath $taskUv)) {
        # Fixed PyPI wheel and digest; no downloaded shell script is evaluated.
        $taskUrl = 'https://files.pythonhosted.org/packages/8b/f0/bf4274a0d2121d013a01e4e1c2550a0d307055d9524bead3c51bf424569d/uv-0.13.3-py3-none-win_amd64.whl'
        $taskHash = '62d1d071693d0e2c92ba86266e2c83d885b8d1930e368b6b9704a443e55a190d'
        New-Item -ItemType Directory -Path $taskToolDir -Force | Out-Null
        $taskWheel = Join-Path $taskToolDir ('uv-' + [guid]::NewGuid().ToString('N') + '.zip')
        Write-Host 'Downloading the project Python manager (uv 0.13.3)...'
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        try {
            Invoke-WebRequest -UseBasicParsing -Uri $taskUrl -OutFile $taskWheel -TimeoutSec 180
            $taskHasher = [Security.Cryptography.SHA256]::Create()
            $taskInput = [IO.File]::OpenRead($taskWheel)
            try { $taskActualHash = ([BitConverter]::ToString($taskHasher.ComputeHash($taskInput))).Replace('-', '').ToLowerInvariant() }
            finally { $taskInput.Dispose(); $taskHasher.Dispose() }
            if ($taskActualHash -ne $taskHash) {
                throw 'Python manager checksum mismatch. Download rejected.'
            }
            Add-Type -AssemblyName System.IO.Compression.FileSystem
            $taskArchive = [IO.Compression.ZipFile]::OpenRead($taskWheel)
            try {
                $taskEntry = $taskArchive.Entries | Where-Object FullName -eq 'uv-0.13.3.data/scripts/uv.exe'
                if (-not $taskEntry) { throw 'Python manager executable missing in wheel.' }
                [IO.Compression.ZipFileExtensions]::ExtractToFile($taskEntry, $taskUv, $true)
            } finally { $taskArchive.Dispose() }
        } finally {
            if (Test-Path -LiteralPath $taskWheel) { Remove-Item -LiteralPath $taskWheel }
        }
    }
    $env:UV_PYTHON_INSTALL_DIR = Join-Path $taskRoot '.runtime'
    $env:UV_CACHE_DIR = Join-Path $taskRoot '.cache\uv'
    $env:UV_PYTHON_INSTALL_REGISTRY = '0'
    $env:UV_PYTHON_INSTALL_BIN = '0'
    Write-Host 'Installing a private Python 3.11 runtime for this project...'
    & $taskUv python install 3.11 --no-config --no-bin
    if ($LASTEXITCODE -ne 0) { throw 'Python download failed. Check GitHub access, or install Python 3.11/3.12 (64-bit) and retry.' }
    $taskFound = & $taskUv python find 3.11 --managed-python --no-config
    if ($LASTEXITCODE -ne 0 -or -not $taskFound) { throw 'Cannot locate the managed Python runtime.' }
    return ([string]($taskFound | Select-Object -Last 1)).Trim()
}

try {
    if (-not [Environment]::Is64BitOperatingSystem -or $env:PROCESSOR_ARCHITECTURE -eq 'ARM64') {
        throw 'This installer requires Windows x64.'
    }
    Set-Location -LiteralPath $taskRoot
    $taskPython = $null
    $taskPrefix = @()
    if ($env:LRE_PYTHON) {
        if (-not (Test-ProjectPython $env:LRE_PYTHON)) {
            throw 'LRE_PYTHON must point to a working Python 3.11/3.12 (64-bit).'
        }
        $taskPython = $env:LRE_PYTHON
    }
    $taskCandidates = @((Join-Path $taskRoot '.venv\Scripts\python.exe'))
    $taskRuntime = Join-Path $taskRoot '.runtime'
    if (Test-Path -LiteralPath $taskRuntime) {
        $taskCandidates += @(Get-ChildItem -LiteralPath $taskRuntime -Filter python.exe -File -Recurse | Select-Object -ExpandProperty FullName)
    }
    foreach ($taskCandidate in $taskCandidates) {
        if (-not $taskPython -and (Test-ProjectPython $taskCandidate)) { $taskPython = $taskCandidate }
    }
    if (-not $taskPython -and (Get-Command py.exe -ErrorAction SilentlyContinue)) {
        foreach ($taskVersion in @('-3.11', '-3.12')) {
            if (Test-ProjectPython 'py.exe' @($taskVersion)) {
                $taskPython = 'py.exe'
                $taskPrefix = @($taskVersion)
                break
            }
        }
    }
    if (-not $taskPython) {
        $taskCommand = Get-Command python.exe -ErrorAction SilentlyContinue
        if ($taskCommand -and (Test-ProjectPython $taskCommand.Source)) { $taskPython = $taskCommand.Source }
    }
    if (-not $taskPython) { $taskPython = Get-ManagedPython }
    if (-not (Test-ProjectPython $taskPython $taskPrefix)) { throw 'No usable Python interpreter found.' }
    & $taskPython @taskPrefix (Join-Path $PSScriptRoot 'bootstrap.py') @LaunchArguments
    exit $LASTEXITCODE
} catch {
    Write-Host ("Startup failed: " + $_.Exception.Message) -ForegroundColor Red
    Write-Host 'Retry setup_windows.bat. See README.md for offline/manual setup.'
    exit 1
}
