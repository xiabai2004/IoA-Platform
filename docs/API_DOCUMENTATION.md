# IoA 平台 API 文档

**版本**: 1.0.0
**更新日期**: 2026-06-03
**Base URL**: `http://127.0.0.1:8000`

---

## 📋 概述

IoA 平台提供以下 API 模块：

| 模块 | 前缀 | 描述 |
|------|------|------|
| Agent 注册中心 | `/registry` | Agent 注册、心跳、能力查询 |
| 消息路由总线 | `/messages` | IoAP 消息发送、接收、WebSocket |
| DAG 调度引擎 | `/dag` | DAG 任务提交、状态查询、取消 |
| 网络模拟器 | `/simulator` | 指标查询、故障注入、拓扑数据 |

### 认证方式

所有写操作需要 Bearer Token 认证：

```http
Authorization: Bearer <pre_shared_key>
```

### 开放端点（无需认证）

| 端点 | 方法 | 描述 |
|------|------|------|
| `/docs` | GET | Swagger UI |
| `/openapi.json` | GET | OpenAPI Schema |
| `/health` | GET | 健康检查 |
| `/gui` | GET | Web 控制台 |
| `/registry/agents` | GET | Agent 列表（只读） |
| `/registry/query` | GET | 能力查询（只读） |

---

## 一、Agent 注册中心 (`/registry`)

### 1.1 Agent 注册

```http
POST /registry/register
```

注册一个新 Agent 或更新已有 Agent 的信息。

**请求体**:

```json
{
    "agent_id": "monitor-east-china",
    "domain": "east-china",
    "capabilities": ["monitor"],
    "protocols": ["ioap-v1", "mcp-v2024", "a2a-v1"],
    "model": null,
    "load": 0.0,
    "status": "active",
    "endpoint": "agent://monitor-east-china",
    "metadata": {
        "version": "1.0.0",
        "description": "华东域监控 Agent - 采集网络指标，检测异常",
        "supported_tasks": ["metrics_collection", "anomaly_detection"],
        "input_schema": {
            "type": "object",
            "properties": {
                "domain": {"type": "string"}
            }
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "metrics": {"type": "object"},
                "anomalies": {"type": "array"}
            }
        }
    }
}
```

**响应**:

```json
{
    "status": "ok",
    "agent_id": "monitor-east-china"
}
```

### 1.2 Agent 心跳

```http
POST /registry/heartbeat
```

每 10 秒调用一次，刷新 last_heartbeat_ms。30 秒未收到心跳将标记为 offline。

**请求体**:

```json
{
    "agent_id": "monitor-east-china"
}
```

**响应**:

```json
{
    "status": "ok"
}
```

### 1.3 Agent 注销

```http
POST /registry/deregister
```

注销一个 Agent，从注册中心移除。

**请求体**:

```json
{
    "agent_id": "monitor-east-china"
}
```

**响应**:

```json
{
    "status": "ok"
}
```

### 1.4 列出 Agent

```http
GET /registry/agents?domain={domain}&status={status}
```

列出所有在册 Agent，可按域和状态过滤。

**查询参数**:

| 参数 | 类型 | 必填 | 描述 |
|------|------|------|------|
| domain | string | 否 | 按域过滤 |
| status | string | 否 | 按状态过滤（默认 active） |

**响应**:

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

### 1.5 能力查询

```http
GET /registry/query?capability={capability}&status={status}
```

按能力标签语义查询 Agent — 路由总线的核心依赖。

**查询参数**:

| 参数 | 类型 | 必填 | 描述 |
|------|------|------|------|
| capability | string | 否 | 能力标签 |
| status | string | 否 | Agent 状态（默认 active） |

**响应**:

```json
{
    "agents": [
        {
            "agent_id": "diagnoser-global",
            "domain": "global",
            "capabilities": ["diagnose"],
            "status": "active",
            "load": 0.1
        }
    ],
    "count": 1
}
```

---

## 二、消息路由总线 (`/messages`)

### 2.1 发送消息

```http
POST /messages
```

接收并路由 IoAP 消息。

**请求体** (IoAP v1):

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

**Intent 类型**:

| 类型 | 描述 |
|------|------|
| task | 任务执行请求 |
| result | 任务执行结果 |
| report | 报告消息 |
| heartbeat | 心跳消息 |

**响应**:

```json
{
    "status": "ok",
    "msg_id": "550e8400-e29b-41d4-a716-446655440000",
    "routed": true
}
```

### 2.2 查询消息历史

