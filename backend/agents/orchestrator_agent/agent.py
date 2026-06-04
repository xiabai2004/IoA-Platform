"""Orchestrator Agent — 自然语言→DAG 编排入口

接收自然语言运维请求，解析意图 → 匹配模板 → 填充参数 → 提交 DAG。

两种推理模式：
- LLM 模式：用 qwen-plus 解析意图，提取 domain/fault_type 等参数
- 规则模式：关键词匹配选择模板，正则提取 domain

工厂函数: create_orchestrator_agent(config) → OrchestratorAgent
"""

import json
import re
import httpx
from agents.base_agent import BaseAgent
from agents.tool_client import HttpToolClient
from agents.llm_client import get_llm_client
from ioa_middleware.bus import MessageBus
from ioa_middleware.orchestrator.templates import match_template, TEMPLATES
from agents.orchestrator_agent.workflow import run_orchestrator_workflow

DOMAINS = ["east-china", "north-china", "south-china", "west-china"]
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
        psk = (config or {}).get("auth", {}).get("pre_shared_key", "ioa2026demo")
        self._auth = {"Authorization": f"Bearer {psk}"}
        self._dag_http = httpx.AsyncClient(timeout=30.0)

    # ── 消息处理 ──────────────────────────────────────

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
            print(f"[{self.agent_id}] No user input in message, skipping")
            return {"success": False, "error": "no_user_input"}

        print(f"[{self.agent_id}] Processing: {user_input[:80]}...")

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
            
            print(f"[{self.agent_id}] LangGraph workflow: template={template_name}, confidence={confidence:.2f}")
            for log in workflow_log:
                print(f"  {log}")
                
        except Exception as e:
            # 降级为传统方法
            print(f"[{self.agent_id}] LangGraph workflow failed, falling back: {e}")
            
            # 1. 解析意图 → 获取参数
            dag_params = await self._parse_intent(user_input)

            # 2. 选择模板
            template_name, template_meta, score = match_template(user_input)
            print(f"[{self.agent_id}] Template: {template_name} (score={score:.2f})")

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
            print(f"[{self.agent_id}] DAG {dag_id} submitted ({template_name})")
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

        prompt = f"""你是网络运维编排专家。从用户输入中提取以下信息，返回 JSON。

## 可用模板
{template_list}

## 可用域
{json.dumps(DOMAINS)}

## 用户输入
{user_input}

## 要求
返回严格 JSON，不要包含其他内容：
{{"domain": "<匹配的域>", "fault_type": "<故障类型或unknown>", "urgency": "<low|medium|high>"}}

已初步提取 domain={domain}，请确认或修正。"""
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
