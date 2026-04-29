@echo off
REM Arnold local kill script — terminates everything spawned by arnold.bat:
REM   - python launch.py (launcher process)
REM   - uvicorn on port 8000 (local FastAPI)
REM   - SSH tunnel on port 18000
REM   - Chromium processes using arnold's persistent profile
REM
REM Idempotent + safe to run multiple times.

setlocal EnableDelayedExpansion

echo [arnold-kill] killing local arnold processes...

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference = 'SilentlyContinue';" ^
  "$killed = @();" ^
  "$procs = Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -match 'launch\.py' };" ^
  "foreach ($p in $procs) { Stop-Process -Id $p.ProcessId -Force; $killed += \"launcher PID $($p.ProcessId)\" }" ^
  ";" ^
  "foreach ($port in 8000, 18000) {" ^
  "  $conns = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue;" ^
  "  foreach ($c in $conns) { Stop-Process -Id $c.OwningProcess -Force -ErrorAction SilentlyContinue; $killed += \"port $port PID $($c.OwningProcess)\" }" ^
  "};" ^
  "$ssh = Get-CimInstance Win32_Process -Filter \"Name='ssh.exe'\" | Where-Object { $_.CommandLine -match '18000:localhost:8000' };" ^
  "foreach ($p in $ssh) { Stop-Process -Id $p.ProcessId -Force; $killed += \"ssh tunnel PID $($p.ProcessId)\" }" ^
  ";" ^
  "$chr = Get-CimInstance Win32_Process -Filter \"Name='chrome.exe' OR Name='chromium.exe'\" | Where-Object { $_.CommandLine -match 'browser_profile' };" ^
  "foreach ($p in $chr) { Stop-Process -Id $p.ProcessId -Force; $killed += \"chromium PID $($p.ProcessId)\" }" ^
  ";" ^
  "if ($killed.Count -eq 0) { Write-Host '[arnold-kill] nothing to kill' } else { foreach ($k in $killed) { Write-Host \"[arnold-kill] killed $k\" } };" ^
  "Start-Sleep -Seconds 1;" ^
  "$still8000 = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue;" ^
  "$still18000 = Get-NetTCPConnection -LocalPort 18000 -State Listen -ErrorAction SilentlyContinue;" ^
  "if ($still8000) { Write-Host '[arnold-kill] WARNING: port 8000 still listening' };" ^
  "if ($still18000) { Write-Host '[arnold-kill] WARNING: port 18000 still listening' };" ^
  "if (-not $still8000 -and -not $still18000) { Write-Host '[arnold-kill] ports 8000 + 18000 free' }"

endlocal
