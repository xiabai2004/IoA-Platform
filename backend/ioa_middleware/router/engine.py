"""语义路由引擎

在能力标签匹配基础上，增加多维加权评分：
1. 能力匹配度（必须匹配，否则排除）
2. 域亲和性（同域 +0.3，global +0.15，其他 +0.0）
3. 负载权重（负载越低分越高：+0.4 * (1 - load)）
4. 任务相似度（LLM 可用时做语义匹配，否则用关键词重叠）

最终选最高分 Agent。

用法：
    engine = SemanticRouter(llm_client)
    best = engine.select(candidates, context)
"""

import re
import logging

logger = logging.getLogger("router.engine")


# ── 能力关键词映射 ────────────────────────────────────────

CAPABILITY_KEYWORDS = {
    "monitor":  ["监控", "采集", "指标", "检测", "monitor", "metrics", "check"],
    "diagnose": ["诊断", "分析", "根因", "定位", "diagnose", "analyze", "root cause"],
    "repair":   ["修复", "恢复", "清除", "处理", "repair", "fix", "recover", "clear"],
    "report":   ["报告", "汇总", "总结", "汇报", "report", "summary"],
    "orchestrate": ["编排", "调度", "协调", "orchestrate", "schedule"],
}


class SemanticRouter:
    """语义路由引擎 — 多维评分选择最佳 Agent。"""

    def __init__(self, llm_client=None):
        self._llm = llm_client

    # ── 公开 API ──────────────────────────────────────────

    def select(
        self,
        candidates: list[dict],
        capability: str,
        domain: str | None = None,
        task_desc: str = "",
    ) -> dict | None:
        """从候选 Agent 中选择最佳的一个。

        参数：
            candidates: 候选 Agent 列表（来自注册中心查询结果）
            capability: 要求的核心能力标签
            domain:     目标域（可选）
            task_desc:  任务描述（用于语义匹配）

        返回：最佳 Agent dict，或 None（无可用 Agent）
        """
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

        # 评分
        scored = [
            (self._score(a, capability, domain, task_desc), a)
            for a in filtered
        ]
        scored.sort(key=lambda x: x[0], reverse=True)

        best_score, best_agent = scored[0]
        logger.info(
            "SemanticRouter: selected %s (score=%.3f, domain=%s, load=%.2f) "
            "from %d candidates for cap=%s",
            best_agent.get("agent_id"), best_score,
            best_agent.get("domain"), best_agent.get("load", 0),
            len(filtered), capability,
        )
        return best_agent

    # ── 评分函数 ──────────────────────────────────────────

    def _score(
        self, agent: dict, capability: str, domain: str | None, task_desc: str
    ) -> float:
        """综合评分：能力 + 域 + 负载 + 语义，总分 0~1。"""
        score = 0.0

        # 1. 能力匹配 (0~0.2)
        score += self._capability_score(agent, capability)

        # 2. 域亲和 (0~0.3)
        score += self._domain_score(agent, domain)

        # 3. 负载权重 (0~0.4) — 负载越低分越高
        score += self._load_score(agent)

        # 4. 语义相似度 (0~0.1) — 可选 LLM 增强
        score += self._semantic_score(agent, task_desc)

        return score

    def _capability_matches(self, agent: dict, capability: str) -> bool:
        """检查 Agent 是否包含目标能力标签。"""
        import json
        caps = agent.get("capabilities", [])
        if isinstance(caps, str):
            try:
                caps = json.loads(caps)
            except json.JSONDecodeError:
                caps = []
        return capability in caps

    def _capability_score(self, agent: dict, capability: str) -> float:
        """能力匹配得分。精确匹配 0.2，否则 0.0。"""
        if self._capability_matches(agent, capability):
            return 0.20
        return 0.0

    def _domain_score(self, agent: dict, domain: str | None) -> float:
        """域亲和得分。"""
        if not domain:
            return 0.15  # 无域要求，给所有 Agent 中等分
        agent_domain = agent.get("domain", "global")
        if agent_domain == domain:
            return 0.30  # 同域最高
        elif agent_domain == "global":
            return 0.15  # global 可跨域，次之
        else:
            return 0.0   # 不同域，不亲和

    def _load_score(self, agent: dict) -> float:
        """负载得分：负载越低分越高。"""
        load = float(agent.get("load", 0.0))
        load = max(0.0, min(1.0, load))
        return 0.40 * (1.0 - load)

    def _semantic_score(self, agent: dict, task_desc: str) -> float:
        """语义相似度得分（关键词重叠）。"""
        if not task_desc:
            return 0.0

        agent_cap = agent.get("capabilities", [])
        import json
        if isinstance(agent_cap, str):
            try:
                agent_cap = json.loads(agent_cap)
            except json.JSONDecodeError:
                agent_cap = []

        task_lower = task_desc.lower()
        all_keywords = []
        for cap in agent_cap:
            all_keywords.extend(CAPABILITY_KEYWORDS.get(cap, []))

        if not all_keywords:
            return 0.0

        hits = sum(1 for kw in all_keywords if kw.lower() in task_lower)
        return 0.10 * min(hits / max(len(all_keywords), 1), 1.0)
