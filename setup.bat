@echo off
chcp 65001 >nul
echo ========================================
echo   IoA 分布式网络运维协同平台 - 一键部署
echo ========================================
echo.

:: Check Python
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] 未找到 Python，请安装 Python 3.10+
    echo         下载：https://www.python.org/downloads/
    pause
    exit /b 1
)

echo [1/4] 检测到 Python:
python --version

:: Create virtual environment
if not exist ".venv" (
    echo [2/4] 创建虚拟环境...
    python -m venv .venv
) else (
    echo [2/4] 虚拟环境已存在，跳过
)

:: Activate and install
echo [3/4] 安装依赖...
call .venv\Scripts\activate.bat
pip install -r backend/requirements.txt -q

:: Create .env if missing
if not exist ".env" (
    echo [4/4] 创建 .env 配置文件...
    echo DEEPSEEK_API_KEY=sk-your-key-here > .env
    echo IOA_AUTH_ENABLED=false >> .env
    echo. 
    echo ⚠️  请在 .env 中填入你的 DeepSeek API Key
    echo    获取地址：https://platform.deepseek.com/api_keys
) else (
    echo [4/4] .env 配置文件已存在
)

echo.
echo ========================================
echo   部署完成！启动方式：
echo   双击 run.bat  或  执行以下命令：
echo   .venv\Scripts\activate
echo   python backend\run.py
echo.
echo   打开浏览器访问: http://127.0.0.1:8000/gui
echo ========================================
pause