```http
GET /messages?correlation_id={id}&from_agent={agent}&to_agent={agent}&status={status}&limit={n}
```

查询消息历史，支持多种过滤条件。

**查询参数**:

| 参数 | 类型 | 必填 | 描述 |
|------|------|------|------|
| correlation_id | string | 否 | 关联 ID |
| from_agent | string | 否 | 发送方 Agent |
| to_agent | string | 否 | 接收方 Agent |
| status | string | 否 | 消息状态 |
| limit | int | 否 | 返回数量（默认 50，最大 200） |

**响应**:

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

### 2.3 获取消息详情

```http
GET /messages/{msg_id}
```

根据消息 ID 获取单条消息的完整详情。

**路径参数**:

| 参数 | 类型 | 描述 |
|------|------|------|
| msg_id | string | 消息唯一标识 |

**响应**:

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
    "status": "delivered",
    "ts_ms": 1700000000000,
    "delivered_ms": 1700000000100
}
```

### 2.4 WebSocket 连接

```http
WS /messages/ws?agent_id={agent_id}
```

Agent WebSocket 连接端点。

**连接参数**:

| 参数 | 类型 | 必填 | 描述 |
|------|------|------|------|
| agent_id | string | 是 | Agent 唯一标识 |

**通信协议**:

**服务端 → 客户端**:
- IoAP 消息 JSON
- 心跳: `{"type": "ping"}`

**客户端 → 服务端**:
- 消息确认: `{"type": "ack", "msg_id": "..."}`
- 心跳回复: `{"type": "pong"}`

**示例**:

```javascript
const ws = new WebSocket('ws://localhost:8000/messages/ws?agent_id=my-agent');

ws.onmessage = (event) => {
    const msg = JSON.parse(event.data);
    if (msg.type === 'ping') {
        ws.send(JSON.stringify({type: 'pong'}));
    } else {
        console.log('Received:', msg);
        ws.send(JSON.stringify({type: 'ack', msg_id: msg.msg_id}));
    }
};
```

---

## 三、DAG 调度引擎 (`/dag`)

### 3.1 提交 DAG 任务

```http
POST /dag
```

提交 DAG（有向无环图）任务定义，调度器异步执行。

**请求体**:

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

**节点类型**:

| 类型 | 能力 | 描述 |
|------|------|------|
| monitor | monitor | 网络指标采集 |
| diagnose | diagnose | 故障根因分析 |
| repair | repair | 故障自动修复 |
| verify | verify | 闭环验证 |
| report | report | 报告生成 |

**响应**:

```json
{
    "status": "ok",
    "dag_id": "dag-001"
}
```

### 3.2 列出 DAG 任务

```http
GET /dag?status={status}&limit={limit}
```

列出所有 DAG 任务，可按状态过滤。

**查询参数**:

| 参数 | 类型 | 必填 | 描述 |
|------|------|------|------|
| status | string | 否 | 按状态过滤 |
| limit | int | 否 | 返回数量（默认 50） |

**响应**:

```json
{
    "dags": [
        {
            "dag_id": "dag-001",
            "correlation_id": "corr-1",
            "description": "华东域故障诊断修复",
            "status": "completed",
            "submitted_at_ms": 1700000000000,
            "finished_at_ms": 1700000006000
        }
    ],
    "count": 1
}
```

### 3.3 获取 DAG 详情

```http
GET /dag/{dag_id}
```

获取 DAG 任务详情，包含所有节点状态。

**路径参数**:

| 参数 | 类型 | 描述 |
|------|------|------|
| dag_id | string | DAG 唯一标识 |

**响应**:

```json
{
    "dag_id": "dag-001",
    "status": "completed",
    "nodes": [
        {
            "node_id": "monitor-1",
            "status": "completed",
            "assigned_agent": "monitor-east-china",
            "output": {"domain": "east-china", "anomalies": []}
        }
    ],
    "verifications": []
}
```

### 3.4 获取 DAG 节点

```http
GET /dag/{dag_id}/nodes
```

获取 DAG 任务的所有执行节点状态。

**响应**:

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

### 3.5 取消 DAG 任务

```http
POST /dag/{dag_id}/cancel
```

取消正在执行的 DAG 任务。

**响应**:

```json
{
    "status": "ok",
    "dag_id": "dag-001",
    "new_status": "cancelled"
}
```

---

## 四、网络模拟器 (`/simulator`)

### 4.1 获取网络指标

```http
GET /simulator/metrics?domain={domain}
```

获取网络指标快照。

**查询参数**:

| 参数 | 类型 | 必填 | 描述 |
|------|------|------|------|
| domain | string | 否 | 按域过滤 |

**响应**:

```json
{
    "ts_ms": 1700000000000,
    "metrics": {
        "east-china": {
            "domain": "east-china",
            "latency_ms": 15.5,
            "packet_loss": 0.001,
            "bandwidth_util": 0.45,
            "throughput_mbps": 450.0,
            "connection_count": 150
        }
    }
}
```

### 4.2 获取拓扑数据

```http
GET /simulator/topology
```

获取网络拓扑数据（节点+链路）。

**响应**:

```json
{
    "domains": ["east-china", "north-china", "south-china", "west-china"],
    "nodes": {
        "east-china": {
            "edge_router": "Edge-R1",
            "servers": ["srv-east-1", "srv-east-2", "srv-east-3"],
            "terminal_count": 20
        }
    },
    "links": [
        {"from": "Core-Router", "to": "Edge-R1", "bandwidth_gbps": 10.0}
    ]
}
```

### 4.3 注入故障

```http
POST /simulator/fault/inject?fault_type={type}&target={target}
```

注入网络故障。

**故障类型**:

| 类型 | 描述 |
|------|------|
| link_congestion | 链路拥塞 |
| link_outage | 链路中断 |
| cpu_overload | CPU 过载 |
| ddos | DDoS 攻击 |
| misconfig | 配置错误 |
| device_failure | 设备故障 |

**响应**:

```json
{
    "status": "ok",
    "fault_id": "fault-1",
    "fault_type": "link_congestion",
    "target": "east-china"
}
```

### 4.4 清除故障

```http
POST /simulator/fault/clear?fault_id={fault_id}
```

清除指定故障。

**响应**:

```json
{
    "status": "ok",
    "fault_id": "fault-1"
}
```

### 4.5 清除所有故障

```http
GET /simulator/fault/clear_all
```

清除所有故障。

**响应**:

```json
{
    "status": "ok"
}
```

### 4.6 列出活跃故障

```http
GET /simulator/faults
```

列出当前所有激活的故障。

**响应**:

```json
{
    "faults": [
        {
            "fault_id": "fault-1",
            "type": "link_congestion",
            "target": "east-china",
            "injected_at_ms": 1700000000000
        }
    ]
}
```

---

## 五、错误码

| HTTP 状态码 | 描述 |
|------------|------|
| 200 | 成功 |
| 201 | 创建成功 |
| 400 | 请求错误 |
| 401 | 未授权 |
| 404 | 资源不存在 |
| 409 | 冲突（如 DAG ID 已存在） |
| 422 | 参数验证失败 |
| 500 | 服务器内部错误 |

---

## 六、SDK 示例

### Python

```python
import httpx

