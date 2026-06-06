@echo off
title IoA Platform

:: Check if .venv exists; if not, run setup first
if not exist ".venv\Scripts\activate.bat" (
    echo [!] Virtual environment not found. Running setup first...
    echo.
    call setup.bat
)

echo Starting IoA Platform...
echo   Backend:  http://127.0.0.1:8000
echo   Simulator: http://127.0.0.1:8001
echo   GUI:  http://127.0.0.1:8000/gui
echo.

call .venv\Scripts\activate.bat

:: Quick dependency check
python -c "import uvicorn" 2>nul
if errorlevel 1 (
    echo [!] Dependencies missing. Installing...
    pip install -r backend/requirements.txt -q
    echo [OK] Dependencies installed.
)

python backend\run.py
pause