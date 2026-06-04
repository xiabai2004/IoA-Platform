"""网络模拟器 — 运行时状态管理

管理每条链路的当前状态：延迟、丢包率、带宽使用率、吞吐量。
"""

import asyncio
import time
from dataclasses import dataclass, field


@dataclass
class LinkState:
    """单条链路的运行时状态。"""
    from_node: str
    to_node: str
    bandwidth_gbps: float
    latency_ms: float = 10.0         # 当前延迟 ms
    packet_loss: float = 0.001       # 丢包率 0~1
    bandwidth_util: float = 0.40     # 带宽使用率 0~1
    throughput_mbps: float = 400.0   # 当前吞吐量 Mbps
    connection_count: int = 50       # 连接数

    # 故障覆盖值（非 None 时覆盖正常的指标生成）
    fault_latency: float | None = None
    fault_packet_loss: float | None = None
    fault_bandwidth_util: float | None = None

    def get_effective_latency(self) -> float:
        return self.fault_latency if self.fault_latency is not None else self.latency_ms

    def get_effective_packet_loss(self) -> float:
        return self.fault_packet_loss if self.fault_packet_loss is not None else self.packet_loss

    def get_effective_bandwidth_util(self) -> float:
        return self.fault_bandwidth_util if self.fault_bandwidth_util is not None else self.bandwidth_util

    def has_active_fault(self) -> bool:
        return (self.fault_latency is not None or
                self.fault_packet_loss is not None or
                self.fault_bandwidth_util is not None)


class SimulatorState:
    """模拟器全局状态。"""

    def __init__(self, links: list[tuple[str, str, float]]):
        self.links: dict[str, LinkState] = {}
        self.faults: dict[str, dict] = {}      # fault_id → fault_info
        self._fault_counter = 0
        for from_n, to_n, bw in links:
            key = f"{from_n}->{to_n}"
            self.links[key] = LinkState(from_node=from_n, to_node=to_n, bandwidth_gbps=bw)

    def get_link(self, from_node: str, to_node: str | None = None) -> LinkState | None:
        """Get a link by composite key "from->to" (one arg) or (from, to) pair."""
        if to_node is None:
            return self.links.get(from_node)
        return self.links.get(f"{from_node}->{to_node}")

    def get_all_links(self) -> list[LinkState]:
        return list(self.links.values())

    def add_fault(self, fault_type: str, target: str, params: dict | None = None) -> str:
        """注入故障，返回 fault_id。"""
        self._fault_counter += 1
        fid = f"fault-{self._fault_counter}"
        self.faults[fid] = {
            "type": fault_type,
            "target": target,
            "params": params or {},
            "injected_at_ms": int(time.time() * 1000),
        }
        return fid

    def remove_fault(self, fault_id: str) -> bool:
        """清除故障。"""
        if fault_id in self.faults:
            del self.faults[fault_id]
            return True
        return False

    def clear_all_faults(self) -> bool:
        """清除所有故障，恢复所有链路正常状态。返回是否有故障被清除。"""
        had_faults = len(self.faults) > 0
        self.faults.clear()
        for link in self.links.values():
            link.fault_latency = None
            link.fault_packet_loss = None
            link.fault_bandwidth_util = None
        return had_faults

    def clear_faults_by_type(self, fault_type: str) -> int:
        """Clear all faults of a specific type. Returns count removed."""
        removed = 0
        for fid in list(self.faults.keys()):
            if self.faults[fid].get("type") == fault_type:
                self.remove_fault(fid)
                removed += 1
        return removed

    def reset_all_links(self) -> None:
        """Reset all link fault overlays to None."""
        for link in self.get_all_links():
            link.fault_latency = None
            link.fault_packet_loss = None
            link.fault_bandwidth_util = None


# 全局单例
_state: SimulatorState | None = None


def get_state() -> SimulatorState:
    global _state
    if _state is None:
        raise RuntimeError("SimulatorState not initialized.")
    return _state


def init_state(links: list[tuple[str, str, float]]) -> SimulatorState:
    global _state
    _state = SimulatorState(links)
    return _state
