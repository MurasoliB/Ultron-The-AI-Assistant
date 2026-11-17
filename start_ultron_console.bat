@echo off
setlocal
cd /d "%~dp0"
title ULTRON AI CONSOLE

set "VENV_PY=%~dp0venv\Scripts\python.exe"
if not exist "%VENV_PY%" (
    echo [ERROR] Virtual environment not found at %VENV_PY%
    pause
    exit /b 1
)

"%VENV_PY%" "%~dp0main.py"
if %errorlevel% neq 0 (
    echo.
    echo [ULTRON] Process exited with error code %errorlevel%.
    pause
)
