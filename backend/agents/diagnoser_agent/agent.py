"""Diagnoser Agent — 根因分析

接收 Monitor 采集的异常指标 + 拓扑数据，
通过规则引擎（或 LLM 增强）确定故障类型和根因。

工厂函数: create_diagnoser_agent(config) → DiagnoserAgent
"""

import json
import logging
from agents.base_agent import BaseAgent
from agents.tool_client import HttpToolClient, AutoToolClient, TOOL_GET_ALL_METRICS, TOOL_GET_TOPOLOGY
from agents.llm_client import get_llm_client
from agents.diagnoser_agent.workflow import run_diagnoser_workflow
from ioa_middleware.bus import MessageBus
from prompts import PROMPTS

logger = logging.getLogger(__name__)

# ── 症状 → 故障类型映射 ─────────────────────────────────

SYMPTOM_RULES = [
    # 条件函数基于实际指标值判定，不依赖 Monitor 的 severity 标签（标签不够稳定）
    {
        "name": "link_outage_or_device_failure",
        "check": lambda a: any(
            m["metric"] == "packet_loss" and m["value"] >= 0.99
            for m in a
        ),
        "fault_type": "device_failure",
        "confidence": 0.95,
        "description": "完全丢包，疑似链路中断或设备离线",
        "repair_action": "link_failover",
    },
    {
        "name": "ddos_attack",
        "check": lambda a: (
            any(m["metric"] == "bandwidth_util" and m["value"] >= 0.95 for m in a)
            and any(m["metric"] == "latency_ms" and m["value"] >= 200.0 for m in a)
        ),
        "fault_type": "ddos",
        "confidence": 0.90,
        "description": "带宽接近100%且延迟极高，疑似DDoS攻击",
        "repair_action": "acl_deploy",
    },
    {
        "name": "link_congestion",
        "check": lambda a: (
            any(m["metric"] == "bandwidth_util" and m["value"] >= 0.85 for m in a)
            and any(m["metric"] == "latency_ms" and m["value"] >= 100.0 for m in a)
            and not any(m["metric"] == "packet_loss" and m["value"] >= 0.99 for m in a)
        ),
        "fault_type": "link_congestion",
        "confidence": 0.85,
        "description": "高带宽+高延迟但未中断，疑似链路拥塞",
        "repair_action": "traffic_shape",
    },
    {
        "name": "cpu_overload",
        "check": lambda a: (
            any(m["metric"] == "latency_ms" and 80.0 <= m["value"] < 150.0 for m in a)
            and not any(m["metric"] == "bandwidth_util" and m["value"] >= 0.85 for m in a)
        ),
        "fault_type": "cpu_overload",
        "confidence": 0.65,
        "description": "中等延迟升高但带宽正常，疑似CPU过载",
        "repair_action": "restart_service",
    },
    {
        "name": "cpu_overload_severe",
        "check": lambda a: (
            any(m["metric"] == "latency_ms" and m["value"] >= 150.0 for m in a)
            and not any(m["metric"] == "bandwidth_util" and m["value"] >= 0.85 for m in a)
            and not any(m["metric"] == "packet_loss" and m["value"] >= 0.99 for m in a)
        ),
        "fault_type": "cpu_overload",
        "confidence": 0.75,
        "description": "延迟严重升高但带宽正常，疑似CPU严重过载",
        "repair_action": "restart_service",
    },
    {
        "name": "misconfig",
        "check": lambda a: any(
            m["metric"] == "packet_loss" and 0.01 < m["value"] < 0.50
            for m in a
        ),
        "fault_type": "misconfig",
        "confidence": 0.75,
        "description": "中度丢包，疑似路由配置错误",
        "repair_action": "restart_service",
    },
]


class DiagnoserAgent(BaseAgent):
    """根因分析 Agent — 规则引擎 + LLM 增强。"""

    def __init__(self, bus: MessageBus, config: dict | None = None):
        super().__init__(
            agent_id="diagnoser-global",
            domain="global",
            capability="diagnose",
            bus=bus,
            config=config,
        )
        self.tool_client = AutoToolClient()  # 优先 MCP，降级 HTTP
        self._llm = get_llm_client(config)

    # ── 消息处理 ──────────────────────────────────────

    async def handle_message(self, topic: str, message: dict) -> dict:
        """处理 task 消息：收集上下文 → 根因分析 → 返回诊断。"""
        intent = message.get("intent", {})
        if intent.get("type") != "task":
            return {"success": False, "error": "not_a_task"}

        payload = message.get("payload", {})
        dag_id = payload.get("dag_id", "")
        node_id = payload.get("node_id", "")
        correlation_id = message.get("correlation_id", "")
        params = payload.get("params", {})

        # 从前置 Monitor 节点结果中提取异常数据
        monitor_output = params.get("monitor", {})
        anomalies = monitor_output.get("anomalies", [])
        metrics = monitor_output.get("metrics", {})

        logger.info("[%s] Diagnosing (dag=%s, node=%s), %d anomalies", self.agent_id, dag_id, node_id, len(anomalies))

        # ── LangGraph 工作流（优先），降级为规则引擎 ──
        llm_enhance = self._llm_enhance if self._llm.available else None
        wf_result = await run_diagnoser_workflow(
            self.tool_client, self._rule_diagnose, llm_enhance,
            anomalies, metrics,
        )

        diagnosis = wf_result.get("diagnosis", {})
        llm_insight = wf_result.get("llm_insight", "")
        wf_error = wf_result.get("error", "")

        if wf_error:
            logger.warning("[%s] LangGraph workflow had errors, using fallback: %s", self.agent_id, wf_error)

        result = {
            "success": True,
            "output": {
                "diagnosis": diagnosis,
                "llm_insight": llm_insight,
                "anomalies": anomalies,
                "metrics_snapshot": metrics,
                "generated_by": "langgraph" if not wf_error else "rule_fallback",
            },
        }
        return result

    # ── 规则诊断 ──────────────────────────────────────

    def _rule_diagnose(
        self, anomalies: list[dict], metrics: dict, all_metrics: dict
    ) -> dict:
        """规则引擎：按优先级匹配症状 → 返回诊断结果。"""
        if not anomalies:
            return {
                "fault_type": "none",
                "confidence": 0.95,
                "description": "未检测到异常，网络运行正常",
                "repair_action": None,
            }

        # 按 confidence 降序匹配
        for rule in sorted(SYMPTOM_RULES, key=lambda r: r["confidence"], reverse=True):
            if rule["check"](anomalies):
                return {
                    "fault_type": rule["fault_type"],
                    "confidence": rule["confidence"],
                    "description": rule["description"],
                    "repair_action": rule["repair_action"],
                }

        # 兜底
        return {
            "fault_type": "unknown",
            "confidence": 0.50,
            "description": f"检测到 {len(anomalies)} 项异常，但无法匹配已知故障模式",
            "repair_action": "clear_all_faults",
        }

    # ── LLM 增强 ──────────────────────────────────────

    async def _llm_enhance(
        self, anomalies: list[dict], metrics: dict, topology: dict, rule_diag: dict
    ) -> str:
        """使用 LLM 进行更深层的根因推理。"""
        prompt = PROMPTS.diagnoser_root_cause(anomalies, metrics, topology, rule_diag)
        return await self._llm.ask(prompt)


# ── 工厂 ─────────────────────────────────────────────

def create_diagnoser_agent(bus: MessageBus, config: dict) -> DiagnoserAgent:
    """创建 Diagnoser Agent（全局，单个实例）。"""
    return DiagnoserAgent(bus=bus, config=config)
