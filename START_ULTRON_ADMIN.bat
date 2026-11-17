@echo off
setlocal EnableDelayedExpansion

:: 1. Force working directory to JARVIS workspace root
cd /d "C:\Users\ELCOT\JARVIS"

:: 2. Check for Administrator privileges
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo [ULTRON] Administrator privileges required for CodeTantra input injection.
    echo [ULTRON] Launching Windows UAC prompt...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process cmd.exe -ArgumentList '/c \"\"%~f0\"\"' -Verb RunAs"
    exit /b
)

title ULTRON AI ASSISTANT - ADMINISTRATOR MODE
echo ========================================================
echo   ULTRON IS RUNNING AS ADMINISTRATOR
echo   CodeTantra-SEA Hardware Input Injection is UNBLOCKED
echo ========================================================
echo.

if not exist "C:\Users\ELCOT\JARVIS\venv\Scripts\python.exe" (
    echo [ERROR] Virtual environment python.exe not found at:
    echo C:\Users\ELCOT\JARVIS\venv\Scripts\python.exe
    pause
    exit /b 1
)

"C:\Users\ELCOT\JARVIS\venv\Scripts\python.exe" "C:\Users\ELCOT\JARVIS\mic_button.py"
if %errorlevel% neq 0 (
    echo.
    echo [ULTRON] Process exited with error code %errorlevel%.
    pause
)
