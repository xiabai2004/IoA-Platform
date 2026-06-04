# IoAP 协议规范 (Internet of Agents Protocol)

**版本**: v1.0
**更新日期**: 2026-06-04
**状态**: 草案

---

## 1. 概述

IoAP (Internet of Agents Protocol) 是 IoA 平台自研的智能体间通信协议，用于实现 Agent 之间的标准化消息传递。

### 1.1 设计目标

| 目标 | 说明 |
|------|------|
| **松耦合** | Agent 间通过消息通信，无直接依赖 |
| **可追溯** | 每条消息包含唯一 ID 和关联 ID |
| **可扩展** | 支持自定义 intent 类型和 payload |
| **多协议兼容** | 与 A2A、MCP 协议可桥接 |

### 1.2 协议栈

```
┌─────────────────────────────────────┐
│         应用层 (Application)        │
│    Agent 业务逻辑 + DAG 调度        │
├─────────────────────────────────────┤
│         IoAP 协议层                 │
│    消息格式 + 路由规则 + 状态机     │
├─────────────────────────────────────┤
│         传输层 (Transport)          │
│    WebSocket + HTTP REST            │
├─────────────────────────────────────┤
│         网络层 (Network)            │
│    TCP/IP                           │
└─────────────────────────────────────┘
```

---

## 2. 消息格式

### 2.1 消息结构 (JSON)

```json
{
  "msg_id": "string (UUID)",
  "from_agent": "string",
  "to_agent": "string | null",
  "intent": {
    "type": "string",
    "description": "string",
    "priority": "string"
  },
  "payload": {
    // 业务数据，结构取决于 intent.type
  },
  "correlation_id": "string | null",
  "ts_ms": "integer (毫秒时间戳)"
}
```

### 2.2 字段说明

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `msg_id` | string | 是 | 消息唯一标识，UUID v4 格式 |
| `from_agent` | string | 是 | 发送方 Agent ID |
| `to_agent` | string | 否 | 接收方 Agent ID，null 表示广播 |
| `intent` | object | 是 | 意图描述对象 |
| `payload` | object | 是 | 业务数据载荷 |
| `correlation_id` | string | 否 | 关联 ID，用于链路追踪 |
| `ts_ms` | integer | 是 | 消息创建时间戳（毫秒） |

### 2.3 Intent 对象

```json
{
  "type": "string",
  "description": "string",
  "priority": "string"
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `type` | string | 是 | 意图类型（见下表） |
| `description` | string | 否 | 意图描述（人类可读） |
| `priority` | string | 否 | 优先级，默认 "normal" |

### 2.4 Intent 类型定义

| type | 说明 | 典型发送方 | 典型接收方 |
|------|------|-----------|-----------|
| `user` | 用户输入 | GUI / CLI | orchestrator |
| `task` | 任务分发 | orchestrator | monitor / diagnoser / repairer / verifier / reporter |
| `result` | 任务结果 | monitor / diagnoser / repairer / verifier / reporter | orchestrator |
| `report` | 报告消息 | reporter | GUI / 用户 |
| `heartbeat` | 心跳 | 所有 Agent | 注册中心 |
| `ack` | 消息确认 | 接收方 | 发送方 |
| `error` | 错误通知 | 任何 Agent | 任何 Agent |

### 2.5 优先级定义

| 优先级 | 数值权重 | 说明 |
|--------|---------|------|
| `low` | 0 | 低优先级，可延迟处理 |
| `normal` | 1 | 普通优先级（默认） |
| `high` | 2 | 高优先级，优先处理 |
| `critical` | 3 | 紧急，立即处理 |

---

## 3. Payload 结构定义

### 3.1 task 类型 Payload

```json
{
  "dag_id": "string",
  "node_id": "string",
  "node_type": "string",
  "capability": "string",
  "params": {
    // 节点参数，由 DAG 模板定义
  }
}
```

### 3.2 result 类型 Payload

```json
{
  "dag_id": "string",
  "node_id": "string",
  "result": {
    "success": "boolean",
    "output": {
      // 业务输出数据
    },
    "error": "string | null"
  }
}
```

### 3.3 user 类型 Payload

```json
{
  "message": "string",
  "params": {
    // 用户输入的参数
  }
}
```

### 3.4 report 类型 Payload

```json
{
  "dag_id": "string",
  "report": {
    "summary": {},
    "narrative": "string",
    "improvements": {},
    "final_metrics": {}
  }
}
```

---

## 4. 路由规则

### 4.1 路由策略

```
收到消息
    │
    ▼
┌─────────────────┐
│ to_agent 非空?  │
└────────┬────────┘
         │
    ┌────┴────┐
    ▼         ▼
   是         否
    │         │
    ▼         ▼
┌───────┐ ┌───────┐
│精准路由│ │广播   │
│到指定  │ │到所有 │
│Agent   │ │Agent  │
└───────┘ └───────┘
```

### 4.2 精准路由

当 `to_agent` 非空时，消息直接推送到目标 Agent 的 WebSocket 连接。

**查找逻辑**：
1. 在连接池中查找 `agent_id == to_agent` 的 WebSocket
2. 找到 → 推送消息
3. 未找到 → 消息持久化，等待 Agent 重连后拉取

### 4.3 广播路由

当 `to_agent` 为空时，消息推送到所有已连接的 Agent。

**实现**：
```python
for agent_id, ws in connections.items():
    await ws.send_json(msg)
