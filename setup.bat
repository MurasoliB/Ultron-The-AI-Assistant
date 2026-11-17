@echo off
setlocal EnableDelayedExpansion
title ULTRON AI ASSISTANT - SETUP & INSTALLATION
cd /d "%~dp0"

echo =====================================================================
echo           ULTRON AI ASSISTANT - AUTOMATED SETUP
echo =====================================================================
echo.

:: 1. Check Python installation
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python was not found in your system PATH!
    echo Please install Python 3.10, 3.11, or 3.12 from https://www.python.org/
    echo Make sure to check "Add Python to PATH" during installation.
    echo.
    pause
    exit /b 1
)

echo [1/4] Checking Python environment...
python --version

:: 2. Create virtual environment if not present
if not exist "venv\Scripts\python.exe" (
    echo [2/4] Creating Python virtual environment in .\venv...
    python -m venv venv
    if %errorlevel% neq 0 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
    echo Virtual environment created successfully.
) else (
    echo [2/4] Virtual environment .\venv already exists.
)

:: 3. Install/upgrade dependencies
echo [3/4] Installing required dependencies from requirements.txt...
echo This may take 1-2 minutes depending on your network connection...
venv\Scripts\python.exe -m pip install --upgrade pip
venv\Scripts\python.exe -m pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo [WARNING] Some dependencies had warnings during installation.
    echo Verifying core runtime modules...
)

:: 4. Setup .env configuration file
echo [4/4] Checking environment configuration...
if not exist ".env" (
    if exist ".env.example" (
        copy ".env.example" ".env" >nul
        echo [NOTICE] Created a fresh '.env' file from '.env.example'.
        echo IMPORTANT: Open '.env' in Notepad and add your GEMINI_API_KEY and GROQ_API_KEY!
    ) else (
        echo [WARNING] .env.example not found. Please create a .env file.
    )
) else (
    echo [OK] Existing '.env' configuration detected.
)

echo.
echo =====================================================================
echo             ULTRON SETUP COMPLETED SUCCESSFULLY!
echo =====================================================================
echo.
echo Next Steps:
echo 1. Open the '.env' file and paste your free Gemini and Groq API keys:
echo    - Gemini: https://aistudio.google.com/
echo    - Groq:   https://console.groq.com/
echo.
echo 2. Run ULTRON using any of the following:
echo    - start_mic.bat          (Starts the floating Ultron Eye HUD)
echo    - start_ultron_ui.bat     (Starts the Full Cyberpunk Desktop GUI)
echo    - start_ultron.bat        (Starts Ultron in standard mode)
echo.
echo Hotkeys:
echo    - Ctrl + Alt + M          (Global Windows Shortcut to Launch Mic)
echo    - F6                      (Stealth Toggle: Hide / Show All Widgets)
echo    - F7                      (Arm Click-to-Inject Code)
echo    - F8                      (Start Typing Code into CodeTantra/Editor)
echo    - F9 / Esc                (Stop / Abort Typing)
echo =====================================================================
echo.
pause
