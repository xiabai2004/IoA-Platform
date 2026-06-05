"""DAG 调度器 — 持久化存储层

操作 dags / dag_nodes / audit_logs / verifications 四张表。
"""

import json
import time
from ioa_middleware.db import execute, fetch_all, fetch_one
from ioa_middleware.orchestrator.models import (
    DagDefinition,
    DagNodeDef,
    DagNodeState,
    DagState,
    DagStatus,
    NodeStatus,
    AuditEventType,
)


# ═══════════════════════════════════════════════════════════
#  DAG
# ═══════════════════════════════════════════════════════════

async def create_dag(dag_def: DagDefinition) -> None:
    """创建 DAG 记录。"""
    await execute(
        """INSERT INTO dags (dag_id, correlation_id, definition, status, submitted_at_ms)
           VALUES (?, ?, ?, ?, ?)""",
        (
            dag_def.dag_id,
            dag_def.correlation_id,
            json.dumps(dag_def.model_dump()),
            DagStatus.pending.value,
            int(time.time() * 1000),
        ),
    )


async def update_dag_status(dag_id: str, status: DagStatus, result: dict | None = None) -> None:
    """更新 DAG 状态。"""
    if status in (DagStatus.completed, DagStatus.failed):
        await execute(
            "UPDATE dags SET status = ?, finished_at_ms = ?, result = ? WHERE dag_id = ?",
            (status.value, int(time.time() * 1000), json.dumps(result) if result else None, dag_id),
        )
    else:
        await execute(
            "UPDATE dags SET status = ? WHERE dag_id = ?",
            (status.value, dag_id),
        )


async def get_dag(dag_id: str) -> dict | None:
    """获取 DAG 记录。"""
    return await fetch_one("SELECT * FROM dags WHERE dag_id = ?", (dag_id,))


async def list_dags(status: str | None = None, limit: int = 50) -> list[dict]:
    """列出 DAG，可按状态过滤。"""
    if status:
        return await fetch_all(
            "SELECT * FROM dags WHERE status = ? ORDER BY submitted_at_ms DESC LIMIT ?",
            (status, limit),
        )
    return await fetch_all(
        "SELECT * FROM dags ORDER BY submitted_at_ms DESC LIMIT ?",
        (limit,),
    )


async def get_dags_batch(dag_ids: list[str]) -> list[dict]:
    """批量获取 DAG 记录。"""
    if not dag_ids:
        return []
    placeholders = ",".join("?" for _ in dag_ids)
    return await fetch_all(
        f"SELECT * FROM dags WHERE dag_id IN ({placeholders}) ORDER BY submitted_at_ms DESC",
        tuple(dag_ids),
    )


# ═══════════════════════════════════════════════════════════
#  DAG Nodes
# ═══════════════════════════════════════════════════════════

async def create_dag_nodes(dag_id: str, nodes: list[DagNodeDef]) -> None:
    """批量创建 DAG 节点记录。"""
    now_ms = int(time.time() * 1000)
    for node in nodes:
        await execute(
            """INSERT INTO dag_nodes
               (dag_id, node_id, status, assigned_agent, retry_count, started_at_ms)
               VALUES (?, ?, ?, NULL, 0, ?)""",
            (dag_id, node.node_id, NodeStatus.pending.value, now_ms),
        )


async def assign_node(dag_id: str, node_id: str, agent_id: str) -> None:
    """将节点分配给指定 Agent。"""
    await execute(
        """UPDATE dag_nodes SET status = ?, assigned_agent = ?, started_at_ms = ?
           WHERE dag_id = ? AND node_id = ?""",
        (NodeStatus.assigned.value, agent_id, int(time.time() * 1000), dag_id, node_id),
    )


async def start_node(dag_id: str, node_id: str) -> None:
    """标记节点开始执行。"""
    await execute(
        "UPDATE dag_nodes SET status = ?, started_at_ms = ? WHERE dag_id = ? AND node_id = ?",
        (NodeStatus.running.value, int(time.time() * 1000), dag_id, node_id),
    )


async def complete_node(dag_id: str, node_id: str, output: dict) -> None:
    """标记节点执行成功。"""
    await execute(
        """UPDATE dag_nodes SET status = ?, finished_at_ms = ?, output = ?
           WHERE dag_id = ? AND node_id = ?""",
        (NodeStatus.completed.value, int(time.time() * 1000), json.dumps(output), dag_id, node_id),
    )


async def fail_node(dag_id: str, node_id: str, error: dict | None = None) -> None:
    """标记节点执行失败。"""
    await execute(
        """UPDATE dag_nodes SET status = ?, finished_at_ms = ?, output = ?
           WHERE dag_id = ? AND node_id = ?""",
        (NodeStatus.failed.value, int(time.time() * 1000),
         json.dumps(error) if error else None, dag_id, node_id),
    )


