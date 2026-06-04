"""Embedding 语义路由引擎

使用 sentence-transformers 实现真正的语义匹配：
1. 加载中文多语言 embedding 模型
2. 预计算 Agent 能力描述的向量
3. 查询时计算任务描述的向量
4. 用余弦相似度匹配最佳 Agent

相比关键词匹配的优势：
- 理解语义相似性（"网络延迟高" ≈ "网络慢"）
- 支持同义词匹配
- 中文语义理解准确

用法：
    router = EmbeddingRouter()
    best = router.select(candidates, capability, domain, task_desc)
"""

import logging
import numpy as np
from typing import Optional

logger = logging.getLogger("router.embedding")

# ── 能力描述文本（用于 embedding 计算）────────────────────

CAPABILITY_DESCRIPTIONS = {
    "monitor": "监控网络指标采集检测延迟丢包带宽流量",
    "diagnose": "诊断分析根因故障定位异常检测问题排查",
    "repair": "修复恢复清除故障处理自动修复网络恢复",
    "report": "报告汇总总结运维报告数据分析趋势统计",
    "orchestrate": "编排调度协调任务分发流程管理DAG执行",
    "verify": "验证检查确认闭环验证效果评估指标对比",
}

# 域描述
DOMAIN_DESCRIPTIONS = {
    "east-china": "华东地区东部网络",
    "north-china": "华北地区北部网络",
    "south-china": "华南地区南部网络",
    "west-china": "西南地区西部网络",
    "global": "全局所有区域跨域",
}


