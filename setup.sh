#!/usr/bin/env bash
set -e

echo "========================================"
echo "  IoA 分布式网络运维协同平台 - 一键部署"
echo "========================================"
echo ""

# Check Python
if ! command -v python3 &> /dev/null; then
    echo "[ERROR] 未找到 python3，请安装 Python 3.10+"
    exit 1
fi

PYTHON=$(command -v python3)
echo "[1/4] 检测到 Python: $($PYTHON --version)"

# Create virtual environment
if [ ! -d ".venv" ]; then
    echo "[2/4] 创建虚拟环境..."
    $PYTHON -m venv .venv
else
    echo "[2/4] 虚拟环境已存在，跳过"
fi

# Activate and install
echo "[3/4] 安装依赖..."
source .venv/bin/activate
pip install -r backend/requirements.txt -q

# Create .env if missing
if [ ! -f ".env" ]; then
    echo "[4/4] 创建 .env 配置文件..."
    echo "DEEPSEEK_API_KEY=sk-your-key-here" > .env
    echo "IOA_AUTH_ENABLED=false" >> .env
    echo ""
    echo "⚠️  请在 .env 中填入你的 DeepSeek API Key"
    echo "   获取地址：https://platform.deepseek.com/api_keys"
else
    echo "[4/4] .env 配置文件已存在"
fi

echo ""
echo "========================================"
echo "  部署完成！启动方式："
echo "  source .venv/bin/activate"
echo "  python backend/run.py"
echo ""
echo "  打开浏览器访问: http://127.0.0.1:8000/gui"
echo "========================================"
