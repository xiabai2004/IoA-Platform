"""IoA 消息路由总线 — REST API + WebSocket

架构方案 v2 §3.2 消息路由总线：
- POST /messages   — 接收 IoAP 消息，持久化并路由到目标 Agent
- GET  /messages   — 查询消息历史（支持过滤）
- GET  /messages/{msg_id} — 单条消息详情
- WS   /ws         — Agent WebSocket 连接，接收实时推送

路由策略（Phase 2 版本）：
  1. 若 to_agent 指定，推送到该 Agent 的 WebSocket 连接
  2. 若 to_agent 为空，广播给所有已连接 Agent
  3. 所有消息持久化到 SQLite messages 表

IoAP 消息格式：
{
    "msg_id": "uuid",
    "from_agent": "agent-id",
    "to_agent": "target-agent-id",    // 可选，空=广播
    "intent": {"type": "...", "description": "...", "priority": "normal"},
    "payload": {...},
    "correlation_id": "corr-uuid",
    "ts_ms": 1700000000000
}
"""

import asyncio
import json
import time
import logging
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query, Request
from ioa_middleware.db import execute, fetch_all, fetch_one

logger = logging.getLogger("router")

router = APIRouter()

# ── WebSocket 连接池 ──────────────────────────────────────
# agent_id → WebSocket 映射（一个 agent 只允许一个连接）
_connections: dict[str, WebSocket] = {}

# agent_id → asyncio.Queue（供 Agent 的 listen_loop 消费）
_queues: dict[str, asyncio.Queue] = {}


def _get_or_create_queue(agent_id: str) -> asyncio.Queue:
    """获取或创建指定 Agent 的消息队列。"""
    if agent_id not in _queues:
        _queues[agent_id] = asyncio.Queue(maxsize=200)
    return _queues[agent_id]


# ── 消息持久化 ────────────────────────────────────────────