BASE_URL = "http://127.0.0.1:8000"
AUTH_HEADER = {"Authorization": "Bearer ioa2026demo"}

# 发送消息
async def send_message():
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{BASE_URL}/messages",
            json={
                "from_agent": "my-agent",
                "to_agent": "orchestrator-agent",
                "intent": {"type": "task", "description": "检查华东网络"},
                "payload": {"message": "华东地区网络异常"},
            },
            headers=AUTH_HEADER,
        )
        return response.json()

# 查询 Agent
async def query_agents():
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{BASE_URL}/registry/query",
            params={"capability": "monitor"},
        )
        return response.json()
```

### JavaScript

```javascript
const BASE_URL = 'http://127.0.0.1:8000';
const AUTH_HEADER = {'Authorization': 'Bearer ioa2026demo'};

// WebSocket 连接
const ws = new WebSocket(`${BASE_URL.replace('http', 'ws')}/messages/ws?agent_id=my-agent`);

ws.onmessage = (event) => {
    const msg = JSON.parse(event.data);
    console.log('Received:', msg);
};

// 发送消息
async function sendMessage() {
    const response = await fetch(`${BASE_URL}/messages`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            ...AUTH_HEADER,
        },
        body: JSON.stringify({
            from_agent: 'my-agent',
            intent: {type: 'task', description: '检查网络'},
            payload: {message: '网络异常'},
        }),
    });
    return response.json();
}
```

---

## 七、在线 API 文档

启动服务后，访问以下地址查看自动生成的 API 文档：

- **Swagger UI**: http://127.0.0.1:8000/docs
- **ReDoc**: http://127.0.0.1:8000/redoc
- **OpenAPI Schema**: http://127.0.0.1:8000/openapi.json

---

*文档版本: 1.0.0*
*最后更新: 2026-06-03*
