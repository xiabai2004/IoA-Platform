"""语义路由引擎测试 — WeightedRouter + SemanticCache

覆盖：
  - 同步评分（能力/域/负载）
  - LLM 语义评分解析
  - 关键词降级
  - 异步 select 全流程
  - LRU 缓存
"""

import pytest
from ioa_middleware.router.weighted_router import WeightedRouter
from ioa_middleware.router.semantic_cache import SemanticCache


# ── 测试数据 ──────────────────────────────────────────────

SAMPLE_AGENTS = [
    {
        "agent_id": "monitor-east-china",
        "domain": "east-china",
        "capabilities": ["monitor"],
        "status": "active",
        "load": 0.3,
    },
    {
        "agent_id": "diagnoser-global",
        "domain": "global",
        "capabilities": ["diagnose"],
        "status": "active",
        "load": 0.5,
    },
    {
        "agent_id": "repairer-east-china",
        "domain": "east-china",
        "capabilities": ["repair"],
        "status": "active",
        "load": 0.7,
    },
    {
        "agent_id": "monitor-west-china",
        "domain": "west-china",
        "capabilities": ["monitor"],
        "status": "active",
        "load": 0.1,
    },
]


# ── 同步评分测试 ──────────────────────────────────────────

class TestSyncScoring:
    """能力/域/负载评分——纯同步，不涉及 LLM。"""

    def test_capability_matches_exact(self):
        router = WeightedRouter()
        assert router._capability_matches(SAMPLE_AGENTS[0], "monitor") is True
        assert router._capability_matches(SAMPLE_AGENTS[1], "diagnose") is True

    def test_capability_matches_not_found(self):
        router = WeightedRouter()
        assert router._capability_matches(SAMPLE_AGENTS[0], "repair") is False
        assert router._capability_matches(SAMPLE_AGENTS[1], "monitor") is False

    def test_capability_score(self):
        router = WeightedRouter()
        assert router._capability_score(SAMPLE_AGENTS[0], "monitor") == 0.20
        assert router._capability_score(SAMPLE_AGENTS[0], "repair") == 0.0

    def test_domain_score_same(self):
        router = WeightedRouter()
        # 同域 → 0.30
        assert router._domain_score(SAMPLE_AGENTS[0], "east-china") == 0.30

    def test_domain_score_global(self):
        router = WeightedRouter()
        # global Agent 接受任何域 → 0.15
        assert router._domain_score(SAMPLE_AGENTS[1], "east-china") == 0.15

    def test_domain_score_mismatch(self):
        router = WeightedRouter()
        # 不同域且不是 global → 0.0
        assert router._domain_score(SAMPLE_AGENTS[0], "west-china") == 0.0

    def test_domain_score_no_domain_req(self):
        router = WeightedRouter()
        # 无域要求 → 所有 Agent 0.15
        assert router._domain_score(SAMPLE_AGENTS[0], None) == 0.15
        assert router._domain_score(SAMPLE_AGENTS[1], None) == 0.15

    def test_load_score_lower_is_better(self):
        router = WeightedRouter()
        # 负载 0.1 → 0.40 * 0.9 = 0.36
        low = router._load_score({"agent_id": "t", "load": 0.1})
        # 负载 0.7 → 0.40 * 0.3 = 0.12
        high = router._load_score({"agent_id": "t", "load": 0.7})
        assert low == pytest.approx(0.36)
        assert high == pytest.approx(0.12)
        assert low > high

    def test_load_score_clamped(self):
        router = WeightedRouter()
        # 超出 [0,1] 范围的负载应被截断
        neg = router._load_score({"agent_id": "t", "load": -0.5})
        over = router._load_score({"agent_id": "t", "load": 1.5})
        assert 0 <= neg <= 0.40
        assert 0 <= over <= 0.40

    def test_score_sync_integration(self):
        router = WeightedRouter()
        # monitor-east-china: cap=0.20 + domain(east-china)=0.30 + load(0.3)=0.28 = 0.78
        # UCB1 blend (untried agent ucb=1.0): 0.85 * 0.78 + 0.15 * 1.0 = 0.813
        score = router._score_sync(SAMPLE_AGENTS[0], "monitor", "east-china")
        assert score == pytest.approx(0.85 * 0.78 + 0.15 * 1.0)


# ── 关键词降级测试 ────────────────────────────────────────

class TestKeywordFallback:
    """LLM 不可用时降级到关键词匹配。"""

    def test_fallback_with_keyword_hit(self):
        router = WeightedRouter()
        # "诊断" 命中 diagnose 关键词
        score = router._fallback_semantic_score(
            SAMPLE_AGENTS[1],  # diagnoser
            "帮我诊断华东网络故障"
        )
        assert score > 0.0

    def test_fallback_with_no_keyword_hit(self):
        router = WeightedRouter()
        # "修复" 不匹配 monitor 的关键词
        score = router._fallback_semantic_score(
            SAMPLE_AGENTS[0],  # monitor
            "帮我修复一下"
        )
        assert score == 0.0

    def test_fallback_empty_task(self):
        router = WeightedRouter()
        score = router._fallback_semantic_score(SAMPLE_AGENTS[0], "")
        assert score == 0.0


