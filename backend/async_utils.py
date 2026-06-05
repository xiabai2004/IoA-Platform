"""异步工具函数 — 统一 fire-and-forget 任务管理。

用法：
    from async_utils import safe_task, safe_task_group

    # 不再直接 asyncio.create_task
    safe_task(health_check_loop(), name="health_check")
"""
from __future__ import annotations
import asyncio
import logging
from typing import Coroutine, Any

logger = logging.getLogger("async_utils")


def safe_task(coro: Coroutine, *, name: str = "") -> asyncio.Task:
    """包装 asyncio.create_task，确保未捕获异常被记录而非静默消失。

    Returns:
        asyncio.Task — 可选保存以供后续取消。
    """
    task = asyncio.create_task(_wrapper(coro, name))
    return task


async def _wrapper(coro: Coroutine, name: str) -> Any:
    """运行协程，捕获所有未处理异常并记录日志。"""
    try:
        return await coro
    except asyncio.CancelledError:
        logger.debug("Task %r cancelled", name)
    except Exception:
        logger.critical("Task %r crashed!", name, exc_info=True)
