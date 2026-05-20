# Arnold local launcher (PowerShell)
#
# Replaces arnold.bat's logic. PowerShell-only so we never go through cmd.exe's
# batch interpreter — that's where the "Terminate batch job (Y/N)?" prompt
# comes from when Stop-Process inside kill.ps1 fires a CTRL_BREAK on the
# console. PowerShell has no equivalent prompt.
#
# Steps:
#   1. Acquire single-instance lock on arnold/data/.launch.lock
#   2. Run kill.ps1 to clear any zombie launcher / tunnel / browser
#   3. Launch python launch.py from the project venv (or system python)
#   4. Release lock on exit (always — try/finally)

$ErrorActionPreference = 'Stop'

$repoRoot = $PSScriptRoot
$dataDir = Join-Path $repoRoot 'arnold\data'
$lockFile = Join-Path $dataDir '.launch.lock'

if (-not (Test-Path $dataDir)) {
    New-Item -ItemType Directory -Path $dataDir -Force | Out-Null
}

# Exclusive file lock — opens with FileShare.None so a second arnold.ps1 fails
# fast instead of racing into kill+launch.
$lockStream = $null
try {
    $lockStream = [System.IO.File]::Open($lockFile, 'OpenOrCreate', 'Write', 'None')
} catch {
    Write-Host "[arnold] another instance is already running (lock held: $lockFile)"
    Write-Host '[arnold] close it first, or run .\kill.ps1 to clear stale state'
    Read-Host 'Press Enter to exit'
    exit 1
}

try {
    & (Join-Path $repoRoot 'kill.ps1')
    if ($LASTEXITCODE -ne 0) {
        Write-Host '[arnold] kill.ps1 reported leftover state — aborting launch'
        Read-Host 'Press Enter to exit'
        exit 1
    }

    Set-Location (Join-Path $repoRoot 'arnold')

    $venvPython = Join-Path $repoRoot '.venv\Scripts\python.exe'
    if (Test-Path $venvPython) {
        & $venvPython launch.py
    } else {
        & python launch.py
    }
} finally {
    if ($lockStream) {
        $lockStream.Close()
        $lockStream.Dispose()
    }
}
