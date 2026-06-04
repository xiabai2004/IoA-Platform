"""IoA 路由模块 — 智能路由器 + 消息总线导出

提供 SmartRouter，自动选择最佳可用的路由引擎：
1. EmbeddingRouter: 本地 embedding 语义匹配（需要 sentence-transformers）
2. WeightedRouter:  LLM 批量语义评估 + 关键词降级

SmartRouter.select() 是异步接口，与 WeightedRouter 一致。
"""

import asyncio
import logging
from typing import Optional

from ioa_middleware.router.api import router, ws_endpoint

logger = logging.getLogger("router")


class SmartRouter:
    """智能路由器 — 自动选择最佳可用的路由引擎

    优先级（从高到低）：
    1. EmbeddingRouter — 本地 embedding，无网络依赖，语义理解好
    2. WeightedRouter  — LLM 批量语义评估 + 关键词降级

    SmartRouter 懒初始化：首次 select() 调用时才探测引擎，
    探测结果缓存，后续调用零开销。
    """

    def __init__(self):
        self._router = None
        self._router_type: str | None = None
        self._initialized = False

    def _ensure_initialized(self):
        """延迟初始化路由引擎（首次调用时执行）。"""
        if self._initialized:
            return
        self._initialized = True

        # 1. 尝试 EmbeddingRouter（本地 embedding，语义最好）
        try:
            from ioa_middleware.router.embedding_router import get_embedding_router
            emb_router = get_embedding_router()
            emb_router._ensure_initialized()
            if emb_router._model is not None:
                self._router = emb_router
                self._router_type = "embedding"
                logger.info("SmartRouter: using EmbeddingRouter (local semantic matching)")
                return
            else:
                logger.info("SmartRouter: EmbeddingRouter model unavailable, falling back")
        except Exception as e:
            logger.info("SmartRouter: EmbeddingRouter not available: %s", e)

        # 2. 降级为 WeightedRouter（LLM 语义 + 关键词兜底）
        from ioa_middleware.router.weighted_router import WeightedRouter
        self._router = WeightedRouter()
        self._router_type = "weighted"
        logger.info("SmartRouter: using WeightedRouter (LLM + keyword fallback)")

    @property
    def router_type(self) -> str:
        """返回当前使用的路由引擎类型。"""
        self._ensure_initialized()
        return self._router_type or "unknown"

    async def select(
        self,
        candidates: list[dict],
        capability: str,
        domain: str | None = None,
        task_desc: str = "",
    ) -> Optional[dict]:
        """从候选 Agent 中选择最佳的一个。

        参数：
            candidates: 候选 Agent 列表
            capability: 要求的核心能力标签
            domain:     目标域（可选）
            task_desc:  任务描述（用于语义匹配）

        返回：最佳 Agent dict，或 None（无可用 Agent）
        """
        self._ensure_initialized()

        if self._router is None:
            logger.error("SmartRouter: no routing engine available")
            return None

        # EmbeddingRouter.select() 是同步的，用 asyncio.to_thread 包装避免阻塞
        if self._router_type == "embedding":
            return await asyncio.to_thread(
                self._router.select, candidates, capability, domain, task_desc
            )

        # WeightedRouter.select() 是异步的，直接 await
        return await self._router.select(candidates, capability, domain, task_desc)


# ── 导出 ──────────────────────────────────────────────────

__all__ = [
    "router",           # FastAPI APIRouter（消息总线 REST/WS）
    "ws_endpoint",      # WebSocket 端点函数
    "SmartRouter",      # 智能路由器
    "WeightedRouter",   # 多维加权路由（LLM 增强）
    "EmbeddingRouter",  # Embedding 语义路由
]


def WeightedRouter():
    """延迟导入 WeightedRouter，避免循环依赖。"""
    from ioa_middleware.router.weighted_router import WeightedRouter as WR
    return WR()


def EmbeddingRouter():
    """延迟导入 EmbeddingRouter，避免循环依赖。"""
    from ioa_middleware.router.embedding_router import EmbeddingRouter as ER
    return ER()
