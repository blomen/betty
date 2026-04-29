@echo off
setlocal

REM Single-instance lock — prevents two arnold.bat invocations from racing each
REM other into kill.bat + launch.py (the bug that left two zombie launchers
REM fighting over port 8000). Uses an exclusive file handle on a lock file.
set "LOCKFILE=%~dp0arnold\data\.launch.lock"
if not exist "%~dp0arnold\data" mkdir "%~dp0arnold\data" >nul 2>&1

REM Open lockfile for exclusive write — call fails immediately if held.
9>"%LOCKFILE%" (
  REM Always run kill.bat first so a stale launcher / zombie SSH tunnel /
  REM Chromium-profile-lock from a previous session doesn't block the new
  REM launch. kill.bat polls until ports are free + verifies no launcher
  REM survived; non-zero exit means cleanup failed.
  call "%~dp0kill.bat"
  if errorlevel 1 (
    echo [arnold] kill.bat reported leftover state — aborting launch
    pause
    exit /b 1
  )

  cd /d "%~dp0arnold"
  REM Prefer the project venv's Python ^(has the right playwright + chromium versions^).
  REM Fall back to system Python if the venv isn't present.
  REM
  REM `start /WAIT /B` runs python in this console but as its own process
  REM group, so Ctrl+C is delivered to python ^(which exits cleanly via
  REM launch.py's signal handler^) without bubbling up to cmd.exe — that
  REM avoids the "Terminate batch job ^(Y/N^)?" prompt on exit.
  REM `< nul` disconnects stdin so the batch interpreter has nothing to
  REM read after python returns.
  if exist "%~dp0.venv\Scripts\python.exe" (
    start "" /WAIT /B "%~dp0.venv\Scripts\python.exe" launch.py < nul
  ) else (
    start "" /WAIT /B python launch.py < nul
  )
) || (
  echo [arnold] another instance is already running ^(lock held: %LOCKFILE%^)
  echo [arnold] close it first, or run kill.bat to clear stale state
  pause
  exit /b 1
)

endlocal
