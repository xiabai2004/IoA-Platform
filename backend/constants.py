"""IoA 项目级常量 — 所有数值配置统一出口。

用法：
    from constants import TIMING, CACHE, THRESHOLDS
    await asyncio.sleep(TIMING.AGENT_REGISTRATION_GRACE)
"""
from __future__ import annotations
from dataclasses import dataclass


# ── 时间相关常量（秒） ──────────────────────────────────────

@dataclass(frozen=True)
class _TimingConstants:
    AGENT_REGISTRATION_GRACE: float = 2.0    # Agent 注册等待宽限
    WEBSOCKET_RECV_TIMEOUT: float = 5.0      # WS 接收超时
    SCHEDULE_INTERVAL: float = 1.0           # DAG 调度轮询间隔
    NODE_DISPATCH_TIMEOUT: float = 30.0      # 节点分发 HTTP 超时
    HEALTH_CHECK_INTERVAL: float = 15.0      # 心跳扫描间隔
    WS_RECONNECT_BACKOFF: float = 3.0        # WS 断线重连等待
    HTTP_CLIENT_TIMEOUT: float = 30.0        # 默认 HTTP 客户端超时
    DASHBOARD_WS_TIMEOUT: float = 30.0       # Dashboard WS 读超时
    SIMULATOR_PUSH_INTERVAL: float = 1.0     # Simulator WS 推送间隔
    LLM_REQUEST_TIMEOUT: float = 60.0        # LLM API 请求超时
    PROBE_TIMEOUT: float = 2.0               # 路由器探测超时


# ── 缓存相关 ────────────────────────────────────────────────

@dataclass(frozen=True)
class _CacheConstants:
    SEMANTIC_CACHE_TTL: int = 300            # 语义缓存 TTL（秒）
    SEMANTIC_CACHE_MAXSIZE: int = 128        # 语义缓存容量
    NO_AGENT_LOG_INTERVAL: int = 10          # 无 Agent 日志退避（次）
    DB_BUSY_TIMEOUT_MS: int = 5000           # SQLite busy_timeout


# ── 路由 / 评分阈值 ─────────────────────────────────────────

@dataclass(frozen=True)
class _RoutingConstants:
    BANDIT_BLEND_ALPHA: float = 0.15         # Thompson Sampling 混合比例
    UCB_EXPLORATION_C: float = 2.0           # UCB 探索系数


# ── 模拟器阈值 ──────────────────────────────────────────────

@dataclass(frozen=True)
class _SimulatorConstants:
    NOISE_LEVEL: float = 0.05                # 指标噪声幅度
    FAULT_SEVERITY_HIGH_LATENCY: float = 9999.0
    FAULT_SEVERITY_HIGH_LOSS: float = 0.99
    FAULT_CPU_OVERLOAD: float = 0.95
    FAULT_DDOS_LOSS: float = 0.30
    FAULT_DDOS_LATENCY: float = 9999.0
    FAULT_MISCONFIG_LOSS: float = 0.15
    FAULT_DEVICE_FAILURE_LOSS: float = 0.99


# ── Agent 诊断阈值 ──────────────────────────────────────────

@dataclass(frozen=True)
class _DiagnosisConstants:
    CRITICAL_LOSS_THRESHOLD: float = 0.99     # 严重丢包阈值
    CRITICAL_BW_THRESHOLD: float = 0.95       # 严重带宽阈值


TIMING = _TimingConstants()
CACHE = _CacheConstants()
ROUTING = _RoutingConstants()
SIM = _SimulatorConstants()
DIAG = _DiagnosisConstants()
