"""测试配置文件"""

import sys
import os

# 添加 backend 目录到 Python 路径
backend_dir = os.path.join(os.path.dirname(__file__), '..', 'backend')
sys.path.insert(0, backend_dir)
