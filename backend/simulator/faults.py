"""网络模拟器 — 故障注入

6 种故障类型（架构方案 v2 §5.4）：
1. 链路拥塞    2. 链路中断    3. 路由器 CPU 过载
4. DDoS 攻击   5. 配置错误   6. 设备故障
"""

import time
from simulator.state import get_state, LinkState
from simulator.topology import DOMAINS, DOMAIN_NODES, get_all_links

# ── 故障类型定义 ──────────────────────────────────────────

def _get_links_for_target(target: str) -> list[LinkState]:
    """根据 target（域名或设备名）找到相关链路。"""
    state = get_state()
    links = []

    if target in DOMAINS:
        edge_router = DOMAIN_NODES[target]["edge_router"]
        for srv in DOMAIN_NODES[target]["servers"]:
            link = state.get_link(edge_router, srv)
            if link:
                links.append(link)

    if target == "Core-Router":
        for from_n, to_n, _ in get_all_links():
            if from_n == "Core-Router" or to_n == "Core-Router":
                link = state.get_link(from_n, to_n)
                if link:
                    links.append(link)

    # 直接设备名匹配
    for link in state.get_all_links():
        if link.from_node == target or link.to_node == target:
            if link not in links:
                links.append(link)

    return links


def inject_link_congestion(target: str) -> str:
    """故障1：链路拥塞 — 带宽使用率 >85%，延迟非线性增长。"""
    fid = get_state().add_fault("link_congestion", target)
    for link in _get_links_for_target(target):
        link.fault_bandwidth_util = 0.90
        link.fault_latency = link.latency_ms * 20   # 延迟飙升 20 倍
        link.fault_packet_loss = 0.05               # 丢包 5%
    return fid


def inject_link_outage(target: str) -> str:
    """故障2：链路中断 — 丢包率 100%，延迟无限大。"""
    fid = get_state().add_fault("link_outage", target)
    for link in _get_links_for_target(target):
        link.fault_packet_loss = 1.0
        link.fault_latency = 9999.0
        link.fault_bandwidth_util = 0.0
    return fid


def inject_cpu_overload(target: str) -> str:
    """故障3：路由器 CPU 过载 — 延迟抖动增大，连接数下降。"""
    fid = get_state().add_fault("cpu_overload", target)
    for link in _get_links_for_target(target):
        link.fault_latency = link.latency_ms * 8
        link.connection_count = max(1, link.connection_count // 3)
    return fid


def inject_ddos(target: str) -> str:
    """故障4：DDoS — 某台服务器的入流量暴增，带宽占满。"""
    fid = get_state().add_fault("ddos", target)
    for link in _get_links_for_target(target):
        link.fault_bandwidth_util = 1.0
        link.fault_latency = link.latency_ms * 15
    return fid


def inject_misconfig(target: str) -> str:
    """故障5：配置错误 — 路由表错误导致部分流量黑洞（丢包率上升）。"""
    fid = get_state().add_fault("misconfig", target)
    for link in _get_links_for_target(target):
        link.fault_packet_loss = 0.30
        link.fault_latency = link.latency_ms * 3
    return fid


def inject_device_failure(target: str) -> str:
    """故障6：设备故障 — 路由器完全离线。"""
    fid = get_state().add_fault("device_failure", target)
    for link in _get_links_for_target(target):
        link.fault_packet_loss = 1.0
        link.fault_latency = 9999.0
        link.fault_bandwidth_util = 0.0
    return fid


FAULT_ACTIONS = {
    "link_congestion": inject_link_congestion,
    "link_outage": inject_link_outage,
    "cpu_overload": inject_cpu_overload,
    "ddos": inject_ddos,
    "misconfig": inject_misconfig,
    "device_failure": inject_device_failure,
}


def clear_fault(fault_id: str) -> bool:
    """清除指定故障，恢复链路正常状态。"""
    removed = get_state().remove_fault(fault_id)
    if removed:
        # 清除所有链路残留的故障覆盖
        for link in get_state().get_all_links():
            link.fault_latency = None
            link.fault_packet_loss = None
            link.fault_bandwidth_util = None
        # 重新应用剩余故障
        for fid, info in get_state().faults.items():
            ftype = info["type"]
            if ftype in FAULT_ACTIONS:
                FAULT_ACTIONS[ftype](info["target"])
    return removed


def list_active_faults() -> list[dict]:
    """列出当前所有激活的故障。"""
    return [
        {"fault_id": fid, "type": info["type"], "target": info["target"],
         "injected_at_ms": info["injected_at_ms"]}
        for fid, info in get_state().faults.items()
    ]
