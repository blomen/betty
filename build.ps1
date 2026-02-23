# BankrollBBQ Windows Build Script
# Builds the React frontend and packages everything into BankrollBBQ.exe
#
# Usage:  .\build.ps1           Build full .exe
#         .\build.ps1 -Dev      Frontend-only rebuild (for launcher.py dev mode)
# Output: dist\BankrollBBQ.exe

param(
    [switch]$Dev
)

$ErrorActionPreference = "Stop"

# Resolve venv Python (prefer .venv, fall back to system)
$VenvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    $VenvPython = (Get-Command python -ErrorAction SilentlyContinue).Source
    if (-not $VenvPython) {
        Write-Host "ERROR: Python not found. Install Python 3.10+ or create a .venv." -ForegroundColor Red
        exit 1
    }
}
$VenvPip = "$VenvPython -m pip"

# Step counts differ for dev vs full build
if ($Dev) { $TotalSteps = 2 } else { $TotalSteps = 4 }
$Step = 0

function Write-Step($msg) {
    $script:Step++
    Write-Host "`n[$script:Step/$script:TotalSteps] $msg" -ForegroundColor Yellow
}

Write-Host "`n=== BankrollBBQ Build ===" -ForegroundColor Cyan

# ── Pre-flight checks ──────────────────────────────────────────────
Write-Step "Pre-flight checks..."

# Node.js
if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    Write-Host "  ERROR: Node.js not found. Install from https://nodejs.org" -ForegroundColor Red
    exit 1
}
Write-Host "  Node.js $(node --version)" -ForegroundColor DarkGray

# Python (venv)
$pyVer = & $VenvPython --version 2>&1
Write-Host "  $pyVer ($VenvPython)" -ForegroundColor DarkGray

# npm dependencies
if (-not (Test-Path "frontend\node_modules")) {
    Write-Host "  Installing frontend dependencies..." -ForegroundColor DarkYellow
    Push-Location frontend
    npm install --silent
    Pop-Location
}

Write-Host "  All checks passed." -ForegroundColor Green

# ── Build frontend ─────────────────────────────────────────────────
Write-Step "Building frontend..."
Push-Location frontend
npm run build
if ($LASTEXITCODE -ne 0) {
    Pop-Location
    Write-Host "  Frontend build failed!" -ForegroundColor Red
    exit 1
}
Pop-Location

if (-not (Test-Path "frontend\dist\index.html")) {
    Write-Host "  Frontend build produced no output (missing dist/index.html)!" -ForegroundColor Red
    exit 1
}
Write-Host "  Frontend build complete." -ForegroundColor Green

# Dev mode: skip PyInstaller, just rebuild frontend for launcher.py
if ($Dev) {
    Write-Host "`n=== Dev build done ===" -ForegroundColor Green
    Write-Host "  Frontend rebuilt to frontend/dist/" -ForegroundColor Green
    Write-Host "  Restart launcher.py or refresh the window to see changes." -ForegroundColor Cyan
    exit 0
}

# ── Ensure build dependencies ──────────────────────────────────────
Write-Step "Checking build dependencies..."

$MissingDeps = @()
$CheckDeps = @(
    @{ Module = "PyInstaller"; Package = "pyinstaller" },
    @{ Module = "webview";     Package = "pywebview" }
)

foreach ($dep in $CheckDeps) {
    $result = & $VenvPython -c "import $($dep.Module)" 2>&1
    if ($LASTEXITCODE -ne 0) {
        $MissingDeps += $dep.Package
    }
}

if ($MissingDeps.Count -gt 0) {
    Write-Host "  Installing: $($MissingDeps -join ', ')..." -ForegroundColor DarkYellow
    & $VenvPython -m pip install @MissingDeps --quiet
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  Failed to install build dependencies!" -ForegroundColor Red
        exit 1
    }
}
Write-Host "  Dependencies OK." -ForegroundColor Green

# ── Build exe ──────────────────────────────────────────────────────
Write-Step "Building BankrollBBQ.exe..."

# Icon
$icoPath = "frontend\public\bankrollbbq.ico"
if (-not (Test-Path $icoPath)) {
    Write-Host "  No .ico file found - exe will use default icon." -ForegroundColor DarkGray
}

# Clean previous build artifacts
if (Test-Path "build") { Remove-Item -Recurse -Force "build" }
if (Test-Path "dist\BankrollBBQ.exe") { Remove-Item -Force "dist\BankrollBBQ.exe" }

# Run PyInstaller from venv so it picks up all installed packages
& $VenvPython -m PyInstaller bankrollbbq.spec --clean --noconfirm
if ($LASTEXITCODE -ne 0) {
    Write-Host "  PyInstaller build failed!" -ForegroundColor Red
    exit 1
}

# ── Report ─────────────────────────────────────────────────────────
$exe = "dist\BankrollBBQ.exe"
if (Test-Path $exe) {
    $size = [math]::Round((Get-Item $exe).Length / 1MB, 1)
    Write-Host "`n=== Build complete ===" -ForegroundColor Green
    Write-Host "  Output: $exe ($size MB)" -ForegroundColor Green
    Write-Host "`n  Run with:  .\dist\BankrollBBQ.exe" -ForegroundColor Cyan
    Write-Host "  Data dir:  %LOCALAPPDATA%\BankrollBBQ`n" -ForegroundColor Cyan
} else {
    Write-Host "  Build failed - no .exe produced." -ForegroundColor Red
    exit 1
}
