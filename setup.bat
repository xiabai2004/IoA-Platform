@echo off
title IoA Platform Setup
echo ========================================
echo   IoA Platform - One-Click Setup
echo ========================================
echo.

where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Install Python 3.10+
    echo         https://www.python.org/downloads/
    pause
    exit /b 1
)

echo [1/4] Python detected:
python --version

if not exist ".venv" (
    echo [2/4] Creating virtual environment...
    python -m venv .venv
) else (
    echo [2/4] Virtual environment exists, skip
)

echo [3/4] Installing dependencies...
call .venv\Scripts\activate.bat
pip install -r backend/requirements.txt -q

if not exist ".env" (
    echo [4/4] Creating .env config...
    echo DEEPSEEK_API_KEY=sk-your-key-here> .env
    echo IOA_AUTH_ENABLED=false>> .env
    echo.
    echo [!] Edit .env and set your DeepSeek API Key
    echo     https://platform.deepseek.com/api_keys
) else (
    echo [4/4] .env config exists
)

echo.
echo ========================================
echo   Setup complete! To start:
echo   Double-click run.bat   OR:
echo   .venv\Scripts\activate
echo   python backend\run.py
echo.
echo   Open browser: http://127.0.0.1:8000/gui
echo ========================================
pause