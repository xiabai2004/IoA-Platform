"""Orchestrator Agent — 自然语言→DAG 编排入口

接收自然语言运维请求，解析意图 → 匹配模板 → 填充参数 → 提交 DAG。

两种推理模式：
- LLM 模式：用 DeepSeek 解析意图，提取 domain/fault_type 等参数
- 规则模式：关键词匹配选择模板，正则提取 domain

工厂函数: create_orchestrator_agent(config) → OrchestratorAgent
"""

import json
import logging
import re
import time
import httpx
from agents.base_agent import BaseAgent
from agents.tool_client import HttpToolClient
from agents.llm_client import get_llm_client
from ioa_middleware.bus import MessageBus
from ioa_middleware.orchestrator.templates import match_template, TEMPLATES
from agents.orchestrator_agent.workflow import run_orchestrator_workflow
from prompts import PROMPTS

DOMAINS = ["east-china", "north-china", "south-china", "west-china"]
logger = logging.getLogger("orchestrator_agent")
DOMAIN_ALIASES = {
    "华东": "east-china", "东部": "east-china", "east": "east-china",
    "华北": "north-china", "北部": "north-china", "north": "north-china",
    "华南": "south-china", "南部": "south-china", "south": "south-china",
    "西南": "west-china", "西部": "west-china", "west": "west-china",
    "华西": "west-china",
}


