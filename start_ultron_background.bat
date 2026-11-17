@echo off
setlocal
cd /d "%~dp0"

set "VENV_PYW=%~dp0venv\Scripts\pythonw.exe"
if not exist "%VENV_PYW%" (
    echo [ERROR] Virtual environment pythonw not found at %VENV_PYW%
    pause
    exit /b 1
)

start "" "%VENV_PYW%" "%~dp0main.py"