class EmbeddingRouter:
    """基于 Embedding 的语义路由引擎

    使用 sentence-transformers 计算文本向量，通过余弦相似度匹配。
    """

    def __init__(self, model_name: str = "paraphrase-multilingual-MiniLM-L12-v2"):
        """
        Args:
            model_name: sentence-transformers 模型名称
                       默认使用多语言模型，支持中文
        """
        self._model_name = model_name
        self._model = None
        self._capability_embeddings: dict[str, np.ndarray] = {}
        self._domain_embeddings: dict[str, np.ndarray] = {}
        self._initialized = False

    def _ensure_initialized(self):
        """延迟初始化模型（首次调用时加载）"""
        if self._initialized:
            return

        try:
            from sentence_transformers import SentenceTransformer
            logger.info("Loading embedding model: %s ...", self._model_name)
            self._model = SentenceTransformer(self._model_name)
            logger.info("Embedding model loaded successfully")

            # 预计算能力描述的 embedding
            for cap, desc in CAPABILITY_DESCRIPTIONS.items():
                self._capability_embeddings[cap] = self._model.encode(desc)

            # 预计算域描述的 embedding
            for domain, desc in DOMAIN_DESCRIPTIONS.items():
                self._domain_embeddings[domain] = self._model.encode(desc)

            self._initialized = True
            logger.info("Pre-computed %d capability + %d domain embeddings",
                       len(self._capability_embeddings), len(self._domain_embeddings))

        except Exception as e:
            logger.error("Failed to load embedding model: %s", e)
            self._model = None
            self._initialized = True  # 标记已尝试初始化

    def _cosine_similarity(self, a: np.ndarray, b: np.ndarray) -> float:
        """计算两个向量的余弦相似度"""
        dot_product = np.dot(a, b)
        norm_a = np.linalg.norm(a)
        norm_b = np.linalg.norm(b)
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return float(dot_product / (norm_a * norm_b))

    def _get_embedding(self, text: str) -> np.ndarray:
        """获取文本的 embedding 向量"""
        if self._model is None:
            return np.zeros(384)  # MiniLM 维度
        return self._model.encode(text)

    def select(
        self,
        candidates: list[dict],
        capability: str,
        domain: Optional[str] = None,
        task_desc: str = "",
    ) -> Optional[dict]:
        """从候选 Agent 中选择最佳的一个

        评分维度：
        1. 能力语义相似度 (0.4) - embedding 余弦相似度
        2. 域语义相似度 (0.2) - embedding 余弦相似度
        3. 负载权重 (0.3) - 负载越低分越高
        4. 任务描述相似度 (0.1) - embedding 余弦相似度
        """
        self._ensure_initialized()

        if not candidates:
            return None

        # 过滤：必须有匹配的能力标签
        filtered = [
            a for a in candidates
            if self._capability_matches(a, capability)
        ]
        if not filtered:
            logger.info("No agent matches capability %s", capability)
            return None

        # 如果模型加载失败，降级为简单匹配
        if self._model is None:
            logger.warning("Embedding model not available, using simple matching")
            return self._simple_select(filtered, domain)

        # 计算任务描述的 embedding
        task_embedding = self._get_embedding(task_desc) if task_desc else None

        # 评分
        scored = []
        for agent in filtered:
            score = self._score(agent, capability, domain, task_embedding)
            scored.append((score, agent))

        scored.sort(key=lambda x: x[0], reverse=True)

        best_score, best_agent = scored[0]
        logger.info(
            "EmbeddingRouter: selected %s (score=%.3f, domain=%s, load=%.2f) "
            "from %d candidates for cap=%s",
            best_agent.get("agent_id"), best_score,
            best_agent.get("domain"), best_agent.get("load", 0),
            len(filtered), capability,
        )
        return best_agent

    def _score(
        self,
        agent: dict,
        capability: str,
        domain: Optional[str],
        task_embedding: Optional[np.ndarray],
    ) -> float:
        """综合评分（含 UCB1 在线学习融合）"""
        score = 0.0

        # 1. 能力语义相似度 (0-0.4)
        cap_embedding = self._capability_embeddings.get(capability)
        agent_caps = self._get_agent_capabilities(agent)
        if cap_embedding is not None and agent_caps:
            # 计算与所有能力的平均相似度
            similarities = []
            for cap in agent_caps:
                agent_cap_emb = self._capability_embeddings.get(cap)
                if agent_cap_emb is not None:
                    similarities.append(self._cosine_similarity(cap_embedding, agent_cap_emb))
            if similarities:
                score += 0.4 * max(similarities)

        # 2. 域语义相似度 (0-0.2)
        if domain:
            domain_emb = self._domain_embeddings.get(domain)
            agent_domain = agent.get("domain", "global")
            agent_domain_emb = self._domain_embeddings.get(agent_domain)
            if domain_emb is not None and agent_domain_emb is not None:
                sim = self._cosine_similarity(domain_emb, agent_domain_emb)
                score += 0.2 * sim
        else:
            score += 0.1  # 无域要求，给中等分

        # 3. 负载权重 (0-0.3)
        load = float(agent.get("load", 0.0))
        load = max(0.0, min(1.0, load))
        score += 0.3 * (1.0 - load)

        # 4. 任务描述相似度 (0-0.1)
        if task_embedding is not None:
            # 使用 Agent 的能力描述作为 Agent 的语义表示
            agent_desc = " ".join([CAPABILITY_DESCRIPTIONS.get(c, "") for c in agent_caps])
            if agent_desc:
                agent_embedding = self._get_embedding(agent_desc)
                sim = self._cosine_similarity(task_embedding, agent_embedding)
                score += 0.1 * sim

        # Blend with UCB1 bandit online learning
        from ioa_middleware.router.bandit_router import get_bandit
        return get_bandit().blend(score, agent.get("agent_id", ""))

    def _capability_matches(self, agent: dict, capability: str) -> bool:
        """检查 Agent 是否包含目标能力标签"""
        caps = self._get_agent_capabilities(agent)
        return capability in caps

    def _get_agent_capabilities(self, agent: dict) -> list[str]:
        """获取 Agent 的能力列表"""
        import json
        caps = agent.get("capabilities", [])
        if isinstance(caps, str):
            try:
                caps = json.loads(caps)
            except json.JSONDecodeError:
                caps = []
        return caps

    def _simple_select(self, candidates: list[dict], domain: Optional[str]) -> Optional[dict]:
        """简单匹配降级方案（无 embedding 时使用）"""
        if not candidates:
            return None

        # 按负载排序，选择负载最低的
        scored = []
        for agent in candidates:
            load = float(agent.get("load", 0.0))
            agent_domain = agent.get("domain", "global")
            domain_score = 0.3 if agent_domain == domain else (0.15 if agent_domain == "global" else 0)
            score = domain_score + 0.4 * (1.0 - load)
            scored.append((score, agent))

        scored.sort(key=lambda x: x[0], reverse=True)
        return scored[0][1] if scored else None


# ── 全局单例 ──────────────────────────────────────────────

_router: Optional[EmbeddingRouter] = None


def get_embedding_router() -> EmbeddingRouter:
    """获取全局 EmbeddingRouter 单例"""
    global _router
    if _router is None:
        _router = EmbeddingRouter()
    return _router