class OrchestratorAgent(BaseAgent):
    """编排 Agent — 自然语言 → DAG。"""

    def __init__(self, bus: MessageBus, config: dict | None = None):
        super().__init__(
            agent_id="orchestrator-agent",
            domain="global",
            capability="orchestrate",
            bus=bus,
            config=config,
        )
        self.tool_client = HttpToolClient()
        self._llm = get_llm_client(config)
        # DAG 提交地址
        port = (config or {}).get("middleware", {}).get("port", 8000)
        self._dag_url = f"http://127.0.0.1:{port}/dag"
        # 使用与中间件相同的 PSK
        psk = (config or {}).get("auth", {}).get("pre_shared_key", "")
        self._auth = {"Authorization": f"Bearer {psk}"} if psk else {}
        self._dag_http = httpx.AsyncClient(timeout=30.0)
        # 故障场景缓存：相同输入 → 快速返回上次结果
        self._scenario_cache: dict[str, dict] = {}
        self._CACHE_TTL_SEC = 600  # 10 分钟缓存有效期

    # ── 消息处理 ──────────────────────────────────────

    def _cache_key(self, user_input: str) -> str:
        """生成缓存键：归一化输入（去空格、小写、截断）。"""
        return user_input.strip().lower()[:100]

    def _lookup_cache(self, user_input: str) -> dict | None:
        """查找场景缓存，过期返回 None。"""
        key = self._cache_key(user_input)
        entry = self._scenario_cache.get(key)
        if entry is None:
            return None
        if time.time() - entry.get("cached_at", 0) > self._CACHE_TTL_SEC:
            del self._scenario_cache[key]
            return None
        return entry

    def _store_cache(self, user_input: str, dag_id: str, template_name: str,
                     diagnosis_summary: str, elapsed_ms: int) -> None:
        """缓存已完成的场景结果。"""
        key = self._cache_key(user_input)
        self._scenario_cache[key] = {
            "dag_id": dag_id,
            "template": template_name,
            "diagnosis": diagnosis_summary,
            "elapsed_ms": elapsed_ms,
            "cached_at": time.time(),
        }
        # 限制缓存大小
        if len(self._scenario_cache) > 64:
            oldest = min(self._scenario_cache, key=lambda k: self._scenario_cache[k]["cached_at"])
            del self._scenario_cache[oldest]

    async def handle_message(self, topic: str, message: dict) -> dict:
        """处理 task / user 消息：解析 NL → 提交 DAG → 返回 dag_id。"""
        intent = message.get("intent", {})
        msg_type = intent.get("type", "")

        payload = message.get("payload", {})
        params = payload.get("params", {})
        correlation_id = message.get("correlation_id", "")
        dag_id_existing = payload.get("dag_id", "")
        node_id = payload.get("node_id", "")

        # 提取用户输入（多种来源）
        user_input = (
            params.get("message", "")
            or params.get("query", "")
            or intent.get("description", "")
            or payload.get("message", "")
        )

        if not user_input:
            logger.info("[%s] No user input in message, skipping", self.agent_id)
            return {"success": False, "error": "no_user_input"}

        logger.info("[%s] Processing: %s...", self.agent_id, user_input[:80])

        # ── 显式模板指定（第二场景直接指定 template）──
        explicit_template = params.get("template", "")
        if explicit_template and explicit_template in TEMPLATES:
            logger.info("[%s] Explicit template: %s", self.agent_id, explicit_template)
            dag_params = params
            template_meta = TEMPLATES[explicit_template]
            dag_def = template_meta["fn"](dag_params)
            try:
                resp = await self._dag_http.post(
                    self._dag_url,
                    json=dag_def,
                    headers=self._auth,
                )
                resp.raise_for_status()
                dag_id = dag_def.get("dag_id", "unknown")
                logger.info("[%s] DAG %s submitted (%s)", self.agent_id, dag_id, explicit_template)
                return {
                    "success": True,
                    "output": {
                        "dag_id": dag_id,
                        "template": explicit_template,
                        "user_input": user_input,
                        "message": f"DAG {dag_id} 已提交 (模板: {explicit_template})",
                    },
                }
            except Exception as e:
                logger.exception("[%s] Failed to submit DAG: %s", self.agent_id, e)
                return {"success": False, "error": str(e)}

        # ── 场景缓存：相同指令秒级响应 ──
        cached = self._lookup_cache(user_input)
        if cached:
            cached_at_ago = int(time.time() - cached["cached_at"])
            logger.info("[%s] Cache hit for: %s... (score=%.2f, cached %ds ago, dag=%s)", self.agent_id, user_input[:40], cached[1], cached_at_ago, cached['dag_id'])
            return {
                "success": True,
                "output": {
                    "dag_id": cached["dag_id"],
                    "template": cached["template"],
                    "user_input": user_input,
                    "cached": True,
                    "diagnosis": cached["diagnosis"],
                    "elapsed_ms": cached["elapsed_ms"],
                    "message": (
                        f"[缓存命中] 相同场景 {cached_at_ago}s 前已处理完毕。"
                        f"诊断: {cached['diagnosis']}。"
                        f"上次耗时: {cached['elapsed_ms']/1000:.1f}s。"
                        f"正在提交新的修复流程..."
                    ),
                },
            }

        # 使用 LangGraph 工作流（优先）或降级为传统方法
        try:
            workflow_result = await run_orchestrator_workflow(
                user_input=user_input,
                llm_client=self._llm,
                templates=TEMPLATES,
            )
            
            dag_def = workflow_result.get("dag_definition", {})
            template_name = workflow_result.get("template_name", "unknown")
            dag_params = workflow_result.get("intent", {})
            confidence = workflow_result.get("confidence", 0.0)
            workflow_log = workflow_result.get("workflow_log", [])
            
            logger.info("[%s] LangGraph workflow: template=%s, confidence=%.2f", self.agent_id, template_name, confidence)
            for log in workflow_log:
                logger.info("  %s", log)
                
        except Exception as e:
            # 降级为传统方法
            logger.info("[%s] LangGraph workflow failed, falling back: %s", self.agent_id, e)
            
            # 1. 解析意图 → 获取参数
            dag_params = await self._parse_intent(user_input)

            # 2. 选择模板
            template_name, template_meta, score = match_template(user_input)
            logger.info("[%s] Template: %s (score=%.2f)", self.agent_id, template_name, score)

            # 3. 生成 DAG 定义
            dag_def = template_meta["fn"](dag_params)

        # 4. 提交 DAG
        try:
            resp = await self._dag_http.post(
                self._dag_url,
                json=dag_def,
                headers=self._auth,
            )
            resp.raise_for_status()
            result_data = resp.json()
            dag_id = result_data.get("dag_id", dag_def.get("dag_id", ""))

            # 生成诊断摘要（用于缓存快速响应）
            domain = dag_params.get("domain", "east-china")
            diagnosis_summary = f"{template_name} @ {domain}"
            if dag_params.get("fault_type") and dag_params["fault_type"] != "unknown":
                diagnosis_summary = f"{dag_params['fault_type']} @ {domain}"

            result = {
                "success": True,
                "output": {
                    "dag_id": dag_id,
                    "template": template_name,
                    "params": dag_params,
                    "user_input": user_input,
                    "message": f"DAG {dag_id} 已提交（模板: {template_name}），正在执行中...",
                },
            }
            # 缓存场景结果（下次相同指令秒级响应）
            self._store_cache(user_input, dag_id, template_name, diagnosis_summary, 0)
            logger.info("[%s] DAG %s submitted (%s)", self.agent_id, dag_id, template_name)
        except Exception as e:
            result = {
                "success": False,
                "error": str(e),
            }

        # 5. 返回结果（通过 bus reply 机制自动返回给调用者）
        return result

    # ── 意图解析 ──────────────────────────────────────

    async def _parse_intent(self, user_input: str) -> dict:
        """解析自然语言 → 提取 domain / fault_type 等参数。

        LLM 优先，无 LLM 时降级为正则 + 关键词。
        """
        # 先提取 domain
        domain = self._extract_domain(user_input)

        # LLM 增强
        if self._llm.available:
            llm_params = await self._llm_parse(user_input, domain)
            if llm_params:
                return llm_params

        # 规则降级
        return {
            "domain": domain,
            "correlation_id": None,
        }

    def _extract_domain(self, text: str) -> str:
        """从文本中提取域信息。"""
        text_lower = text.lower()
        # 全域关键词
        global_keywords = ["全域", "所有域", "全部域", "全局", "所有地区", "全部地区", "所有故障", "全部故障"]
        if any(kw in text for kw in global_keywords):
            return "global"
        # 直接匹配英文域名
        for domain in DOMAINS:
            if domain in text_lower:
                return domain
        # 中文别名
        for alias, domain in DOMAIN_ALIASES.items():
            if alias in text:
                return domain
        return "east-china"

    async def _llm_parse(self, user_input: str, domain: str) -> dict | None:
        """LLM 解析意图 → 结构化参数。"""
        template_list = "\n".join(
            f"- {name}: {meta['description']} (关键词: {', '.join(meta['keywords'][:4])})"
            for name, meta in TEMPLATES.items()
        )

        prompt = PROMPTS.orchestrator_intent_rule(
            template_list, DOMAINS, user_input, domain)
        resp = await self._llm.ask(prompt)
        if not resp:
            return None
        try:
            # 提取 JSON（可能被包裹在 ``` 中）
            if "```" in resp:
                resp = resp.split("```")[1]
                if resp.startswith("json"):
                    resp = resp[4:]
            parsed = json.loads(resp.strip())
            parsed.setdefault("domain", domain)
            parsed.setdefault("correlation_id", None)
            return parsed
        except json.JSONDecodeError:
            return None

    async def stop(self) -> None:
        await self._dag_http.aclose()
        await super().stop()


# ── 工厂 ─────────────────────────────────────────────

def create_orchestrator_agent(bus: MessageBus, config: dict) -> OrchestratorAgent:
    """创建 Orchestrator Agent（全局，单个实例）。"""
    return OrchestratorAgent(bus=bus, config=config)
