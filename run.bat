@echo off
chcp 65001 >nul
echo 🚀 启动 IoA 平台...

call .venv\Scripts\activate.bat
echo   后端: http://127.0.0.1:8000
echo   模拟器: http://127.0.0.1:8001
echo   GUI: http://127.0.0.1:8000/gui
echo.
python backend\run.py
pause
