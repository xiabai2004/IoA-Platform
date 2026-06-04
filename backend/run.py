"""IoA 平台启动入口

一条命令启动全系统：
    python run.py

单进程架构（架构方案第七章）：
- 中间件 FastAPI App（端口 8000）：/registry + /messages + /dag + /ws
- 模拟器 FastAPI App（端口 8001）：/simulator/*
- 后台 asyncio Tasks：健康检查、指标生成
- Agent 进程：注册后通过 asyncio.Queue 通信
"""

import asyncio
import logging
import sys
import uvicorn

# 修复 Windows 控制台中文编码
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

from ioa_middleware.config import get_config
from ioa_middleware.main import app as middleware_app
from simulator.api import app as simulator_app

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("run")


async def main():
    """主入口：启动模拟器 + 中间件 + 后台任务。"""
    config = get_config()

    logger.info("=" * 60)
    logger.info("  IoA 分布式网络运维协同平台")
    logger.info("  C4 B-EP1 智能体互联网创新攻关")
    logger.info("=" * 60)

    # 安全检查
    from ioa_middleware.auth import _get_psk_unsafe
    try:
        _get_psk_unsafe(config)
        logger.info("✅ PSK validated successfully.")
    except RuntimeError as e:
        logger.warning("⚠️  PSK check failed: %s", e)

    # 1. 启动模拟器（独立端口，仅本地访问）
    sim_cfg = uvicorn.Config(
        simulator_app,
        host="127.0.0.1",  # 安全：仅本地访问
        port=config["simulator"]["port"],
        log_level="warning",
    )
    sim_server = uvicorn.Server(sim_cfg)
    sim_task = asyncio.create_task(sim_server.serve())
    await asyncio.sleep(0.5)  # 等待模拟器就绪
    logger.info("[Simulator] Started on port %d", config["simulator"]["port"])

    # 2. 启动指标生成器（后台任务）
    from simulator.generator import generator_loop
    asyncio.create_task(generator_loop(config["simulator_config"]["update_interval_ms"]))
    logger.info("[Generator] Metric generator started")

    # 3. 启动中间件（主应用）
    main_cfg = uvicorn.Config(
        middleware_app,
        host=config["middleware"]["host"],
        port=config["middleware"]["port"],
        log_level="info",
    )
    main_server = uvicorn.Server(main_cfg)
    logger.info("[Middleware] Starting on port %d...", config["middleware"]["port"])
    logger.info("")
    logger.info("🌐 GUI: http://127.0.0.1:%d/gui", config["middleware"]["port"])
    logger.info("📊 Simulator: http://127.0.0.1:%d", config["simulator"]["port"])
    logger.info("")

    # 同时运行两个 server
    await asyncio.gather(main_server.serve(), sim_task)


if __name__ == "__main__":
    asyncio.run(main())
