@echo off
setlocal
cd /d "%~dp0"
title ULTRON AI ASSISTANT

set "VENV_PY=%~dp0venv\Scripts\python.exe"
set "VENV_PYW=%~dp0venv\Scripts\pythonw.exe"

if exist "%VENV_PYW%" (
    start "" "%VENV_PYW%" "%~dp0ui.py"
) else (
    "%VENV_PY%" "%~dp0ui.py"
)

if %errorlevel% neq 0 (
    echo.
    echo [ULTRON] Process exited with error code %errorlevel%.
    pause
)
