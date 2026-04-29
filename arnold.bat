@echo off
REM Always run kill.bat first so a stale launcher / zombie SSH tunnel /
REM Chromium-profile-lock from a previous session doesn't block the new
REM launch. kill.bat is idempotent and safe when nothing's running.
call "%~dp0kill.bat"

cd /d "%~dp0arnold"
REM Prefer the project venv's Python (has the right playwright + chromium versions).
REM Fall back to system Python if the venv isn't present.
if exist "%~dp0.venv\Scripts\python.exe" (
    "%~dp0.venv\Scripts\python.exe" launch.py
) else (
    python launch.py
)