async def retry_node(dag_id: str, node_id: str) -> int:
    """节点重试：retry_count + 1，状态重置为 pending。返回新的 retry_count。"""
    row = await fetch_one(
        "SELECT retry_count FROM dag_nodes WHERE dag_id = ? AND node_id = ?",
        (dag_id, node_id),
    )
    if not row:
        return 0
    new_count = (row.get("retry_count", 0) or 0) + 1
    await execute(
        """UPDATE dag_nodes SET status = ?, retry_count = ?, assigned_agent = NULL,
           started_at_ms = NULL, finished_at_ms = NULL, output = NULL
           WHERE dag_id = ? AND node_id = ?""",
        (NodeStatus.pending.value, new_count, dag_id, node_id),
    )
    return new_count


async def get_node(dag_id: str, node_id: str) -> dict | None:
    """获取单个节点状态。"""
    return await fetch_one(
        "SELECT * FROM dag_nodes WHERE dag_id = ? AND node_id = ?",
        (dag_id, node_id),
    )


async def get_dag_nodes(dag_id: str) -> list[dict]:
    """获取 DAG 的所有节点。"""
    return await fetch_all(
        "SELECT * FROM dag_nodes WHERE dag_id = ? ORDER BY id",
        (dag_id,),
    )


async def get_dag_nodes_batch(dag_ids: list[str]) -> dict[str, list[dict]]:
    """批量获取多个 DAG 的节点。返回 {dag_id: [nodes]}。"""
    if not dag_ids:
        return {}
    placeholders = ",".join("?" for _ in dag_ids)
    rows = await fetch_all(
        f"SELECT * FROM dag_nodes WHERE dag_id IN ({placeholders}) ORDER BY dag_id, id",
        tuple(dag_ids),
    )
    grouped: dict[str, list[dict]] = {did: [] for did in dag_ids}
    for row in rows:
        grouped.setdefault(row["dag_id"], []).append(row)
    return grouped


async def get_pending_nodes(dag_id: str) -> list[dict]:
    """获取 DAG 中状态为 pending 的节点。"""
    return await fetch_all(
        "SELECT * FROM dag_nodes WHERE dag_id = ? AND status = ?",
        (dag_id, NodeStatus.pending.value),
    )


async def get_nodes_by_status(dag_id: str, status: NodeStatus) -> list[dict]:
    """按状态获取节点。"""
    return await fetch_all(
        "SELECT * FROM dag_nodes WHERE dag_id = ? AND status = ?",
        (dag_id, status.value),
    )


# ═══════════════════════════════════════════════════════════
#  审计日志
# ═══════════════════════════════════════════════════════════

async def write_audit(
    event_type: AuditEventType,
    from_agent: str | None = None,
    to_agent: str | None = None,
    msg_id: str | None = None,
    detail: dict | None = None,
    correlation_id: str | None = None,
) -> None:
    """写入一条审计日志。"""
    await execute(
        """INSERT INTO audit_logs
           (ts_ms, event_type, from_agent, to_agent, msg_id, detail, auth_result, correlation_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            int(time.time() * 1000),
            event_type,
            from_agent,
            to_agent,
            msg_id,
            json.dumps(detail) if detail else None,
            "pass",
            correlation_id,
        ),
    )


async def get_audit_logs(correlation_id: str | None = None, limit: int = 100) -> list[dict]:
    """查询审计日志。"""
    if correlation_id:
        return await fetch_all(
            "SELECT * FROM audit_logs WHERE correlation_id = ? ORDER BY ts_ms DESC LIMIT ?",
            (correlation_id, limit),
        )
    return await fetch_all(
        "SELECT * FROM audit_logs ORDER BY ts_ms DESC LIMIT ?",
        (limit,),
    )


# ═══════════════════════════════════════════════════════════
#  闭环验证
# ═══════════════════════════════════════════════════════════

async def save_verification(
    dag_id: str,
    metric_before: dict,
    metric_after: dict,
    verdict: str,
    retry_count: int = 0,
) -> None:
    """保存一次闭环验证记录。"""
    await execute(
        """INSERT INTO verifications
           (dag_id, repair_ts_ms, metric_before, metric_after, verdict, retry_count, finished_at_ms)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            dag_id,
            int(time.time() * 1000),
            json.dumps(metric_before),
            json.dumps(metric_after),
            verdict,
            retry_count,
            int(time.time() * 1000),
        ),
    )


async def get_verifications(dag_id: str) -> list[dict]:
    """获取某 DAG 的所有验证记录。"""
    return await fetch_all(
        "SELECT * FROM verifications WHERE dag_id = ? ORDER BY id DESC",
        (dag_id,),
    )


async def get_verifications_batch(dag_ids: list[str]) -> dict[str, list[dict]]:
    """批量获取多个 DAG 的验证记录。返回 {dag_id: [verifications]}。"""
    if not dag_ids:
        return {}
    placeholders = ",".join("?" for _ in dag_ids)
    rows = await fetch_all(
        f"SELECT * FROM verifications WHERE dag_id IN ({placeholders}) ORDER BY dag_id, id DESC",
        tuple(dag_ids),
    )
    grouped: dict[str, list[dict]] = {did: [] for did in dag_ids}
    for row in rows:
        grouped.setdefault(row["dag_id"], []).append(row)
    return grouped
