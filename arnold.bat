@echo off
cd /d "%~dp0arnold"
REM Prefer the project venv's Python (has the right playwright + chromium versions).
REM Fall back to system Python if the venv isn't present.
if exist "%~dp0.venv\Scripts\python.exe" (
    "%~dp0.venv\Scripts\python.exe" launch.py
) else (
    python launch.py
)