# ── LLM 响应解析测试 ─────────────────────────────────────

class TestLLMResponseParsing:
    """解析 LLM 返回的 JSON 评分。"""

    def test_parse_valid_json(self):
        router = WeightedRouter()
        response = '{"scores": {"monitor-east-china": 0.2, "diagnoser-global": 0.9}}'
        result = router._parse_llm_scores(response, SAMPLE_AGENTS[:2])
        assert result.get("monitor-east-china") == 0.2
        assert result.get("diagnoser-global") == 0.9

    def test_parse_json_with_extra_text(self):
        router = WeightedRouter()
        # LLM 可能在 JSON 前后加解释
        response = '根据分析，我认为：\n{"scores": {"monitor-east-china": 0.3}}\n这个比较合理。'
        result = router._parse_llm_scores(response, SAMPLE_AGENTS[:1])
        assert result.get("monitor-east-china") == 0.3

    def test_parse_invalid_response(self):
        router = WeightedRouter()
        result = router._parse_llm_scores("抱歉，我无法回答这个问题", SAMPLE_AGENTS[:1])
        assert result == {}

    def test_parse_empty_response(self):
        router = WeightedRouter()
        result = router._parse_llm_scores("", SAMPLE_AGENTS[:1])
        assert result == {}

    def test_parse_scores_out_of_range(self):
        router = WeightedRouter()
        # LLM 可能给出超出 [0,1] 的分数，应该被截断
        response = '{"scores": {"monitor-east-china": 999, "diagnoser-global": -1}}'
        result = router._parse_llm_scores(response, SAMPLE_AGENTS[:2])
        assert result.get("monitor-east-china") == 1.0  # clamped
        assert result.get("diagnoser-global") == 0.0     # clamped


# ── Async select 全流程测试 ──────────────────────────────

@pytest.mark.asyncio
class TestSelect:
    """异步 select 全流程——无 LLM 时走关键词降级。"""

    async def test_select_basic(self):
        router = WeightedRouter()
        result = await router.select(
            candidates=SAMPLE_AGENTS,
            capability="monitor",
            domain="east-china",
            task_desc="华东网络延迟高，查一下",
        )
        assert result is not None
        assert result["agent_id"] == "monitor-east-china"

    async def test_select_no_candidates(self):
        router = WeightedRouter()
        result = await router.select(
            candidates=[],
            capability="monitor",
        )
        assert result is None

    async def test_select_no_capability_match(self):
        router = WeightedRouter()
        result = await router.select(
            candidates=SAMPLE_AGENTS,
            capability="nonexistent",
        )
        assert result is None

    async def test_select_domain_preference(self):
        router = WeightedRouter()
        # west-china 域的监控任务，应该优先选 monitor-west-china
        result = await router.select(
            candidates=SAMPLE_AGENTS,
            capability="monitor",
            domain="west-china",
            task_desc="",
        )
        assert result is not None
        assert result["agent_id"] == "monitor-west-china"

    async def test_select_global_preference(self):
        router = WeightedRouter()
        # 不指定域，diagnoser-global 和 repairer-east-china 都有 0.15 域分
        # 但 repairer 负载 0.7 比 diagnoser 负载 0.5 高
        result = await router.select(
            candidates=[SAMPLE_AGENTS[1], SAMPLE_AGENTS[2]],  # diagnoser(load=0.5), repairer(load=0.7)
            capability="repair",
        )
        assert result is not None
        # 只有一个 repair 候选，就是 repairer-east-china
        assert result["agent_id"] == "repairer-east-china"


# ── LRU 缓存测试 ─────────────────────────────────────────

class TestSemanticCache:
    """SemanticCache 单元测试。"""

    def test_cache_hit(self):
        cache = SemanticCache(maxsize=10)
        cache.put("华东网络延迟高", "diagnoser-global", 0.95)
        assert cache.get("华东网络延迟高", "diagnoser-global") == 0.95

    def test_cache_miss(self):
        cache = SemanticCache()
        assert cache.get("华东网络延迟高", "diagnoser-global") is None

    def test_cache_normalization(self):
        cache = SemanticCache()
        # 大小写和空格差异应该命中同一缓存
        cache.put(" 华东网络延迟高 ", "diagnoser-global", 0.9)
        assert cache.get("华东网络延迟高", "diagnoser-global") == 0.9

    def test_cache_lru_eviction(self):
        cache = SemanticCache(maxsize=2)
        cache.put("task-A", "agent-1", 0.9)
        cache.put("task-B", "agent-2", 0.8)
        cache.put("task-C", "agent-3", 0.7)  # 挤掉 task-A
        assert cache.get("task-A", "agent-1") is None  # 已被淘汰
        assert cache.get("task-B", "agent-2") == 0.8   # 还在

    def test_cache_invalidate(self):
        cache = SemanticCache()
        cache.put("任务1", "agent-A", 0.9)
        cache.put("任务2", "agent-A", 0.8)
        cache.invalidate("agent-A")
        assert cache.get("任务1", "agent-A") is None
        assert cache.get("任务2", "agent-A") is None

    def test_cache_ttl(self):
        cache = SemanticCache()
        cache.put("test", "agent", 0.5)
        # 马上取应该还在
        assert cache.get("test", "agent") is not None
