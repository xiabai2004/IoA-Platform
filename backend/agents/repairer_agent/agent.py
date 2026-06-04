"""Repairer Agent — differentiated fault repair execution.

Receives diagnosis from Diagnoser, selects the appropriate repair strategy
(primary → fallback), executes it, and collects post-repair metrics.
"""
from __future__ import annotations

import logging
from typing import Any

from agents.base_agent import BaseAgent
from agents.tool_client import (
    HttpToolClient,
    TOOL_GET_ALL_METRICS,
    TOOL_EXECUTE_REPAIR,
    TOOL_LIST_FAULTS,
)
from ioa_middleware.bus import MessageBus
from simulator.faults import FAULT_REPAIR_STRATEGIES

logger = logging.getLogger(__name__)


class RepairerAgent(BaseAgent):
    """Repair execution agent — selects and applies the right repair for each fault type."""

    def __init__(self, bus: MessageBus, config: dict | None = None):
        super().__init__(
            agent_id="repairer-global",
            domain="global",
            capability="repair",
            bus=bus,
            config=config,
        )
        self.tool_client = HttpToolClient()

    # ── Message handling ──────────────────────────────

    async def handle_message(self, topic: str, message: dict[str, Any]) -> dict[str, Any]:
        """Process a repair task message."""
        intent = message.get("intent", {})
        if intent.get("type") != "task":
            return {"success": False, "error": "not_a_task"}

        payload = message.get("payload", {})
        dag_id = payload.get("dag_id", "")
        node_id = payload.get("node_id", "")
        params = payload.get("params", {})

        # Extract diagnosis context from upstream (Diagnoser / Monitor)
        diagnose_output = params.get("diagnose", {})
        monitor_output = params.get("monitor", {})
        diagnosis = diagnose_output.get("diagnosis", {})
        domain = monitor_output.get("domain", "east-china")
        fault_type = diagnosis.get("fault_type", "unknown")
        anomaly_details = monitor_output.get("anomalies", [])

        # Determine the target (link/device) from anomaly data
        target = self._extract_target(anomaly_details, diagnosis, domain)

        logger.info(
            "[%s] Repairing dag=%s node=%s fault=%s target=%s domain=%s",
            self.agent_id, dag_id, node_id, fault_type, target, domain,
        )

        try:
            # 1. Check active faults
            active_faults = []
            try:
                fr = await self.tool_client.call_tool(TOOL_LIST_FAULTS, {})
                active_faults = fr.get("faults", [])
            except Exception:
                pass

            # 2. Collect pre-repair metrics
            metrics_before = {}
            try:
                mr = await self.tool_client.call_tool(TOOL_GET_ALL_METRICS, {})
                metrics_before = mr.get("metrics", {})
            except Exception:
                pass

            # 3. Execute repair (differentiated by fault type)
            if active_faults:
                repair_result = await self._execute_repair(fault_type, target, diagnosis)
            else:
                repair_result = {
                    "status": "ok",
                    "message": "No active faults found, repair skipped",
                    "skipped": True,
                }

            # 4. Collect post-repair metrics
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
                    "target": target,
                    "repair_strategy_used": repair_result.get("strategy_used", "none"),
                    "fallback_used": repair_result.get("fallback_used", False),
                    "repair_result": repair_result,
                    "metrics_before": metrics_before,
                    "metrics_after": metrics_after,
                    "active_faults_at_start": len(active_faults),
                },
            }
        except Exception as e:
            logger.exception("Repair failed for dag=%s", dag_id)
            result = {"success": False, "error": str(e)}

        return result

    # ── Repair logic ──────────────────────────────────

    def _extract_target(
        self, anomalies: list[dict], diagnosis: dict, domain: str
    ) -> str:
        """Extract the most likely target (link/device) from anomaly data."""
        # Try to get target from diagnosis context
        context = diagnosis.get("context", {})
        if context.get("target"):
            return context["target"]

        # Fall back to first anomaly's source
        if anomalies:
            first = anomalies[0]
            return first.get("link_id", first.get("device_id", f"device-{domain}"))

        return f"device-{domain}"

    async def _execute_repair(
        self, fault_type: str, target: str, diagnosis: dict
    ) -> dict:
        """Execute differentiated repair with primary/fallback strategy.

        Returns a dict with keys: status, strategy_used, fallback_used, message, details.
        """
        strategy = FAULT_REPAIR_STRATEGIES.get(fault_type)
        if not strategy:
            logger.warning("No repair strategy for fault type %r, using generic clear", fault_type)
            return await self._generic_fallback()

        primary = strategy["primary"]
        fallback = strategy.get("fallback")

        # Build repair parameters based on context
        params = self._build_repair_params(fault_type, target, diagnosis, strategy)

        # Attempt primary repair
        logger.info("Attempting primary repair: %s on %s", primary, target)
        primary_result = await self._apply_repair(primary, target, params)

        if primary_result.get("success"):
            return {
                "status": "ok",
                "strategy_used": primary,
                "fallback_used": False,
                "message": f"Primary repair ({primary}) succeeded on {target}",
                "details": primary_result,
            }

        # Attempt fallback if primary failed
        if fallback:
            logger.warning("Primary repair %s failed, trying fallback %s", primary, fallback)
            fallback_result = await self._apply_repair(fallback, target, params)

            if fallback_result.get("success"):
                return {
                    "status": "ok",
                    "strategy_used": fallback,
                    "fallback_used": True,
                    "message": f"Fallback repair ({fallback}) succeeded on {target} (primary {primary} failed)",
                    "details": fallback_result,
                }

        # Both failed
        return {
            "status": "error",
            "strategy_used": primary,
            "fallback_used": False,
            "message": f"All repair strategies failed for {fault_type} on {target}",
        }

    def _build_repair_params(
        self, fault_type: str, target: str, diagnosis: dict, strategy: dict
    ) -> dict:
        """Build parameters for the repair action based on fault type and context."""
        context = diagnosis.get("context", {})

        if fault_type == "link_congestion":
            return {
                "max_bandwidth": 0.7,
                "backup_link_id": context.get("alternate_link", f"{target}-backup"),
            }

        elif fault_type == "link_outage":
            return {
                "standby_link_id": context.get("standby_link", f"{target}-standby"),
                "backup_link_id": context.get("alternate_link", f"{target}-backup"),
            }

        elif fault_type == "cpu_overload":
            return {
                "service_name": context.get("affected_service", "bgpd"),
            }

        elif fault_type == "ddos":
            return {
                "rules": [
                    "deny ip any any established",
                    "rate-limit icmp 1000",
                    "drop tcp syn flood threshold 10000",
                ],
            }

        elif fault_type == "misconfig":
            return {
                "service_name": "configd",
            }

        elif fault_type == "device_failure":
            return {
                "standby_link_id": f"{target}-standby",
                "backup_link_id": f"{target}-backup",
            }

        return {}

    async def _apply_repair(
        self, action_type: str, target: str, params: dict
    ) -> dict:
        """Apply a single repair action via the simulator API."""
        try:
            result = await self.tool_client.call_tool(TOOL_EXECUTE_REPAIR, {
                "action_type": action_type,
                "target": target,
                "params": params,
            })
            # The API returns {"status": "applied", ...} or raises HTTPException
            if isinstance(result, dict) and result.get("status") == "applied":
                return {"success": True, "action": action_type, "response": result}
            return {"success": True, "action": action_type, "response": result}
        except Exception as e:
            logger.exception("Repair action %s failed on %s", action_type, target)
            return {"success": False, "action": action_type, "error": str(e)}

    async def _generic_fallback(self) -> dict:
        """Legacy fallback: clear all faults (kept for unknown fault types)."""
        try:
            from agents.tool_client import TOOL_CLEAR_ALL_FAULTS
            result = await self.tool_client.call_tool(TOOL_CLEAR_ALL_FAULTS, {})
            return {
                "status": "ok",
                "strategy_used": "generic_clear",
                "fallback_used": False,
                "message": "Generic fault clear executed (no specific strategy)",
                "details": result,
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}


# ── Factory ─────────────────────────────────────────

def create_repairer_agent(bus: MessageBus, config: dict) -> RepairerAgent:
    """Create a Repairer Agent (global, single instance)."""
    return RepairerAgent(bus=bus, config=config)
