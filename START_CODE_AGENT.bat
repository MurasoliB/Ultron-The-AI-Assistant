@echo off
cd /d "C:\Users\ELCOT\JARVIS"
title ULTRON CODE AGENT
echo Starting Ultron Code Agent...
"C:\Users\ELCOT\JARVIS\venv\Scripts\python.exe" "C:\Users\ELCOT\JARVIS\code_agent.py"
if %errorlevel% neq 0 (
    echo.
    echo [ULTRON] Process exited with error code %errorlevel%.
    pause
)

