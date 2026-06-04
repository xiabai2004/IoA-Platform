"""网络模拟器 — 指标生成器

架构方案 v2 §5.3 流量模型：
- 00:00-08:00 低流量（泊松到达）
- 08:00-18:00 正常工作（韦伯突发）
- 18:00-24:00 高峰（帕累托重尾）

每秒更新一次所有链路的指标，添加 5% 随机噪声。
"""

import asyncio
import math
import random
import time
from simulator.state import get_state
from simulator.topology import DOMAINS, DOMAIN_NODES, get_all_links

NOISE_LEVEL = 0.05   # ±5% 噪声


def _get_hour_multiplier() -> float:
    """根据当前小时返回流量倍率。"""
    h = time.localtime().tm_hour
    if 0 <= h < 8:
        return 0.4   # 低流量
    elif 8 <= h < 18:
        return 1.0   # 正常
    else:
        return 1.6   # 高峰


def _add_noise(value: float, noise_level: float = NOISE_LEVEL) -> float:
    """添加随机噪声，保持非负。"""
    factor = 1.0 + random.uniform(-noise_level, noise_level)
    return max(0.0, value * factor)


def _generate_latency_ms(base: float = 10.0) -> float:
    """生成延迟值（ms），基线 5-20ms。"""
    hour_mult = _get_hour_multiplier()
    # 基础延迟 + 流量引起的增量 + 随机抖动
    val = base * (0.8 + 0.4 * hour_mult) + random.uniform(-2, 2)
    return _add_noise(max(1.0, val))


def _generate_packet_loss(base: float = 0.001) -> float:
    """生成丢包率，基线 <0.1%。"""
    hour_mult = _get_hour_multiplier()
    val = base * (0.5 + hour_mult) + random.uniform(0, 0.0005)
    return min(_add_noise(val), 1.0)


def _generate_bandwidth_util(base: float = 0.40) -> float:
    """生成带宽使用率，基线 30-60%。"""
    hour_mult = _get_hour_multiplier()
    val = base * (0.6 + 0.8 * hour_mult) + random.uniform(-0.05, 0.05)
    return max(0.01, min(_add_noise(val), 1.0))


def _generate_throughput_mbps(bandwidth_util: float, bandwidth_gbps: float) -> float:
    """根据带宽使用率计算吞吐量 Mbps。"""
    return bandwidth_util * bandwidth_gbps * 1000 * _add_noise(1.0, 0.02)


def _generate_connections(base: int = 50) -> int:
    """生成连接数。"""
    hour_mult = _get_hour_multiplier()
    val = int(base * (0.6 + 0.8 * hour_mult) + random.randint(-10, 15))
    return max(5, val)


def update_all_links() -> None:
    """更新所有链路的运行时指标（每秒调用一次）。"""
    state = get_state()
    for link in state.get_all_links():
        # 如果没有激活故障，正常生成指标
        if not link.has_active_fault():
            link.latency_ms = _generate_latency_ms()
            link.packet_loss = _generate_packet_loss()
            link.bandwidth_util = _generate_bandwidth_util()
        link.throughput_mbps = _generate_throughput_mbps(
            link.get_effective_bandwidth_util(), link.bandwidth_gbps
        )
        link.connection_count = _generate_connections()


def get_domain_metrics(domain: str) -> dict:
    """聚合某个域的所有链路指标，返回域级别汇总数据。"""
    state = get_state()
    if domain not in DOMAINS:
        return {}

    edge_router = DOMAIN_NODES[domain]["edge_router"]
    servers = DOMAIN_NODES[domain]["servers"]

    latencies = []
    packet_losses = []
    bw_utils = []
    throughputs = []
    connections = 0

    for srv in servers:
        link = state.get_link(edge_router, srv)
        if link:
            latencies.append(link.get_effective_latency())
            packet_losses.append(link.get_effective_packet_loss())
            bw_utils.append(link.get_effective_bandwidth_util())
            throughputs.append(link.throughput_mbps)
            connections += link.connection_count

    if not latencies:
        return {"domain": domain, "latency_ms": 0, "packet_loss": 0, "bandwidth_util": 0,
                "throughput_mbps": 0, "connection_count": 0}

    return {
        "domain": domain,
        "latency_ms": round(sum(latencies) / len(latencies), 2),
        "packet_loss": round(sum(packet_losses) / len(packet_losses), 5),
        "bandwidth_util": round(sum(bw_utils) / len(bw_utils), 4),
        "throughput_mbps": round(sum(throughputs) / len(throughputs), 2),
        "connection_count": connections,
    }


def get_all_domain_metrics() -> dict[str, dict]:
    """返回所有域的汇总指标。"""
    return {d: get_domain_metrics(d) for d in DOMAINS}


async def generator_loop(interval_ms: int = 1000) -> None:
    """后台无限循环：按时更新所有链路指标。"""
    while True:
        try:
            update_all_links()
        except Exception:
            pass  # 静默容错，不打印
        await asyncio.sleep(interval_ms / 1000.0)
