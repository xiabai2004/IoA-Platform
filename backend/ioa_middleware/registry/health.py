"""Agent 注册中心 — 心跳健康检查协程

后台 asyncio Task：每 15s 扫描一次，30s 无心跳的 Agent 标记为 offline。
"""

import asyncio
import time
import logging
from ioa_middleware.registry import store

logger = logging.getLogger("registry.health")

HEARTBEAT_TIMEOUT_MS = 30_000   # 30 秒无心跳 → offline
SCAN_INTERVAL_SEC = 15           # 每 15 秒扫描一次


async def health_check_loop() -> None:
    """后台无限循环：扫描所有 active Agent，超时则标记 offline。"""
    logger.info("Health checker started (timeout=%dms, interval=%ds)",
                HEARTBEAT_TIMEOUT_MS, SCAN_INTERVAL_SEC)
    while True:
        try:
            now_ms = int(time.time() * 1000)
            agents = await store.list_agents(status="active")
            for a in agents:
                last_hb = a.get("last_heartbeat_ms", 0) or 0
                if now_ms - last_hb > HEARTBEAT_TIMEOUT_MS:
                    logger.warning("Agent %s heartbeat timeout (%dms), marking offline",
                                   a["agent_id"], now_ms - last_hb)
                    await store.mark_offline(a["agent_id"])
        except Exception:
            logger.exception("Health check iteration failed")
        await asyncio.sleep(SCAN_INTERVAL_SEC)
