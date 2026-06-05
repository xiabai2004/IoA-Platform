"""DAG 调度器 — Pydantic 模型

定义 DAG、节点、调度状态的数据结构。
"""

from __future__ import annotations
from pydantic import BaseModel, Field
from typing import Literal
from enum import Enum


# ── 状态枚举 ──────────────────────────────────────────────

class DagStatus(str, Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class NodeStatus(str, Enum):
    pending = "pending"
    assigned = "assigned"
    running = "running"
    completed = "completed"
    failed = "failed"
    retrying = "retrying"


# ── DAG 节点定义 ──────────────────────────────────────────

class DagNodeDef(BaseModel):
    """DAG 单个节点的定义。"""
    node_id: str = Field(..., pattern=r"^[a-z0-9_-]{2,64}$")
    type: str = Field(..., description="节点类型: monitor / diagnose / repair / verify")
    capability: str = Field(..., description="匹配 Agent 能力标签")
    domain: str | None = Field(None, description="目标域，null = 全局")
    depends_on: list[str] = Field(default_factory=list, description="依赖节点 ID 列表")
    params: dict = Field(default_factory=dict, description="节点参数（注入到 Agent 任务）")
    max_retries: int = Field(default=2, ge=0, le=5)
    timeout_ms: int = Field(default=60_000, ge=5_000, le=300_000)


# ── DAG 定义 ─────────────────────────────────────────────

class DagDefinition(BaseModel):
    """DAG 任务定义。"""
    model_config = {"extra": "allow"}
    dag_id: str = Field(..., pattern=r"^[a-z0-9_-]{3,64}$")
    correlation_id: str | None = Field(None, description="关联 ID，用于审计追踪")
    nodes: list[DagNodeDef] = Field(..., min_length=1)
    description: str | None = None


# ── 调度器内部状态 ────────────────────────────────────────

class DagNodeState(BaseModel):
    """DAG 节点运行时状态（持久化到 dag_nodes 表）。"""
    dag_id: str
    node_id: str
    node_type: str
    capability: str
    domain: str | None = None
    params: dict = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)
    max_retries: int = 2
    timeout_ms: int = 60_000
    status: NodeStatus = NodeStatus.pending
    assigned_agent: str | None = None
    retry_count: int = 0
    output: dict | None = None
    started_at_ms: int | None = None
    finished_at_ms: int | None = None


class DagState(BaseModel):
    """DAG 运行时状态。"""
    dag_id: str
    correlation_id: str | None = None
    description: str | None = None
    status: DagStatus = DagStatus.pending
    nodes: dict[str, DagNodeState] = Field(default_factory=dict)
    submitted_at_ms: int = 0
    finished_at_ms: int | None = None
    result: dict | None = None


# ── 审计事件类型 ──────────────────────────────────────────

AuditEventType = Literal[
    "dag.submitted",
    "dag.started",
    "dag.completed",
    "dag.failed",
    "dag.cancelled",
    "node.assigned",
    "node.started",
    "node.completed",
    "node.failed",
    "node.retrying",
    "verify.pass",
    "verify.fail",
    "verify.retry",
]