async def _persist_message(msg: dict) -> None:
    """将消息写入 messages 表。"""
    intent = msg.get("intent", {})
    payload = msg.get("payload", {})
    await execute(
        """INSERT INTO messages
           (msg_id, from_agent, to_agent, intent_type, intent_desc, priority,
            payload, correlation_id, route_decision, status, ts_ms)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            msg.get("msg_id", ""),
            msg.get("from_agent", ""),
            msg.get("to_agent"),
            intent.get("type", ""),
            intent.get("description"),
            intent.get("priority", "normal"),
            json.dumps(payload),
            msg.get("correlation_id"),
            json.dumps(msg.get("route_decision")) if msg.get("route_decision") else None,
            "delivered",
            msg.get("ts_ms", int(time.time() * 1000)),
        ),
    )


async def _push_to_agent(agent_id: str, msg: dict) -> bool:
    """推送消息到指定 Agent 的 WebSocket 连接。返回是否成功推送。"""
    ws = _connections.get(agent_id)
    if ws is not None:
        try:
            await ws.send_text(json.dumps(msg))
            return True
        except Exception:
            # 连接已断开但尚未清理
            _connections.pop(agent_id, None)
    return False


async def _broadcast(msg: dict) -> int:
    """广播消息到所有已连接 Agent。返回推送数量。"""
    count = 0
    disconnected = []
    for agent_id, ws in list(_connections.items()):
        try:
            await ws.send_text(json.dumps(msg))
            count += 1
        except Exception:
            disconnected.append(agent_id)

    for aid in disconnected:
        _connections.pop(aid, None)

    return count


# ── REST 端点 ─────────────────────────────────────────────

@router.post(
    "",
    status_code=201,
    summary="发送 IoAP 消息",
    description="""
接收并路由 IoAP（Internet of Agents Protocol）消息。

### 路由逻辑

- **to_agent 非空**: 精准路由到指定 Agent 的 WebSocket 连接
- **to_agent 为空**: 广播给所有已连接 Agent

### 消息格式（IoAP v1）

```json
{
    "msg_id": "550e8400-e29b-41d4-a716-446655440000",
    "from_agent": "orchestrator-agent",
    "to_agent": "monitor-east-china",
    "intent": {
        "type": "task",
        "description": "采集华东域网络指标",
        "priority": "normal"
    },
    "payload": {
        "dag_id": "dag-001",
        "node_id": "monitor-1",
        "params": {"domain": "east-china"}
    },
    "correlation_id": "corr-001",
    "ts_ms": 1700000000000
}
```

### Intent 类型

| 类型 | 描述 |
|------|------|
| task | 任务执行请求 |
| result | 任务执行结果 |
| report | 报告消息 |
| heartbeat | 心跳消息 |

### 优先级

- low: 低优先级
- normal: 普通优先级（默认）
- high: 高优先级
- critical: 紧急
    """,
    response_description="消息发送结果",
    responses={
        201: {"description": "消息发送成功"},
        422: {"description": "消息格式错误"},
    },
)
async def post_message(msg: dict, request: Request):
    """接收 IoAP 消息。"""
    # 确保 ts_ms 存在
    if "ts_ms" not in msg:
        msg["ts_ms"] = int(time.time() * 1000)

    # 确保 msg_id 存在
    if "msg_id" not in msg:
        import uuid
        msg["msg_id"] = str(uuid.uuid4())

    # 持久化
    await _persist_message(msg)

    # 路由
    to_agent = msg.get("to_agent")
    delivered = False
    if to_agent:
        # Publish to message bus FIRST (all agents use bus now)
        bus = getattr(request.app.state, "bus", None)
        if bus is not None:
            try:
                await bus.publish(f"agent.{to_agent}", msg)
                delivered = True
            except (ConnectionError, OSError) as exc:
                logger.warning("Bus publish to %s failed: %s", to_agent, exc)
            except Exception:
                logger.exception("Unexpected error publishing to bus for %s", to_agent)
        # Fallback to WebSocket (for scheduler or legacy connections)
        if not delivered:
            delivered = await _push_to_agent(to_agent, msg)
        if not delivered:
            logger.info("Agent %s not reachable, message %s stored for polling",
                        to_agent, msg["msg_id"])
    else:
        count = await _broadcast(msg)
        logger.info("Broadcast message %s to %d agents", msg["msg_id"], count)

    return {
        "status": "ok",
        "msg_id": msg["msg_id"],
        "routed": delivered,
    }


@router.get(
    "",
    summary="查询消息历史",
    description="""
查询消息历史，支持多种过滤条件。

可用于 Agent 轮询获取离线期间的消息，或前端仪表盘展示消息流。

### 查询参数

- **correlation_id**: 按关联 ID 过滤（用于追踪完整任务链路）
- **from_agent**: 按发送方 Agent 过滤
- **to_agent**: 按接收方 Agent 过滤
- **status**: 按消息状态过滤（sent / delivered / acked）
- **limit**: 返回数量限制（默认 50，最大 200）

### 响应示例

```json
{
    "messages": [
        {
            "msg_id": "550e8400-e29b-41d4-a716-446655440000",
            "from_agent": "orchestrator-agent",
            "to_agent": "monitor-east-china",
            "intent_type": "task",
            "intent_desc": "采集华东域网络指标",
            "priority": "normal",
            "payload": {"dag_id": "dag-001"},
            "correlation_id": "corr-001",
            "status": "delivered",
            "ts_ms": 1700000000000
        }
    ],
    "count": 1
}
```
    """,
    response_description="消息列表",
)
async def list_messages(
    correlation_id: str | None = Query(None, description="关联 ID"),
    from_agent: str | None = Query(None, description="发送方 Agent"),
    to_agent: str | None = Query(None, description="接收方 Agent"),
    status: str | None = Query(None, description="消息状态"),
    limit: int = Query(50, ge=1, le=200, description="返回数量限制"),
):
    """查询消息历史，支持多种过滤条件。"""
    conditions = []
    params = []

    if correlation_id:
        conditions.append("correlation_id = ?")
        params.append(correlation_id)
    if from_agent:
        conditions.append("from_agent = ?")
        params.append(from_agent)
    if to_agent:
        conditions.append("to_agent = ?")
        params.append(to_agent)
    if status:
        conditions.append("status = ?")
        params.append(status)

    where = ""
    if conditions:
        where = "WHERE " + " AND ".join(conditions)

    sql = f"SELECT * FROM messages {where} ORDER BY ts_ms DESC LIMIT ?"
    params.append(limit)

    rows = await fetch_all(sql, tuple(params))
    return {
        "messages": rows,
        "count": len(rows),
    }


@router.get(
    "/{msg_id}",
    summary="获取消息详情",
    description="""
根据消息 ID 获取单条消息的完整详情。

### 路径参数

- **msg_id**: 消息唯一标识（UUID 格式）

### 响应示例

```json
{
    "msg_id": "550e8400-e29b-41d4-a716-446655440000",
    "from_agent": "orchestrator-agent",
    "to_agent": "monitor-east-china",
    "intent_type": "task",
    "intent_desc": "采集华东域网络指标",
    "priority": "normal",
    "payload": {
        "dag_id": "dag-001",
        "node_id": "monitor-1",
        "params": {"domain": "east-china"}
    },
    "correlation_id": "corr-001",
    "route_decision": null,
    "status": "delivered",
    "ts_ms": 1700000000000,
    "delivered_ms": 1700000000100
}
```
    """,
    response_description="消息详情",
    responses={
        200: {"description": "成功"},
        404: {"description": "消息不存在"},
    },
)
async def get_message(msg_id: str):
    """获取单条消息详情。"""
    row = await fetch_one("SELECT * FROM messages WHERE msg_id = ?", (msg_id,))
    if not row:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Message '{msg_id}' not found")
    return row


# ── Bandit 在线学习统计端点 ─────────────────────────────

@router.get("/bandit/stats")
async def get_bandit_stats():
    """获取 UCB1 多臂老虎机的在线学习统计数据。

    用于可视化路由权重的收敛过程。
    答辩时可展示：初始经验权重 → MAB 收敛后的数据驱动权重。

    ### 返回格式
    ```json
    {
      "agents": {
        "monitor-east-china": {
          "n": 12, "successes": 9, "failures": 3,
          "mean_reward": 0.75, "ucb_score": 0.82
        }
      },
      "total_trials": 30,
      "c": 2.0,
      "blend_alpha": 0.15
    }
    ```
    """
    from ioa_middleware.router.bandit_router import get_bandit
    return get_bandit().export_stats()


@router.get("/bandit/convergence")
async def get_bandit_convergence():
    """获取收敛曲线数据（答辩用）。

    返回每个 Agent 的 (trials, mean_reward) 序列，
    可绘制折线图展示 MAB 收敛过程。
    """
    from ioa_middleware.router.bandit_router import get_bandit
    return {"convergence": get_bandit().convergence_curve()}


@router.post("/bandit/reset")
async def reset_bandit():
    """重置 MAB 统计数据（调试/演示用）。"""
    from ioa_middleware.router.bandit_router import get_bandit
    get_bandit().reset()
    return {"status": "ok", "message": "Bandit statistics reset"}


# ── Reranker 状态端点 ──────────────────────────────────

@router.get("/reranker/status")
async def get_reranker_status():
    """获取 Cross-Encoder Reranker 的状态。

    答辩时展示：Bi-Encoder vs Cross-Encoder 的架构对比和可用性状态。

    ### 返回
    ```json
    {
      "available": true,
      "model": "BAAI/bge-reranker-v2-m3",
      "description": "Cross-Encoder 联合编码精排，在 Bi-Encoder 粗筛后对 Top-3 重排序"
    }
    ```
    """
    # 不触发模型加载，直接检查单例状态
    from ioa_middleware.router.reranker import _reranker, CrossEncoderReranker
    model_loaded = _reranker is not None and _reranker._model is not None
    return {
        "available": model_loaded,
        "model": CrossEncoderReranker.DEFAULT_MODEL,
        "description": (
            "Cross-Encoder 联合编码精排：将 [任务描述, Agent能力描述] 拼接后"
            "一起送入模型做联合推理，精度显著优于 Bi-Encoder 的独立余弦相似度。"
            "在 Bi-Encoder 粗筛 Top-3 后执行，兼顾效率与精度。"
        ),
    }


# ── WebSocket 端点 ────────────────────────────────────────

@router.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    """Agent WebSocket 连接端点。

    Agent 通过 ws://host:8000/messages/ws?agent_id=xxx 连接，
    服务端将推送所有发给该 Agent 的消息。

    ### 连接参数

    - **agent_id**: Agent 唯一标识（必填）

    ### 通信协议

    **服务端 → 客户端**:
    - IoAP 消息 JSON
    - 心跳: `{"type": "ping"}`

    **客户端 → 服务端**:
    - 消息确认: `{"type": "ack", "msg_id": "..."}`
    - 心跳回复: `{"type": "pong"}`

    ### 心跳机制

    - 服务端每 30 秒发送 ping
    - 客户端应在 5 秒内回复 pong
    - 超时未回复将断开连接

    ### 示例

    ```
    // 连接
    const ws = new WebSocket('ws://localhost:8000/messages/ws?agent_id=my-agent');

    // 接收消息
    ws.onmessage = (event) => {
        const msg = JSON.parse(event.data);
        if (msg.type === 'ping') {
            ws.send(JSON.stringify({type: 'pong'}));
        } else {
            console.log('Received:', msg);
            // 确认消息
            ws.send(JSON.stringify({type: 'ack', msg_id: msg.msg_id}));
        }
    };
    ```
    """
    # 从 query string 提取 agent_id
    agent_id = ws.query_params.get("agent_id")
    if not agent_id:
        await ws.accept()
        await ws.send_text(json.dumps({"type": "error", "detail": "Missing agent_id query parameter"}))
        await ws.close()
        return

    await ws.accept()
    logger.info("Agent %s connected via WebSocket", agent_id)

    # 注册连接（踢掉旧连接）
    old_ws = _connections.pop(agent_id, None)
    if old_ws is not None:
        try:
            await old_ws.close()
        except (WebSocketDisconnect, RuntimeError, OSError) as exc:
            logger.debug("Error closing old WebSocket for %s: %s", agent_id, exc)
    _connections[agent_id] = ws

    try:
        while True:
            # 接收客户端消息（ACK / PONG / 等）
            try:
                data = await asyncio.wait_for(ws.receive_text(), timeout=30.0)
                try:
                    msg = json.loads(data)
                    msg_type = msg.get("type", "")
                    if msg_type == "ack":
                        # 可选：更新消息状态为 acked
                        pass
                    elif msg_type == "pong":
                        pass  # 心跳回复
                except json.JSONDecodeError:
                    pass
            except asyncio.TimeoutError:
                # 超时发送 ping
                try:
                    await ws.send_text(json.dumps({"type": "ping"}))
                except (WebSocketDisconnect, RuntimeError, OSError):
                    break  # Connection lost, exit loop
    except WebSocketDisconnect:
        logger.info("Agent %s disconnected", agent_id)
    except Exception:
        logger.exception("WebSocket error for agent %s", agent_id)
    finally:
        _connections.pop(agent_id, None)
        logger.info("Agent %s WebSocket cleaned up", agent_id)
