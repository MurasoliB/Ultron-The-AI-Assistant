@echo off
setlocal
cd /d "%~dp0"
title ULTRON FLOATING MIC

:: 1. Verify Virtual Environment
set "VENV_PY=%~dp0venv\Scripts\python.exe"
set "VENV_PYW=%~dp0venv\Scripts\pythonw.exe"

if not exist "%VENV_PY%" (
    echo.
    echo ========================================================
    echo   [ERROR] Ultron virtual environment not found!
    echo   Location: "%VENV_PY%"
    echo   Please run setup first.
    echo ========================================================
    echo.
    pause
    exit /b 1
)

:: 2. Launch Ultron Floating Mic
if exist "%VENV_PYW%" (
    start "" "%VENV_PYW%" "%~dp0mic_button.py"
) else (
    start "" "%VENV_PY%" "%~dp0mic_button.py"
)
exit /b 0