```

---

## 5. 状态机

### 5.1 消息状态

```
┌───────────┐
│  created  │ ← 消息创建
└─────┬─────┘
      │
      ▼
┌───────────┐
│   sent    │ ← 发送到路由总线
└─────┬─────┘
      │
      ▼
┌───────────┐
│ delivered │ ← 推送到目标 Agent
└─────┬─────┘
      │
      ▼
┌───────────┐
│  acked    │ ← 接收方确认
└───────────┘
```

### 5.2 状态转换规则

| 当前状态 | 触发条件 | 下一状态 |
|---------|---------|---------|
| created | 消息发送 | sent |
| sent | WebSocket 推送成功 | delivered |
| delivered | 接收方发送 ack | acked |
| sent | 超时未送达 | failed |

---

## 6. 错误处理

### 6.1 错误码定义

| 错误码 | 说明 | 处理建议 |
|--------|------|---------|
| `E001` | 消息格式无效 | 检查 JSON 结构 |
| `E002` | 目标 Agent 不存在 | 检查 agent_id |
| `E003` | 目标 Agent 离线 | 等待重连或重新路由 |
| `E004` | 消息队列满 | 稍后重试 |
| `E005` | 认证失败 | 检查 Token |
| `E006` | 超时 | 增加超时时间或重试 |

### 6.2 错误消息格式

```json
{
  "msg_id": "uuid",
  "from_agent": "system",
  "to_agent": "original_sender",
  "intent": {
    "type": "error",
    "description": "Error description"
  },
  "payload": {
    "error_code": "E001",
    "error_message": "Invalid message format",
    "original_msg_id": "uuid"
  },
  "ts_ms": 1700000000000
}
```

---

## 7. 心跳机制

### 7.1 心跳流程

```
Agent                           注册中心
  │                                │
  │  POST /registry/heartbeat      │
  │  {"agent_id": "xxx"}           │
  │ ──────────────────────────────→│
  │                                │ 更新 last_heartbeat_ms
  │  {"status": "ok"}              │
  │ ←──────────────────────────────│
  │                                │
  │  (每 10 秒重复)                │
```

### 7.2 超时检测

- 心跳间隔：10 秒
- 超时阈值：30 秒
- 检测周期：15 秒
- 超时动作：标记 Agent 为 `offline`

---

## 8. WebSocket 协议

### 8.1 连接建立

```
Client → Server:
  ws://host:8000/messages/ws?agent_id={agent_id}

Server → Client:
  HTTP 101 Switching Protocols
```

### 8.2 消息推送

**服务端 → 客户端**：
```json
{
  "msg_id": "uuid",
  "from_agent": "orchestrator",
  "to_agent": "monitor-east-china",
  "intent": {"type": "task", ...},
  "payload": {...},
  "ts_ms": 1700000000000
}
```

### 8.3 心跳机制

**服务端 → 客户端** (每 30 秒)：
```json
{"type": "ping"}
```

**客户端 → 服务端** (收到 ping 后)：
```json
{"type": "pong"}
```

### 8.4 消息确认（可选）

**客户端 → 服务端**：
```json
{"type": "ack", "msg_id": "uuid"}
```

---

## 9. REST API

### 9.1 发送消息

```http
POST /messages
Authorization: Bearer {token}
Content-Type: application/json

{
  "msg_id": "uuid",
  "from_agent": "gui",
  "to_agent": "orchestrator-agent",
  "intent": {
    "type": "user",
    "description": "华东网络异常",
    "priority": "high"
  },
  "payload": {
    "message": "华东网络异常，请诊断修复"
  },
  "correlation_id": "gui-001",
  "ts_ms": 1700000000000
}
```

**响应**：
```json
{
  "status": "ok",
  "msg_id": "uuid",
  "routed": true
}
```

### 9.2 查询消息

```http
GET /messages?correlation_id={id}&limit=50
Authorization: Bearer {token}
```

**响应**：
```json
{
  "messages": [...],
  "count": 10
}
```

---

## 10. 与其他协议的关系

### 10.1 IoAP 与 A2A

| 维度 | IoAP | A2A |
|------|------|-----|
| 用途 | 平台内 Agent 通信 | 跨平台 Agent 互操作 |
| 消息格式 | 自定义 JSON | A2A 标准格式 |
| 发现机制 | 注册中心 | Agent Card |
| 桥接方式 | A2AToIoAPBridge | - |

### 10.2 IoAP 与 MCP

| 维度 | IoAP | MCP |
|------|------|-----|
| 用途 | Agent 间通信 | Agent 调用工具 |
| 消息格式 | 自定义 JSON | MCP 标准格式 |
| 桥接方式 | McpToolClient | - |

---

## 11. 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| v1.0 | 2026-06-04 | 初始版本，定义基本消息格式和路由规则 |

---

*协议规范版本: v1.0 | 最后更新: 2026-06-04*
