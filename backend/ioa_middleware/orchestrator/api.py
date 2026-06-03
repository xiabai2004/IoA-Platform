"""DAG 调度器 — REST API 路由

端点：
- POST   /dag            — 提交 DAG 定义
- GET    /dag            — 列出所有 DAG
- GET    /dag/{dag_id}   — 获取 DAG 详情（含节点状态）
- GET    /dag/{dag_id}/nodes — 获取 DAG 所有节点
- POST   /dag/{dag_id}/cancel — 取消 DAG

DAG 定义格式：
{
    "dag_id": "dag-001",
    "correlation_id": "corr-1",
    "description": "华东域故障诊断修复",
    "nodes": [
        {"node_id": "monitor-1", "type": "monitor", "capability": "monitor", "domain": "east-china"},
        {"node_id": "diagnose-1", "type": "diagnose", "capability": "diagnose", "depends_on": ["monitor-1"]},
        {"node_id": "repair-1", "type": "repair", "capability": "repair", "depends_on": ["diagnose-1"]},
        {"node_id": "verify-1", "type": "verify", "capability": "verify", "depends_on": ["repair-1"]}
    ]
}
"""

import uuid
import time
from fastapi import APIRouter, HTTPException, Query

from ioa_middleware.orchestrator.models import DagDefinition
from ioa_middleware.orchestrator.scheduler import get_scheduler
from ioa_middleware.orchestrator import store
from ioa_middleware.orchestrator.models import DagStatus

router = APIRouter()


@router.post(
    "",
    status_code=201,
    summary="提交 DAG 任务",
    description="""
提交 DAG（有向无环图）任务定义，调度器异步执行。

### DAG 节点类型

| 类型 | 能力 | 描述 |
|------|------|------|
| monitor | monitor | 网络指标采集 |
| diagnose | diagnose | 故障根因分析 |
| repair | repair | 故障自动修复 |
| verify | verify | 闭环验证 |
| report | report | 报告生成 |

### 节点依赖

- **depends_on**: 依赖的节点 ID 列表
- 支持多级依赖（A → B → C）
- 不允许循环依赖

### 请求体示例

```json
{
    "dag_id": "dag-001",
    "correlation_id": "corr-1",
    "description": "华东域故障诊断修复",
    "nodes": [
        {
            "node_id": "monitor-1",
            "type": "monitor",
            "capability": "monitor",
            "domain": "east-china",
            "params": {"domain": "east-china"}
        },
        {
            "node_id": "diagnose-1",
            "type": "diagnose",
            "capability": "diagnose",
            "depends_on": ["monitor-1"]
        },
        {
            "node_id": "repair-1",
            "type": "repair",
            "capability": "repair",
            "depends_on": ["diagnose-1"]
        },
        {
            "node_id": "verify-1",
            "type": "verify",
            "capability": "verify",
            "depends_on": ["repair-1"]
        }
    ]
}
```

### 执行流程

1. 调度器接收 DAG 定义
2. 拓扑排序确定执行顺序
3. 为每个节点匹配最佳 Agent
4. 按依赖顺序分发任务
5. 收集结果，处理重试
6. 完成或失败时更新状态

### 响应示例

```json
{
    "status": "ok",
    "dag_id": "dag-001"
}
```
    """,
    response_description="DAG 提交结果",
    responses={
        201: {"description": "提交成功"},
        409: {"description": "DAG ID 已存在"},
        422: {"description": "DAG 定义无效"},
    },
)
async def submit_dag(definition: dict):
    """提交 DAG 任务。"""
    # 若未提供 dag_id，自动生成
    if "dag_id" not in definition:
        definition["dag_id"] = f"dag-{uuid.uuid4().hex[:12]}"

    try:
        dag_def = DagDefinition(**definition)
    except Exception as e:
        raise HTTPException(status_code=422, detail=str(e))

    scheduler = get_scheduler()

    # 检查是否已存在
    existing = await store.get_dag(dag_def.dag_id)
    if existing:
        raise HTTPException(status_code=409, detail=f"DAG '{dag_def.dag_id}' already exists")

    try:
        dag_id = await scheduler.submit(dag_def)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    return {"status": "ok", "dag_id": dag_id}


