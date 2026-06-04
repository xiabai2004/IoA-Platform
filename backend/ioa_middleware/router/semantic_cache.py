"""语义匹配结果缓存 — LRU

对 (task_desc[:200], agent_id) 做缓存，避免同类路由反复调 LLM。
同一比赛中任务模式有限（监控/诊断/修复/验证/报告），缓存命中率会很高。
"""

import time
from collections import OrderedDict
import logging

logger = logging.getLogger("router.cache")


# 缓存条目 TTL（秒）— 比赛演示期间不变，可以设长一点
_CACHE_TTL_SEC = 300  # 5 分钟


class SemanticCache:
    """LRU 语义匹配缓存，线程安全（CPython GIL）。"""

    def __init__(self, maxsize: int = 128):
        self._cache: OrderedDict[str, tuple[float, float]] = OrderedDict()  # key → (score, expires_at)
        self._maxsize = maxsize

    # ── 公开 API ──────────────────────────────────────────

    def get(self, task_desc: str, agent_id: str) -> float | None:
        """获取缓存的语义匹配分。过期或不存在返回 None。"""
        key = self._make_key(task_desc, agent_id)
        entry = self._cache.get(key)
        if entry is None:
            return None

        score, expires_at = entry
        if time.time() > expires_at:
            del self._cache[key]
            logger.debug("Cache expired: %s", key)
            return None

        # 移到末尾（LRU）
        self._cache.move_to_end(key)
        return score

    def put(self, task_desc: str, agent_id: str, score: float) -> None:
        """存入缓存。"""
        key = self._make_key(task_desc, agent_id)
        expires_at = time.time() + _CACHE_TTL_SEC
        self._cache[key] = (score, expires_at)
        self._cache.move_to_end(key)
        self._evict_if_full()

    def invalidate(self, agent_id: str) -> None:
        """Agent 能力变更时清除其所有缓存。"""
        to_delete = [k for k in self._cache if k.endswith(f"::{agent_id}")]
        for k in to_delete:
            del self._cache[k]
        if to_delete:
            logger.debug("Invalidated %d cache entries for agent %s", len(to_delete), agent_id)

    # ── 内部 ──────────────────────────────────────────────

    def _make_key(self, task_desc: str, agent_id: str) -> str:
        """归一化的缓存键。"""
        # 截断 + 去空格 + 小写，避免标点差异导致缓存未命中
        normalized = task_desc.strip().lower()[:200]
        return f"{normalized}::{agent_id}"

    def _evict_if_full(self) -> None:
        while len(self._cache) > self._maxsize:
            self._cache.popitem(last=False)

    @property
    def size(self) -> int:
        return len(self._cache)
