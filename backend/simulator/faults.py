"""Fault injection and differentiated repair actions for the network simulator.

Provides 6 fault types, each with a primary + fallback repair strategy.
The old ``clear_fault`` is kept only for cleanup/reset operations.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from .state import get_state

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Fault injection
# ---------------------------------------------------------------------------

def inject_link_congestion(link_id: str, severity: str = "high") -> dict:
    """Inject link congestion on a specific link."""
    state = get_state()
    link = state.get_link(link_id)
    if not link:
        return {"success": False, "error": f"Link {link_id} not found"}

    mul = {"low": 3.0, "medium": 8.0, "high": 15.0}.get(severity, 15.0)
    link.fault_latency = max(150.0, (link.latency_ms or 50) * mul)  # 至少 150ms，确保超过阈值
    link.fault_bandwidth_util = min(1.0, max(0.90, (link.bandwidth_util or 0.4) + 0.55))  # 至少 90%

    fault_id = state.add_fault("link_congestion", link_id, {"severity": severity})
    logger.info("Injected link_congestion on %s (severity=%s)", link_id, severity)
    return {"success": True, "fault_id": fault_id}


def inject_link_outage(link_id: str) -> dict:
    """Inject total link outage (100% packet loss)."""
    state = get_state()
    link = state.get_link(link_id)
    if not link:
        return {"success": False, "error": f"Link {link_id} not found"}

    link.fault_packet_loss = 1.0
    link.fault_latency = 9999.0
    link.fault_bandwidth_util = 0.99

    fault_id = state.add_fault("link_outage", link_id)
    logger.info("Injected link_outage on %s", link_id)
    return {"success": True, "fault_id": fault_id}


def inject_cpu_overload(device_id: str, load: float = 0.95) -> dict:
    """Inject CPU overload on a device — causes elevated latency on connected links."""
    state = get_state()
    affected = 0
    for link in state.get_all_links():
        if link.from_node == device_id or link.to_node == device_id:
            # Scale latency to be clearly detectable (>100ms monitor threshold, >80ms diagnosis)
            base = (link.latency_ms or 50)
            link.fault_latency = max(120.0, base * 12.0)  # ensure >100ms for anomaly detection
            affected += 1

    fault_id = state.add_fault("cpu_overload", device_id, {"load": load})
    logger.info("Injected cpu_overload on %s (load=%.2f, affected_links=%d)", device_id, load, affected)
    return {"success": True, "fault_id": fault_id}


def inject_ddos(target_id: str, attack_type: str = "syn_flood") -> dict:
    """Inject DDoS attack — saturates bandwidth and causes packet loss."""
    state = get_state()
    affected = 0
    for link in state.get_all_links():
        if link.from_node == target_id or link.to_node == target_id:
            link.fault_bandwidth_util = 0.99
            link.fault_packet_loss = 0.30
            link.fault_latency = (link.latency_ms or 50) * 10.0
            affected += 1

    fault_id = state.add_fault("ddos", target_id, {"attack_type": attack_type})
    logger.info("Injected ddos on %s (type=%s, affected_links=%d)", target_id, attack_type, affected)
    return {"success": True, "fault_id": fault_id}


def inject_misconfig(device_id: str, config_error: str = "bgp_metric") -> dict:
    """Inject misconfiguration — causes moderate packet loss."""
    state = get_state()
    affected = 0
    for link in state.get_all_links():
        if link.from_node == device_id or link.to_node == device_id:
            link.fault_packet_loss = 0.15
            link.fault_latency = (link.latency_ms or 50) * 2.0
            affected += 1

    fault_id = state.add_fault("misconfig", device_id, {"config_error": config_error})
    logger.info("Injected misconfig on %s (error=%s, affected_links=%d)", device_id, config_error, affected)
    return {"success": True, "fault_id": fault_id}


def inject_device_failure(device_id: str) -> dict:
    """Inject device failure — all connected links lose connectivity."""
    state = get_state()
    affected = 0
    for link in state.get_all_links():
        if link.from_node == device_id or link.to_node == device_id:
            link.fault_packet_loss = 1.0
            link.fault_latency = 9999.0
            affected += 1

    fault_id = state.add_fault("device_failure", device_id)
    logger.info("Injected device_failure on %s (affected_links=%d)", device_id, affected)
    return {"success": True, "fault_id": fault_id}


# ---------------------------------------------------------------------------
# Differentiated repair actions
# ---------------------------------------------------------------------------

@dataclass
class RepairAction:
    """A concrete repair action that can be applied to the simulated network."""
    action_type: str
    target: str
    params: dict[str, Any] = field(default_factory=dict)


def _clear_device_faults(device_id: str) -> int:
    """Clear fault overlays from all links connected to a device. Returns count of links cleared."""
    state = get_state()
    count = 0
    for link in state.get_all_links():
        if link.from_node == device_id or link.to_node == device_id:
            link.fault_latency = None
            link.fault_packet_loss = None
            link.fault_bandwidth_util = None
            count += 1
    return count


def _apply_route_switch(link_id: str, backup_link_id: str = "", **kwargs) -> dict:
    """Switch traffic from primary link to backup link (route change)."""
    state = get_state()
    link = state.get_link(link_id)
    backup = state.get_link(backup_link_id)

    if link:
        link.bandwidth_util *= 0.1
        link.fault_latency = None
        link.fault_packet_loss = None
        link.fault_bandwidth_util = None
    else:
        # Device-targeted fallback: clear all links connected to the device
        _clear_device_faults(link_id)

    if backup:
        backup.bandwidth_util = min(1.0, backup.bandwidth_util + 0.2)

    cleared = _clear_faults_for_target(link_id)
    logger.info("Route switch: %s → %s (cleared %d faults)", link_id, backup_link_id, cleared)
    return {"success": True, "action": "route_switch", "primary": link_id, "backup": backup_link_id}


def _apply_acl_deploy(device_id: str, rules: list[str] | None = None, **kwargs) -> dict:
    """Deploy ACL rules to filter attack traffic (DDoS mitigation)."""
    state = get_state()
    rules = rules or ["deny ip any any established", "rate-limit icmp 1000"]

    for link in state.get_all_links():
        if link.from_node == device_id or link.to_node == device_id:
            # 清除所有故障覆盖
            link.fault_latency = None
            link.fault_packet_loss = None
            link.fault_bandwidth_util = None

    # 清除故障记录
    cleared = _clear_faults_for_target(device_id)
    logger.info("ACL deployed on %s (%d rules, cleared %d faults)", device_id, len(rules), cleared)
    return {"success": True, "action": "acl_deploy", "device": device_id, "rules_applied": len(rules)}


def _clear_faults_for_target(target: str) -> int:
    """清除指定目标的所有故障记录。返回清除的故障数量。"""
    state = get_state()
    to_remove = [fid for fid, info in state.faults.items() if info.get("target") == target]
    for fid in to_remove:
        del state.faults[fid]
    return len(to_remove)


def _apply_traffic_shaping(link_id: str, max_bandwidth: float = 0.7, **kwargs) -> dict:
    """Apply traffic shaping/QoS to relieve congestion."""
    state = get_state()
    link = state.get_link(link_id)
    if link:
        link.bandwidth_util = min(link.bandwidth_util, max_bandwidth)
        link.fault_latency = None
        link.fault_bandwidth_util = None
    else:
        # Device-targeted fallback: clear all links connected to the device
        _clear_device_faults(link_id)

    cleared = _clear_faults_for_target(link_id)
    logger.info("Traffic shaping applied on %s (max=%.0f%%, cleared %d faults)", link_id, max_bandwidth * 100, cleared)
    return {"success": True, "action": "traffic_shape", "link": link_id, "max_bandwidth": max_bandwidth}


def _apply_link_failover(link_id: str, standby_link_id: str = "", **kwargs) -> dict:
    """Fail over to a standby link when primary link fails."""
    state = get_state()
    failed = state.get_link(link_id)
    standby = state.get_link(standby_link_id) if standby_link_id else None

    if failed:
        failed.fault_latency = None
        failed.fault_packet_loss = None
        failed.fault_bandwidth_util = None
    else:
        # Device-targeted fallback: clear all links connected to the device
        _clear_device_faults(link_id)

    if standby:
        standby.bandwidth_util = min(1.0, standby.bandwidth_util + 0.15)

    cleared = _clear_faults_for_target(link_id)
    logger.info("Link failover: %s → %s (cleared %d faults)", link_id, standby_link_id, cleared)
    return {"success": True, "action": "link_failover", "failed_link": link_id, "standby_link": standby_link_id}


def _apply_restart_service(device_id: str, service_name: str = "bgpd", **kwargs) -> dict:
    """Restart a service/interface on a device (recovery from CPU overload or misconfig)."""
    state = get_state()
    for link in state.get_all_links():
        if link.from_node == device_id or link.to_node == device_id:
            # 清除所有故障覆盖
            link.fault_latency = None
            link.fault_packet_loss = None
            link.fault_bandwidth_util = None

    # 清除故障记录
    cleared = _clear_faults_for_target(device_id)
    logger.info("Service %s restarted on %s (cleared %d faults)", service_name, device_id, cleared)
    return {"success": True, "action": "restart_service", "device": device_id, "service": service_name}


# Repair handler registry
REPAIR_HANDLERS = {
    "route_switch":    _apply_route_switch,
    "acl_deploy":      _apply_acl_deploy,
    "traffic_shape":   _apply_traffic_shaping,
    "link_failover":   _apply_link_failover,
    "restart_service": _apply_restart_service,
}

# ---------------------------------------------------------------------------
# Fault → Repair strategy mapping (primary + fallback)
# ---------------------------------------------------------------------------

FAULT_REPAIR_STRATEGIES = {
    "link_congestion": {
        "primary": "traffic_shape",
        "fallback": "route_switch",
        "description": "QoS traffic shaping, fallback to route switching",
    },
    "link_outage": {
        "primary": "link_failover",
        "fallback": "route_switch",
        "description": "Link failover to standby, fallback to route switch",
    },
    "cpu_overload": {
        "primary": "restart_service",
        "fallback": "traffic_shape",
        "description": "Service restart, fallback to traffic shaping",
    },
    "ddos": {
        "primary": "acl_deploy",
        "fallback": "traffic_shape",
        "description": "ACL rule deployment, fallback to traffic shaping",
    },
    "misconfig": {
        "primary": "restart_service",
        "fallback": "acl_deploy",
        "description": "Service restart to reset config, fallback to ACL",
    },
    "device_failure": {
        "primary": "link_failover",
        "fallback": "route_switch",
        "description": "Link failover around failed device, fallback to route switch",
    },
}

# ---------------------------------------------------------------------------
# Fault action registry (used by API for injection)
# ---------------------------------------------------------------------------

FAULT_ACTIONS = {
    "link_congestion": inject_link_congestion,
    "link_outage": inject_link_outage,
    "cpu_overload": inject_cpu_overload,
    "ddos": inject_ddos,
    "misconfig": inject_misconfig,
    "device_failure": inject_device_failure,
}

# ---------------------------------------------------------------------------
# Legacy clear_fault — kept for cleanup only
# ---------------------------------------------------------------------------

def clear_fault(fault_id: str) -> bool:
    """Clear a specific fault by ID. Kept for cleanup/reset only.

    For actual repairs, use the specific repair actions above via FAULT_REPAIR_STRATEGIES.
    """
    return get_state().clear_all_faults()


def get_fault_summary() -> dict:
    """Return a summary of current active faults and their repair strategies."""
    state = get_state()
    faults = []
    for fid, info in state.faults.items():
        ftype = info.get("type", "unknown")
        strategy = FAULT_REPAIR_STRATEGIES.get(ftype, {})
        faults.append({
            "fault_id": fid,
            "type": ftype,
            "target": info.get("target", ""),
            "primary_repair": strategy.get("primary", "clear_fault"),
            "fallback_repair": strategy.get("fallback"),
        })
    return {"active_faults": len(faults), "faults": faults}


def list_active_faults() -> list[dict]:
    """列出当前所有激活的故障。"""
    return [
        {"fault_id": fid, "type": info["type"], "target": info["target"],
         "injected_at_ms": info["injected_at_ms"]}
        for fid, info in get_state().faults.items()
    ]
