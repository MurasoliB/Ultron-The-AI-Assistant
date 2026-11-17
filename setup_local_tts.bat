@echo off
setlocal
cd /d "%~dp0"

set "PYEXE=%~dp0venv\Scripts\python.exe"
set "MODEL_DIR=%~dp0tools\piper"

if not exist "%PYEXE%" (
    echo Ultron virtual environment not found. Run setup first.
    pause
    exit /b 1
)

"%PYEXE%" -m pip install -r "%~dp0requirements-local-tts.txt"
if errorlevel 1 (
    echo Could not install the local Piper TTS runtime.
    pause
    exit /b 1
)

if not exist "%MODEL_DIR%" mkdir "%MODEL_DIR%"
pushd "%MODEL_DIR%"
"%PYEXE%" -m piper.download_voices en_US-ryan-high
if errorlevel 1 (
    popd
    echo Could not download the local neural voice model.
    pause
    exit /b 1
)
popd

echo Local Piper neural voice setup is complete.
pause
