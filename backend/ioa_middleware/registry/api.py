"""Agent 注册中心 — REST API 路由

提供 Agent 注册、心跳、注销、能力查询等接口。
支持能力自描述规范 v1.0。
"""

from fastapi import APIRouter, HTTPException, Query
from ioa_middleware.registry.models import (
    CapabilityProfile,
    HeartbeatRequest,
    DeregisterRequest,
    QueryRequest,
)
from ioa_middleware.registry import store

router = APIRouter()


@router.post(
    "/register",
    status_code=201,
    summary="Agent 注册",
    description="""
注册一个新 Agent 或更新已有 Agent 的信息。

Agent 启动时调用此接口，提交能力描述（Capability Profile），
注册中心记录并在后续用于语义路由匹配。

### 请求体

- **agent_id**: Agent 唯一标识（3-64 字符，小写字母、数字、连字符）
- **domain**: 所属域（east-china / north-china / south-china / west-china / global）
- **capabilities**: 能力标签列表（如 ["monitor", "diagnose"]）
- **protocols**: 支持的通信协议
- **metadata**: 能力自描述元数据（可选，包含版本、描述、输入输出 Schema 等）

### 示例

```json
{
    "agent_id": "monitor-east-china",
    "domain": "east-china",
    "capabilities": ["monitor"],
    "protocols": ["ioap-v1", "mcp-v2024", "a2a-v1"],
    "metadata": {
        "version": "1.0.0",
        "description": "华东域监控 Agent",
        "supported_tasks": ["metrics_collection", "anomaly_detection"]
    }
}
```
    """,
    response_description="注册成功，返回 agent_id",
    responses={
        201: {"description": "注册成功"},
        422: {"description": "请求参数验证失败"},
    },
)
async def register(profile: CapabilityProfile):
    """Agent 注册 — 提交能力描述，注册中心记录并在后续用于语义路由匹配。"""
    await store.register_agent(profile)
    return {"status": "ok", "agent_id": profile.agent_id}


@router.post(
    "/heartbeat",
    summary="Agent 心跳",
    description="""
Agent 心跳接口，每 10 秒调用一次，刷新 last_heartbeat_ms。

30 秒未收到心跳的 Agent 将被标记为 offline。

### 请求体

- **agent_id**: Agent 唯一标识

### 示例

```json
{
    "agent_id": "monitor-east-china"
}
```
    """,
    response_description="心跳成功",
    responses={
        200: {"description": "心跳成功"},
        404: {"description": "Agent 不存在"},
    },
)
async def heartbeat(req: HeartbeatRequest):
    """Agent 心跳 — 每 10s 调用一次，刷新 last_heartbeat_ms。"""
    ok = await store.heartbeat(req.agent_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Agent '{req.agent_id}' not found")
    return {"status": "ok"}


@router.post(
    "/deregister",
    summary="Agent 注销",
    description="""
注销一个 Agent，从注册中心移除。

Agent 停止时调用此接口，清理注册信息。

### 请求体

- **agent_id**: Agent 唯一标识

### 示例

```json
{
    "agent_id": "monitor-east-china"
}
```
    """,
    response_description="注销成功",
    responses={
        200: {"description": "注销成功"},
        404: {"description": "Agent 不存在"},
    },
)
async def deregister(req: DeregisterRequest):
    """Agent 注销 — 从注册中心移除。"""
    ok = await store.deregister_agent(req.agent_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Agent '{req.agent_id}' not found")
    return {"status": "ok"}


@router.get(
    "/agents",
    summary="列出 Agent",
    description="""
列出所有在册 Agent，可按域和状态过滤。

### 查询参数

- **domain**: 按域过滤（可选）
- **status**: 按状态过滤（默认 active）

### 响应示例

```json
{
    "agents": [
        {
            "agent_id": "monitor-east-china",
            "domain": "east-china",
            "capabilities": ["monitor"],
            "status": "active",
            "load": 0.2,
            "metadata": {
                "version": "1.0.0",
                "description": "华东域监控 Agent"
            }
        }
    ],
    "count": 1
}
```
    """,
    response_description="Agent 列表",
)
async def list_agents(
    domain: str | None = Query(None, description="按域过滤"),
    status: str = Query("active", description="按状态过滤"),
):
    """列出所有在册 Agent，可按域和状态过滤。"""
    agents = await store.list_agents(domain=domain, status=status)
    return {"agents": agents, "count": len(agents)}


@router.get(
    "/query",
    summary="能力查询",
    description="""
按能力标签语义查询 Agent — 路由总线的核心依赖。

### 查询参数

- **capability**: 能力标签（如 monitor, diagnose, repair）
- **status**: Agent 状态（默认 active）

### 响应示例

```json
{
    "agents": [
        {
            "agent_id": "diagnoser-global",
            "domain": "global",
            "capabilities": ["diagnose"],
            "status": "active",
            "load": 0.1,
            "metadata": {
                "version": "1.0.0",
                "description": "全局诊断 Agent",
                "supported_tasks": ["root_cause_analysis", "fault_classification"]
            }
        }
    ],
    "count": 1
}
```
    """,
    response_description="匹配的 Agent 列表",
)
async def query_agents(
    capability: str | None = Query(None, description="能力标签"),
    status: str = Query("active", description="Agent 状态"),
):
    """按能力标签语义查询 Agent — 路由总线的核心依赖。"""
    if not capability:
        agents = await store.list_agents(status=status)
    else:
        agents = await store.query_by_capability(capability, status=status)
    return {"agents": agents, "count": len(agents)}
