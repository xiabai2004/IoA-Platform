"""Repairer Agent — 故障修复执行

接收 Diagnoser 的诊断结果，执行修复操作，
并采集修复后指标供 Verifier 闭环验证。

v2: retry 轮次智能处理 — 如果故障已清空则跳过修复，避免无效操作

工厂函数: create_repairer_agent(config) → RepairerAgent
"""

from agents.base_agent import BaseAgent
from agents.tool_client import (
    HttpToolClient,
    TOOL_GET_ALL_METRICS,
    TOOL_CLEAR_ALL_FAULTS,
    TOOL_LIST_FAULTS,
)


class RepairerAgent(BaseAgent):
    """修复执行 Agent — 清除故障 + 验证修复效果。"""

    def __init__(self, agent_id: str = "repairer-global", tool_client=None, config: dict | None = None):
        super().__init__(
            agent_id=agent_id,
            domain="global",
            capabilities=["repair"],
            tool_client=tool_client or HttpToolClient(),
            description="全局故障修复执行 Agent，清除故障并采集修复后指标",
            supported_tasks=["fault_remediation", "fault_clearance"],
        )

    # ── 消息处理 ──────────────────────────────────────

    async def handle_message(self, msg: dict) -> None:
        """处理 task 消息：读取诊断 → 执行修复 → 采集修复后指标 → 返回结果。"""
        intent = msg.get("intent", {})
        if intent.get("type") != "task":
            return

        payload = msg.get("payload", {})
        dag_id = payload.get("dag_id", "")
        node_id = payload.get("node_id", "")
        correlation_id = msg.get("correlation_id", "")
        params = payload.get("params", {})

        # 从前置 Diagnoser / Monitor 结果中获取上下文
        diagnose_output = params.get("diagnose", {})
        monitor_output = params.get("monitor", {})
        diagnosis = diagnose_output.get("diagnosis", {})
        domain = monitor_output.get("domain", "east-china")
        fault_type = diagnosis.get("fault_type", "unknown")
        repair_action = diagnosis.get("repair_action")

        print(f"[{self.agent_id}] Repairing (dag={dag_id}, node={node_id}), "
              f"fault={fault_type}, domain={domain}")

        try:
            # 1. 先检查当前是否有活跃故障
            active_faults = []
            try:
                fr = await self.tool_client.call_tool(TOOL_LIST_FAULTS, {})
                active_faults = fr.get("faults", [])
            except Exception:
                pass

            # 2. 采集修复前指标（仅在有故障时才有意义）
            metrics_before = {}
            try:
                mr = await self.tool_client.call_tool(TOOL_GET_ALL_METRICS, {})
                metrics_before = mr.get("metrics", {})
            except Exception:
                pass

            # 3. 执行修复（如果故障已清空则跳过）
            if active_faults:
                repair_result = await self._execute_repair(repair_action, domain)
            else:
                repair_result = {
                    "status": "ok",
                    "message": "No active faults found, repair skipped (likely cleared in previous round)",
                    "skipped": True,
                }

            # 4. 采集修复后指标
            metrics_after = {}
            try:
                mr = await self.tool_client.call_tool(TOOL_GET_ALL_METRICS, {})
                metrics_after = mr.get("metrics", {})
            except Exception:
                pass

            result = {
                "success": repair_result.get("status") == "ok",
                "output": {
                    "domain": domain,
                    "fault_type": fault_type,
                    "repair_action": repair_action if active_faults else "skipped_no_faults",
                    "repair_result": repair_result,
                    "metrics_before": metrics_before,
                    "metrics_after": metrics_after,
                    "active_faults_at_start": len(active_faults),
                },
            }
        except Exception as e:
            result = {
                "success": False,
                "error": str(e),
            }

        await self.send_result(correlation_id, dag_id, node_id, result)

    # ── 修复执行 ──────────────────────────────────────

    async def _execute_repair(self, repair_action: str | None, domain: str) -> dict:
        """执行修复操作。

        支持的动作：
            - clear_all_faults / clear_fault: 清除模拟器所有故障
            - None:            无操作（正常状态）
        """
        if not repair_action:
            return {"status": "ok", "message": "No repair needed"}

        # 1. 列出当前激活故障
        faults_info = {}
        try:
            fr = await self.tool_client.call_tool(TOOL_LIST_FAULTS, {})
            faults_info = fr
        except Exception:
            pass

        # 2. 清除所有故障
        try:
            result = await self.tool_client.call_tool(TOOL_CLEAR_ALL_FAULTS, {})
            result["cleared_faults"] = faults_info.get("faults", [])
            return result
        except Exception as e:
            return {"status": "error", "message": str(e)}


# ── 工厂 ─────────────────────────────────────────────

def create_repairer_agent(config: dict) -> RepairerAgent:
    """创建 Repairer Agent（全局，单个实例）。"""
    return RepairerAgent(config=config)