@router.get(
    "",
    summary="列出 DAG 任务",
    description="""
列出所有 DAG 任务，可按状态过滤。

### 查询参数

- **status**: 按状态过滤（pending / running / completed / failed / cancelled）
- **limit**: 返回数量限制（默认 50，最大 200）

### 响应示例

```json
{
    "dags": [
        {
            "dag_id": "dag-001",
            "correlation_id": "corr-1",
            "description": "华东域故障诊断修复",
            "status": "completed",
            "submitted_at_ms": 1700000000000,
            "finished_at_ms": 1700000006000,
            "result": {"status": "completed"}
        }
    ],
    "count": 1
}
```
    """,
    response_description="DAG 列表",
)
async def list_dags(
    status: str | None = Query(None, description="按状态过滤"),
    limit: int = Query(50, ge=1, le=200, description="返回数量限制"),
):
    """列出所有 DAG，可按状态过滤。"""
    dags = await store.list_dags(status=status, limit=limit)
    return {"dags": dags, "count": len(dags)}


@router.get(
    "/{dag_id}",
    summary="获取 DAG 详情",
    description="""
获取 DAG 任务详情，包含所有节点状态和验证记录。

### 路径参数

- **dag_id**: DAG 唯一标识

### 响应示例

```json
{
    "dag_id": "dag-001",
    "correlation_id": "corr-1",
    "description": "华东域故障诊断修复",
    "status": "completed",
    "submitted_at_ms": 1700000000000,
    "finished_at_ms": 1700000006000,
    "result": {"status": "completed"},
    "nodes": [
        {
            "node_id": "monitor-1",
            "status": "completed",
            "assigned_agent": "monitor-east-china",
            "started_at_ms": 1700000000100,
            "finished_at_ms": 1700000001000,
            "output": {"domain": "east-china", "anomalies": []}
        }
    ],
    "verifications": []
}
```
    """,
    response_description="DAG 详情",
    responses={
        200: {"description": "成功"},
        404: {"description": "DAG 不存在"},
    },
)
async def get_dag(dag_id: str):
    """获取 DAG 详情，包含所有节点状态。"""
    dag = await store.get_dag(dag_id)
    if not dag:
        raise HTTPException(status_code=404, detail=f"DAG '{dag_id}' not found")

    nodes = await store.get_dag_nodes(dag_id)
    dag["nodes"] = nodes

    # 附加验证记录（如有）
    verifications = await store.get_verifications(dag_id)
    dag["verifications"] = verifications

    return dag


@router.get(
    "/{dag_id}/nodes",
    summary="获取 DAG 节点",
    description="""
获取 DAG 任务的所有执行节点状态。

### 路径参数

- **dag_id**: DAG 唯一标识

### 响应示例

```json
{
    "dag_id": "dag-001",
    "nodes": [
        {
            "id": 1,
            "dag_id": "dag-001",
            "node_id": "monitor-1",
            "status": "completed",
            "assigned_agent": "monitor-east-china",
            "started_at_ms": 1700000000100,
            "finished_at_ms": 1700000001000,
            "output": {"domain": "east-china"},
            "retry_count": 0
        }
    ],
    "count": 1
}
```
    """,
    response_description="节点列表",
    responses={
        200: {"description": "成功"},
        404: {"description": "DAG 不存在"},
    },
)
async def get_dag_nodes(dag_id: str):
    """获取 DAG 的所有执行节点。"""
    dag = await store.get_dag(dag_id)
    if not dag:
        raise HTTPException(status_code=404, detail=f"DAG '{dag_id}' not found")

    nodes = await store.get_dag_nodes(dag_id)
    return {"dag_id": dag_id, "nodes": nodes, "count": len(nodes)}


@router.post(
    "/{dag_id}/cancel",
    summary="取消 DAG 任务",
    description="""
取消正在执行的 DAG 任务。

只能取消状态为 pending 或 running 的 DAG。

### 路径参数

- **dag_id**: DAG 唯一标识

### 响应示例

```json
{
    "status": "ok",
    "dag_id": "dag-001",
    "new_status": "cancelled"
}
```
    """,
    response_description="取消结果",
    responses={
        200: {"description": "取消成功"},
        404: {"description": "DAG 不存在"},
        409: {"description": "DAG 状态不允许取消"},
    },
)
async def cancel_dag(dag_id: str):
    """取消正在执行的 DAG。"""
    dag = await store.get_dag(dag_id)
    if not dag:
        raise HTTPException(status_code=404, detail=f"DAG '{dag_id}' not found")

    if dag["status"] not in (DagStatus.pending.value, DagStatus.running.value):
        raise HTTPException(
            status_code=409,
            detail=f"DAG '{dag_id}' is already {dag['status']}",
        )

    await store.update_dag_status(dag_id, DagStatus.cancelled)
    await store.write_audit("dag.cancelled", detail={"dag_id": dag_id})

    return {"status": "ok", "dag_id": dag_id, "new_status": "cancelled"}
