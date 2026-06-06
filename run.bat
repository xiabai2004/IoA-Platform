@echo off
title IoA Platform
echo Starting IoA Platform...
echo   Backend:  http://127.0.0.1:8000
echo   Simulator: http://127.0.0.1:8001
echo   GUI:  http://127.0.0.1:8000/gui
echo.
call .venv\Scripts\activate.bat
python backend\run.py
pause