"""多维加权路由引擎 — 支持 LLM 语义匹配

基于多维加权评分选择最佳 Agent：
1. 能力匹配度（必须匹配，否则排除，权重 0.2）
2. 域亲和性（同域 +0.3，global +0.15，其他 +0.0，权重 0.3）
3. 负载权重（负载越低分越高：+0.4 * (1 - load)，权重 0.4）
4. **语义相似度（LLM 评估任务与 Agent 能力的匹配度，权重 0.1）**

语义部分使用 LLM 批量评估所有候选，一次调用出所有分，
配合 LRU 缓存避免重复调 LLM。LLM 不可用时自动降级关键词匹配。

用法：
    engine = WeightedRouter()
    best = await engine.select(candidates, context)
"""

import re
import json
import logging

from ioa_middleware.router.semantic_cache import SemanticCache

logger = logging.getLogger("router.weighted")


# ── 能力关键词映射（LLM 降级时使用）────────────────────────

CAPABILITY_KEYWORDS = {
    "monitor":  ["监控", "采集", "指标", "检测", "monitor", "metrics", "check"],
    "diagnose": ["诊断", "分析", "根因", "定位", "diagnose", "analyze", "root cause"],
    "repair":   ["修复", "恢复", "清除", "处理", "repair", "fix", "recover", "clear"],
    "report":   ["报告", "汇总", "总结", "汇报", "report", "summary"],
    "orchestrate": ["编排", "调度", "协调", "orchestrate", "schedule"],
}


# ── LLM 语义匹配 Prompt（一次评估所有候选）─────────────────

_SEMANTIC_PROMPT_TEMPLATE = """你是一个智能体路由评估专家。判断以下网络运维任务与各候选Agent能力的匹配程度。

任务描述: "{task_desc}"

候选Agent列表：
{candidates_text}

请分析每个Agent与任务的匹配程度，按以下标准评分（0-1）：
- 0.9-1.0: 完全匹配，该Agent的功能就是处理这类任务
- 0.5-0.8: 部分匹配，Agent能胜任但不是最优选择
- 0.1-0.4: 弱匹配，能力与需求有一定关联
- 0.0:    完全不匹配

只返回JSON格式：{{"scores": {{"agent_id_1": 0.0, "agent_id_2": 0.0}}}}
不要任何额外说明。"""


