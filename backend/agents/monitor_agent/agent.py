"""Monitor Agent — 网络指标监控与异常检测

每个域部署一个 Monitor Agent，负责：
1. 采集所在域的实时网络指标
2. 按阈值检测异常（延迟/丢包/带宽使用率）
3. 将异常详情返回给调度器，供 Diagnoser 分析

工厂函数: create_monitor_agents(config) → list[MonitorAgent]
"""

from agents.base_agent import BaseAgent
from agents.tool_client import HttpToolClient, AutoToolClient, TOOL_GET_METRICS
from ioa_middleware.bus import MessageBus

# ── 异常阈值 ─────────────────────────────────────────────

ANOMALY_THRESHOLDS = {
    "latency_ms":     100.0,   # 延迟 >100ms
    "packet_loss":    0.01,    # 丢包率 >1%
    "bandwidth_util": 0.85,    # 带宽使用率 >85%
}


class MonitorAgent(BaseAgent):
    """域监控 Agent — 指标采集 + 阈值异常检测。"""

    def __init__(self, bus: MessageBus, agent_id: str, domain: str, config: dict | None = None):
        super().__init__(
            agent_id=agent_id,
            domain=domain,
            capability="monitor",
            bus=bus,
            config=config,
        )
        self.tool_client = AutoToolClient()  # 优先 MCP，降级 HTTP
        self._domain = domain

    # ── 消息处理 ──────────────────────────────────────

    async def handle_message(self, topic: str, message: dict) -> dict:
        """处理 task 消息：采集指标 → 异常检测 → 返回结果。"""
        intent = message.get("intent", {})
        if intent.get("type") != "task":
            return {"success": False, "error": "not_a_task"}

        payload = message.get("payload", {})
        dag_id = payload.get("dag_id", "")
        node_id = payload.get("node_id", "")
        correlation_id = message.get("correlation_id", "")
        params = payload.get("params", {})

        # 使用参数中的 domain，否则用本 Agent 的 domain
        target_domain = params.get("domain") or self._domain

        print(f"[{self.agent_id}] Monitoring domain={target_domain} (dag={dag_id}, node={node_id})")

        try:
            # 1. 采集指标
            metrics_resp = await self.tool_client.call_tool(
                TOOL_GET_METRICS,
                {"region": target_domain},
            )

            metrics = metrics_resp.get("metrics", {})
            if isinstance(metrics, dict) and target_domain in metrics:
                metrics = metrics[target_domain]

            # 2. 异常检测
            anomalies = self._detect_anomalies(metrics)

            result = {
                "success": True,
                "output": {
                    "domain": target_domain,
                    "metrics": metrics,
                    "anomalies": anomalies,
                    "anomaly_count": len(anomalies),
                },
            }
        except Exception as e:
            result = {
                "success": False,
                "error": str(e),
            }

        # 3. 返回结果给 orchestrator
        return result

    # ── 异常检测 ──────────────────────────────────────

    def _detect_anomalies(self, metrics: dict) -> list[dict]:
        """阈值检测：遍历各指标，标记异常。"""
        anomalies = []
        for key, threshold in ANOMALY_THRESHOLDS.items():
            value = metrics.get(key)
            if value is None:
                continue
            value = float(value)
            if value > threshold:
                anomalies.append({
                    "metric": key,
                    "value": value,
                    "threshold": threshold,
                    "severity": self._classify_severity(key, value, threshold),
                })
        return anomalies

    def _classify_severity(self, metric: str, value: float, threshold: float) -> str:
        """严重度分级。"""
        ratio = value / threshold if threshold > 0 else value
        if ratio >= 5.0:
            return "critical"
        elif ratio >= 2.0:
            return "high"
        else:
            return "medium"


# ── 工厂 ─────────────────────────────────────────────

MONITOR_DOMAINS = ["east-china", "north-china", "south-china", "west-china"]


def create_monitor_agents(bus: MessageBus, config: dict) -> list[MonitorAgent]:
    """为每个域创建一个 Monitor Agent。"""
    agents = []
    for domain in MONITOR_DOMAINS:
        agent_id = f"monitor-{domain}"
        agent = MonitorAgent(
            bus=bus,
            agent_id=agent_id,
            domain=domain,
            config=config,
        )
        agents.append(agent)
    print(f"[Factory] Created {len(agents)} Monitor Agents")
    return agents
