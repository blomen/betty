@echo off
REM Arnold local kill script — terminates everything spawned by arnold.bat:
REM   - python launch.py (launcher process) + ALL descendants
REM   - uvicorn / FastAPI child of launcher
REM   - SSH tunnel on port 18000 (any cmdline containing 18000:localhost)
REM   - Chromium / Camoufox using arnold's persistent profile
REM   - Anything still listening on port 8000 / 18000
REM
REM Idempotent + safe to run multiple times. Polls up to 5s for ports to free.

setlocal EnableDelayedExpansion

echo [arnold-kill] killing local arnold processes...

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference = 'SilentlyContinue';" ^
  "$killed = New-Object System.Collections.ArrayList;" ^
  "function Kill-Tree([int]$ParentId, [string]$Label) {" ^
  "  $children = Get-CimInstance Win32_Process -Filter \"ParentProcessId=$ParentId\";" ^
  "  foreach ($c in $children) { Kill-Tree $c.ProcessId \"$Label child\" }" ^
  "  $p = Get-Process -Id $ParentId -ErrorAction SilentlyContinue;" ^
  "  if ($p) { Stop-Process -Id $ParentId -Force -ErrorAction SilentlyContinue; [void]$killed.Add(\"$Label PID $ParentId ($($p.ProcessName))\") }" ^
  "}" ^
  "$launchers = Get-CimInstance Win32_Process -Filter \"Name='python.exe' OR Name='pythonw.exe'\" | Where-Object { $_.CommandLine -match 'launch\.py' };" ^
  "foreach ($p in $launchers) { Kill-Tree $p.ProcessId 'launcher' }" ^
  ";" ^
  "$ssh = Get-CimInstance Win32_Process -Filter \"Name='ssh.exe'\" | Where-Object { $_.CommandLine -match '18000:localhost' };" ^
  "foreach ($p in $ssh) { Stop-Process -Id $p.ProcessId -Force; [void]$killed.Add(\"ssh tunnel PID $($p.ProcessId)\") }" ^
  ";" ^
  "$camouf = Get-CimInstance Win32_Process -Filter \"Name='camoufox-bin.exe' OR Name='firefox.exe'\" | Where-Object { $_.CommandLine -match 'playwright|browser_profile|local' };" ^
  "foreach ($p in $camouf) { Stop-Process -Id $p.ProcessId -Force; [void]$killed.Add(\"camoufox PID $($p.ProcessId)\") }" ^
  ";" ^
  "$chr = Get-CimInstance Win32_Process -Filter \"Name='chrome.exe' OR Name='chromium.exe'\" | Where-Object { $_.CommandLine -match 'browser_profile' };" ^
  "foreach ($p in $chr) { Stop-Process -Id $p.ProcessId -Force; [void]$killed.Add(\"chromium PID $($p.ProcessId)\") }" ^
  ";" ^
  "foreach ($port in 8000, 18000) {" ^
  "  $conns = Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue;" ^
  "  $pids = $conns | Select-Object -ExpandProperty OwningProcess -Unique | Where-Object { $_ -gt 0 -and $_ -ne $PID };" ^
  "  foreach ($procId in $pids) { Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue; [void]$killed.Add(\"port $port PID $procId\") }" ^
  "};" ^
  "if ($killed.Count -eq 0) { Write-Host '[arnold-kill] nothing to kill' } else { foreach ($k in $killed) { Write-Host \"[arnold-kill] killed $k\" } };" ^
  "$deadline = (Get-Date).AddSeconds(5);" ^
  "while ((Get-Date) -lt $deadline) {" ^
  "  $b8 = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue;" ^
  "  $b18 = Get-NetTCPConnection -LocalPort 18000 -State Listen -ErrorAction SilentlyContinue;" ^
  "  if (-not $b8 -and -not $b18) { break };" ^
  "  Start-Sleep -Milliseconds 250" ^
  "};" ^
  "$still8000 = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue;" ^
  "$still18000 = Get-NetTCPConnection -LocalPort 18000 -State Listen -ErrorAction SilentlyContinue;" ^
  "$stillLaunch = Get-CimInstance Win32_Process -Filter \"Name='python.exe' OR Name='pythonw.exe'\" | Where-Object { $_.CommandLine -match 'launch\.py' };" ^
  "if ($still8000) { Write-Host '[arnold-kill] WARNING: port 8000 still listening' };" ^
  "if ($still18000) { Write-Host '[arnold-kill] WARNING: port 18000 still listening' };" ^
  "if ($stillLaunch) { Write-Host \"[arnold-kill] WARNING: $($stillLaunch.Count) launcher(s) still alive\"; exit 1 };" ^
  "if (-not $still8000 -and -not $still18000 -and -not $stillLaunch) { Write-Host '[arnold-kill] all clear (ports 8000 + 18000 free, no launchers)' }"

endlocal & exit /b %ERRORLEVEL%