class WeightedRouter:
    """多维加权路由引擎 — 基于多维评分选择最佳 Agent。

    支持 LLM 语义匹配，自动降级关键词匹配。
    """

    def __init__(self, llm_client=None):
        self._llm = llm_client
        self._cache = SemanticCache(maxsize=128)
        self._last_decision: dict | None = None

    # ── lazy LLM client ──────────────────────────────────

    @property
    def _llm_client(self):
        """懒加载 LLM 客户端。"""
        if self._llm is None:
            try:
                from agents.llm_client import get_llm_client
                from ioa_middleware.config import get_config
                self._llm = get_llm_client(get_config())
            except Exception:
                self._llm = None
        return self._llm

    # ── 公开 API ──────────────────────────────────────────

    async def select(
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

        # 1. 过滤：必须有匹配的能力标签
        filtered = [
            a for a in candidates
            if self._capability_matches(a, capability)
        ]
        if not filtered:
            logger.info("No agent matches capability %s", capability)
            return None

        # 2. 同步评分（能力 + 域 + 负载），不含语义
        scored = [
            (self._score_sync(a, capability, domain), a)
            for a in filtered
        ]

        # 3. 批量语义评分（LLM 一次调用评估所有候选）
        if task_desc and self._llm_client and self._llm_client.available:
            try:
                semantic_scores = await self._batch_semantic_score(
                    [a for _, a in scored], task_desc
                )
                # 合并语义分
                scored = [
                    (sync_score + 0.10 * semantic_scores.get(a.get("agent_id", ""), 0.0), a)
                    for sync_score, a in scored
                ]
            except Exception as e:
                logger.warning("LLM semantic scoring failed, using keywords: %s", e)
                # 降级：添加关键词语义分
                scored = [
                    (sync_score + self._fallback_semantic_score(a, task_desc), a)
                    for sync_score, a in scored
                ]
        else:
            # 无 LLM：关键词降级
            scored = [
                (sync_score + self._fallback_semantic_score(a, task_desc), a)
                for sync_score, a in scored
            ]

        # 4. 选最高分
        scored.sort(key=lambda x: x[0], reverse=True)
        best_score, best_agent = scored[0]

        semantic_mode = "llm" if (task_desc and self._llm_client and self._llm_client.available) else "keywords"
        logger.info(
            "SemanticRouter: selected %s (score=%.3f, domain=%s, load=%.2f) (semantic=%s) "
            "from %d candidates for cap=%s",
            best_agent.get("agent_id"), best_score,
            best_agent.get("domain"), best_agent.get("load", 0),
            semantic_mode, len(filtered), capability,
        )

        # 记录路由决策（供调用方持久化）
        self._last_decision = {
            "engine": "weighted",
            "semantic_mode": semantic_mode,
            "capability": capability,
            "domain": domain,
            "selected_agent": best_agent.get("agent_id"),
            "selected_score": round(best_score, 4),
            "candidates_count": len(filtered),
            "candidates": [
                {"agent_id": a.get("agent_id"), "score": round(s, 4), "domain": a.get("domain")}
                for s, a in scored[:5]  # 只保留 top 5
            ],
        }
        return best_agent

    # ── 同步评分（不含语义）────────────────────────────────

    def _score_sync(self, agent: dict, capability: str, domain: str | None) -> float:
        """同步部分：能力 + 域 + 负载 + UCB 探索，总分 0~0.9+。"""
        score = 0.0
        score += self._capability_score(agent, capability)  # 0~0.2
        score += self._domain_score(agent, domain)           # 0~0.3
        score += self._load_score(agent)                     # 0~0.4
        # Blend with UCB1 bandit online learning
        from ioa_middleware.router.bandit_router import get_bandit
        return get_bandit().blend(score, agent.get("agent_id", ""))

    # ── LLM 批量语义评分 ──────────────────────────────────

    async def _batch_semantic_score(
        self, candidates: list[dict], task_desc: str
    ) -> dict[str, float]:
        """一次 LLM 调用评估所有候选，返回 {agent_id: score}。"""
        # 先查缓存，过滤出未缓存的
        uncached: list[dict] = []
        cached_scores: dict[str, float] = {}
        for a in candidates:
            aid = a.get("agent_id", "")
            cached = self._cache.get(task_desc, aid)
            if cached is not None:
                cached_scores[aid] = cached
            else:
                uncached.append(a)

        # 如果全部命中缓存，直接返回
        if not uncached:
            return cached_scores

        # 构建批量 Prompt
        lines = []
        for i, a in enumerate(uncached, 1):
            aid = a.get("agent_id", "?")
            caps = a.get("capabilities", [])
            if isinstance(caps, str):
                try:
                    caps = json.loads(caps)
                except json.JSONDecodeError:
                    caps = []
            desc = ""
            metadata = a.get("metadata")
            if isinstance(metadata, dict):
                desc = metadata.get("description", "")
            lines.append(
                f"{i}. {aid}\n"
                f"   能力: {caps}\n"
                f"   描述: {desc or '无'}"
            )

        candidates_text = "\n".join(lines)
        prompt = _SEMANTIC_PROMPT_TEMPLATE.format(
            task_desc=task_desc,
            candidates_text=candidates_text,
        )

        # 调 LLM
        response = await self._llm_client.ask(prompt)
        scores = self._parse_llm_scores(response, uncached)

        # 写缓存
        for aid, sc in scores.items():
            self._cache.put(task_desc, aid, sc)

        # 合并缓存命中 + LLM 结果
        scores.update(cached_scores)
        return scores

    def _parse_llm_scores(
        self, response: str, candidates: list[dict]
    ) -> dict[str, float]:
        """解析 LLM JSON 返回，提取评分。"""
        if not response:
            return {}

        # 尝试提取 JSON（LLM 可能多说了几句）
        json_str = response.strip()
        # 找到第一个 { 和最后一个 }
        start = json_str.find("{")
        end = json_str.rfind("}")
        if start == -1 or end == -1:
            logger.warning("LLM response has no JSON: %s...", response[:100])
            return {}

        try:
            json_str = json_str[start:end + 1]
            data = json.loads(json_str)
            raw_scores = data.get("scores", data)  # 兼容直接返回 dict
        except json.JSONDecodeError:
            logger.warning("Failed to parse LLM JSON: %s...", response[:100])
            return {}

        # 归一化到 0-1，无效值置 0
        result: dict[str, float] = {}
        for a in candidates:
            aid = a.get("agent_id", "")
            raw = raw_scores.get(aid, 0.0)
            try:
                score = max(0.0, min(1.0, float(raw)))
            except (ValueError, TypeError):
                score = 0.0
            result[aid] = score

        return result

    # ── 关键词降级（无 LLM 时使用）─────────────────────────

    def _fallback_semantic_score(self, agent: dict, task_desc: str) -> float:
        """关键词匹配降级方案。"""
        if not task_desc:
            return 0.0

        agent_cap = agent.get("capabilities", [])
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

    # ── 能力匹配 ──────────────────────────────────────────

    def _capability_matches(self, agent: dict, capability: str) -> bool:
        """检查 Agent 是否包含目标能力标签。"""
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
            return 0.15
        agent_domain = agent.get("domain", "global")
        if agent_domain == domain:
            return 0.30
        elif agent_domain == "global":
            return 0.15
        else:
            return 0.0

    def _load_score(self, agent: dict) -> float:
        """负载得分：负载越低分越高。"""
        load = float(agent.get("load", 0.0))
        load = max(0.0, min(1.0, load))
        return 0.40 * (1.0 - load)
