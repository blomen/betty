# OddOpp Windows Build Script
# Builds the React frontend and packages everything into OddOpp.exe
#
# Usage:  .\build.ps1
# Output: dist\OddOpp.exe

$ErrorActionPreference = "Stop"

Write-Host "`n=== OddOpp Build ===" -ForegroundColor Cyan

# 1. Build frontend
Write-Host "`n[1/4] Building frontend..." -ForegroundColor Yellow
Push-Location frontend
npm run build
if ($LASTEXITCODE -ne 0) {
    Pop-Location
    Write-Host "Frontend build failed!" -ForegroundColor Red
    exit 1
}
Pop-Location
Write-Host "Frontend build complete." -ForegroundColor Green

# 2. Ensure build dependencies
Write-Host "`n[2/4] Checking build dependencies..." -ForegroundColor Yellow
pip install pyinstaller pywebview --quiet
if ($LASTEXITCODE -ne 0) {
    Write-Host "Failed to install build dependencies!" -ForegroundColor Red
    exit 1
}
Write-Host "Dependencies OK." -ForegroundColor Green

# 3. Convert SVG icon to ICO (if not already done)
$icoPath = "frontend\public\terminal.ico"
if (-not (Test-Path $icoPath)) {
    Write-Host "`n[2.5/4] No .ico file found - .exe will use default icon." -ForegroundColor DarkYellow
    Write-Host "  To add a custom icon, place terminal.ico in frontend/public/" -ForegroundColor DarkYellow
}

# 4. Build .exe
Write-Host "`n[3/4] Building OddOpp.exe..." -ForegroundColor Yellow
pyinstaller oddopp.spec --clean --noconfirm
if ($LASTEXITCODE -ne 0) {
    Write-Host "PyInstaller build failed!" -ForegroundColor Red
    exit 1
}

# 5. Report result
$exe = "dist\OddOpp.exe"
if (Test-Path $exe) {
    $size = [math]::Round((Get-Item $exe).Length / 1MB, 1)
    Write-Host "`n[4/4] Build complete!" -ForegroundColor Green
    $sizeStr = "${size} MB"
    Write-Host "  Output: $exe ($sizeStr)" -ForegroundColor Green
    Write-Host "`n  Run with:  .\dist\OddOpp.exe" -ForegroundColor Cyan
    Write-Host "  Data dir:  %LOCALAPPDATA%\OddOpp" -ForegroundColor Cyan
} else {
    Write-Host "Build failed - no .exe produced." -ForegroundColor Red
    exit 1
}
