"""Diagnoser Agent — 根因分析

接收 Monitor 采集的异常指标 + 拓扑数据，
通过规则引擎（或 LLM 增强）确定故障类型和根因。

工厂函数: create_diagnoser_agent(config) → DiagnoserAgent
"""

import json
from agents.base_agent import BaseAgent
from agents.tool_client import HttpToolClient, TOOL_GET_ALL_METRICS, TOOL_GET_TOPOLOGY
from agents.llm_client import get_llm_client
from ioa_middleware.bus import MessageBus

# ── 症状 → 故障类型映射 ─────────────────────────────────

SYMPTOM_RULES = [
    # (条件函数, 故障类型, 修复建议)
    {
        "name": "link_outage_or_device_failure",
        "check": lambda a: any(
            m["metric"] == "packet_loss" and m["value"] >= 0.99
            for m in a
        ),
        "fault_type": "device_failure",
        "confidence": 0.90,
        "description": "完全丢包，疑似链路中断或设备离线",
        "repair_action": "link_failover",
    },
    {
        "name": "ddos_attack",
        "check": lambda a: any(
            m["metric"] == "bandwidth_util" and m["value"] >= 0.95
            and m.get("severity") in ("high", "critical")
            for m in a
        ),
        "fault_type": "ddos",
        "confidence": 0.85,
        "description": "带宽利用率接近100%，疑似DDoS攻击",
        "repair_action": "acl_deploy",
    },
    {
        "name": "link_congestion",
        "check": lambda a: any(
            m["metric"] == "latency_ms" and m["severity"] in ("high", "critical")
            for m in a
        ) and not any(
            m["metric"] == "packet_loss" and m["value"] >= 0.99
            for m in a
        ),
        "fault_type": "link_congestion",
        "confidence": 0.80,
        "description": "延迟显著升高但未完全中断，疑似链路拥塞",
        "repair_action": "traffic_shape",
    },
    {
        "name": "cpu_overload",
        "check": lambda a: any(
            m["metric"] == "latency_ms" and m["severity"] == "medium"
            for m in a
        ),
        "fault_type": "cpu_overload",
        "confidence": 0.70,
        "description": "延迟中度升高，疑似路由器CPU过载",
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
        self.tool_client = HttpToolClient()
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

        print(f"[{self.agent_id}] Diagnosing (dag={dag_id}, node={node_id}), {len(anomalies)} anomalies")

        try:
            # 1. 获取上下文数据
            all_metrics = {}
            topology = {}
            try:
                mr = await self.tool_client.call_tool(TOOL_GET_ALL_METRICS, {})
                all_metrics = mr.get("metrics", {})
            except Exception:
                pass
            try:
                tr = await self.tool_client.call_tool(TOOL_GET_TOPOLOGY, {})
                topology = tr
            except Exception:
                pass

            # 2. 规则引擎诊断
            diagnosis = self._rule_diagnose(anomalies, metrics, all_metrics)

            # 3. LLM 增强（可选）
            llm_insight = ""
            if self._llm.available and anomalies:
                llm_insight = await self._llm_enhance(anomalies, metrics, topology, diagnosis)

            result = {
                "success": True,
                "output": {
                    "diagnosis": diagnosis,
                    "llm_insight": llm_insight,
                    "anomalies": anomalies,
                    "metrics_snapshot": metrics,
                },
            }
        except Exception as e:
            result = {
                "success": False,
                "error": str(e),
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
        prompt = f"""你是网络运维专家。请分析以下异常并给出根因判断。

## 异常指标
{json.dumps(anomalies, ensure_ascii=False, indent=2)}

## 当前域指标
{json.dumps(metrics, ensure_ascii=False, indent=2)}

## 拓扑概要
{json.dumps(topology, ensure_ascii=False, indent=2)[:800]}

## 规则引擎初步判断
{json.dumps(rule_diag, ensure_ascii=False, indent=2)}

请用 2-3 句话给出根因分析和建议。"""
        return await self._llm.ask(prompt)


# ── 工厂 ─────────────────────────────────────────────

def create_diagnoser_agent(bus: MessageBus, config: dict) -> DiagnoserAgent:
    """创建 Diagnoser Agent（全局，单个实例）。"""
    return DiagnoserAgent(bus=bus, config=config)
