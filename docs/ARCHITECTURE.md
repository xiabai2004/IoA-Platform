# 系统架构设计文档 — IoA 多智能体协同运维平台

> C4 网络技术挑战赛 B-EP1 | 版本 v3.0 | 2026-06-04

---

## 目录

1. [系统概述](#1-系统概述)
2. [总体架构](#2-总体架构)
3. [IoA 中间件设计](#3-ioa-中间件设计)
4. [Agent 运行时设计](#4-agent-运行时设计)
5. [IoAP 通信协议](#5-ioap-通信协议)
6. [网络模拟器设计](#6-网络模拟器设计)
7. [闭环验证系统](#7-闭环验证系统)
8. [安全与信任](#8-安全与信任)
9. [部署架构与演进路线](#9-部署架构与演进路线)
10. [评估指标](#10-评估指标)

---

## 1. 系统概述

本系统是一个**双层架构的智能体互联网（Internet of Agents, IoA）应用**。

**下层（IoA 中间件）**：自研通用多Agent通信与编排基础设施，提供消息路由、DAG任务调度、闭环验证、身份认证等通用能力。

**上层（应用场景）**：多智能体协同网络运维，9个Agent协同完成全网监控、故障诊断、自动修复、闭环验证。

**核心定位**：不是做一个网络运维工具，而是用网络运维场景证明 IoA 中间件的通用性和实用性。中间件可复用于任何需要多Agent协同的场景。

### 1.1 当前部署架构说明

> ⚠️ **诚实说明**：当前版本采用**单进程多协程架构**，所有 Agent 运行在同一个 Python 进程中，通过 asyncio 实现并发。这是为了降低演示复杂度和资源占用的工程权衡。
>
> **"多智能体协同"体现在**：
> - 9 个独立 Agent 实例，各有明确职责边界
> - Agent 间通过标准 IoAP 消息协议通信（非函数调用）
> - 多维加权路由引擎根据能力、域、负载等维度自动选择 Agent
> - DAG 调度引擎管理复杂的依赖关系和执行顺序
>
> **演进路线**：已在 [第9节](#9-部署架构与演进路线) 中规划迁移到真正分布式架构的路径。

### 1.2 自研说明

致网科技提供智能体应用开发平台，本方案选择自研 IoA 中间件而非直接使用企业平台——
- 企业平台侧重**单Agent应用开发**，本方案需解决的是跨域多Agent协同这一企业平台未覆盖的问题域
- 自研的多维加权路由引擎、DAG编排引擎是差异化创新点，使用企业平台会掩盖这些技术贡献
- 企业平台作为互补关系——上层Agent可部署在企业平台上，通过IoA中间件实现跨平台协同

---

## 2. 总体架构

```
┌─────────────────────────────────────────────────────────┐
│                    GUI 控制台                              │
│   拓扑视图 │ 消息流 │ 任务DAG │ 仪表盘 │ 运维终端        │
├─────────────────────────────────────────────────────────┤
│                     IoA 中间件层（自研）                   │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐   │
│  │ 注册中心  │ │ 多维加权路由  │ │ DAG调度  │ │ 闭环验证  │   │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘   │
│  ┌──────────┐                                            │
│  │ 身份认证  │                                            │
│  └──────────┘                                            │
├─────────────────────────────────────────────────────────┤
│                     Agent 运行时                          │
│  orchestrator → monitor → diagnoser → repairer → verifier│
│  (LangGraph 状态机)                                      │
├─────────────────────────────────────────────────────────┤
│                     集成 & 数据层                         │
│  网络模拟器 │ MCP Server │ 日志管道 │ SQLite 持久化      │
└─────────────────────────────────────────────────────────┘
```

### 2.1 设计原则

- **松耦合**：Agent 间通过 IoAP 消息协议通信，无直接依赖
- **可观测**：每个DAG节点执行记录、状态、耗时全程可追溯
- **容错性**：节点级重试、超时控制、降级策略
- **可扩展**：新增Agent只需注册能力描述，无需修改路由逻辑

### 2.2 技术选型

| 组件 | 技术 | 选型理由 |
|------|------|----------|
| API 框架 | FastAPI + Uvicorn | 异步高性能，原生WebSocket支持 |
| Agent 框架 | LangGraph | 状态机建模，支持条件分支和循环 |
| LLM | DeepSeek (via API) | 中文能力强，成本低 |
| 数据存储 | SQLite + aiosqlite | 轻量，无需独立数据库服务 |
| 前端 | HTML5 + Chart.js + vis-network.js | 零依赖，实时可视化 |

---

## 3. IoA 中间件设计（自研核心）

### 3.1 能力注册中心 (Agent Registry)

#### 3.1.1 能力描述 JSON Schema

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "IoA Capability Profile",
  "type": "object",
  "required": ["agent_id", "domain", "capabilities", "protocols", "status"],
  "properties": {
    "agent_id":      { "type": "string", "pattern": "^[a-z0-9-]{3,64}$" },
    "domain":        { "type": "string", "enum": ["east-china", "north-china", "south-china", "west-china", "global"] },
    "capabilities":  { "type": "array", "items": { "type": "string" }, "minItems": 1 },
    "protocols":     { "type": "array", "items": { "type": "string" } },
    "model":         { "type": "string" },
    "load":          { "type": "number", "minimum": 0, "maximum": 1 },
    "status":        { "type": "string", "enum": ["active", "degraded", "offline"] },
    "last_heartbeat_ms": { "type": "integer" },
    "endpoint":      { "type": "string", "format": "uri" }
  }
}
```

#### 3.1.2 Agent 能力矩阵

| Agent ID | 域 | 能力标签 | 通信协议 |
|----------|-----|---------|----------|
| orchestrator-agent | global | dag_orchestration, intent_recognition | ioap-v1 |
| monitor-global | global | network_monitoring, metrics_collection | ioap-v1, mcp-v1 |
| diagnoser-global | global | root_cause_analysis, anomaly_detection | ioap-v1 |
| repairer-global | global | fault_repair, configuration_mgmt | ioap-v1 |
| verifier-global | global | closed_loop_verification | ioap-v1 |

#### 3.1.3 生命周期

```
Agent启动 → POST /registry/register → 心跳维持 → Agent关闭 → POST /registry/deregister
                ↑                                      ↑
          失败重试(最多10次, 间隔1s)              自动清理(超时30s无心跳)
```

### 3.2 语义消息路由总线

#### 3.2.1 设计思路

传统Agent间通信为点对点模式（Agent A → Agent B），耦合度高。本系统采用**多维加权路由**：发送方只需声明意图（intent）和目标能力，路由总线根据注册中心的能力描述自动匹配目标Agent。

#### 3.2.2 路由算法

```
Input: message(intent, required_capabilities, domain_preference, task_desc)
Output: matched_agent_id | broadcast_list

1. 从注册中心获取所有 active Agent
2. 能力过滤 — 只保留 capability 标签匹配的 Agent
3. 同步评分（能力 0~0.2 + 域亲和 0~0.3 + 负载 0~0.4）= 0~0.9
4. [可选] LLM 批量语义评分 — 将所有候选 Agent 的能力描述 + 任务描述
   一次发给 LLM，LLM 返回每个 Agent 的语义匹配度（0~1），加权 0.1
5. 同步分 + 语义分 = 最终分，排序选最高
6. LLM 不可用时自动降级为关键词匹配（权重 0.1）
7. 语义匹配结果缓存 5 分钟（LRU，128 条），同类任务免重复调 LLM
```

#### 3.2.3 路由示例

| 发送方 | 意图 | 路由结果 |
|--------|------|----------|
| orchestrator | type=monitoring, domain=east-china | → monitor-global（capability + domain匹配） |
| orchestrator | type=diagnosis | → diagnoser-global（capability匹配，global域） |
| diagnoser | type=repair, fault=link_congestion | → repairer-global（capability匹配） |
| repairer | type=verification | → verifier-global（capability匹配） |

### 3.3 DAG 任务调度引擎

#### 3.3.1 核心数据结构

```json
{
  "dag_id": "dag-remediate-70de908b",
  "template": "full_remediation",
  "nodes": [
    {"node_id": "monitor-1",  "type": "monitor",   "depends_on": []},
    {"node_id": "diagnose-1", "type": "diagnose",  "depends_on": ["monitor-1"]},
    {"node_id": "repair-1",   "type": "repair",    "depends_on": ["diagnose-1"]},
    {"node_id": "verify-1",   "type": "verify",    "depends_on": ["repair-1"]}
  ],
  "status": "completed",
  "submitted_at_ms": 1717200000000,
  "finished_at_ms": 1717200006000
}
```

#### 3.3.2 调度算法（Kahn拓扑排序）

```
1. 构建入度表（从 depends_on 计算）
2. 将入度为0的节点加入就绪队列
3. 循环：
   a. 取出队首节点，分配Agent并执行
   b. 等待节点完成或超时
   c. 将该节点的所有后继节点入度减1
   d. 若后继入度变为0，加入就绪队列
4. 所有节点完成 → DAG状态置为 completed
5. 节点失败 → 根据重试策略决定 retry / fail
```

#### 3.3.3 重试策略

| 触发条件 | 行为 | 最大次数 |
|----------|------|----------|
| Agent无响应（超时30s） | 重新分配Agent | 3次 |
| Agent返回 retry 建议 | 回退到前驱节点重新执行 | 3次 |
| Agent返回 fail | 标记DAG失败，停止执行 | - |

#### 3.3.4 DAG 模板定义

```python
TEMPLATES = {
    "full_remediation": {
        "description": "全流程诊断修复",
        "nodes": [
            {"node_id": "monitor-1",  "type": "monitor",   "depends_on": []},
            {"node_id": "diagnose-1", "type": "diagnose",  "depends_on": ["monitor-1"]},
            {"node_id": "repair-1",   "type": "repair",    "depends_on": ["diagnose-1"]},
            {"node_id": "verify-1",   "type": "verify",    "depends_on": ["repair-1"]}
        ],
        "keywords": ["延迟", "latency", "丢包", "packet_loss", "故障", "fault",
                      "异常", "anomaly", "拥塞", "congestion", "攻击", "ddos"]
    },
    "health_check": {
        "description": "全网健康检查",
        "nodes": [
            {"node_id": "monitor-1", "type": "monitor", "depends_on": []}
        ],
        "keywords": ["健康", "health", "检查", "check", "状态", "status"]
    }
}
```

### 3.4 身份认证

采用 Bearer Token 机制，所有管理API调用需携带 `Authorization: Bearer ioa-dev-only-insecure-key`。

```python
# main.py
async def verify_token(token: str = Header(None)):
    if token != f"Bearer {IOA_TOKEN}":
        raise HTTPException(status_code=401)
```

---

## 4. Agent 运行时设计

### 4.1 orchestrator-agent（编排Agent）

**职责**：接收自然语言指令，意图识别后匹配DAG模板，创建DAG并分发任务。

```
输入: NL指令文本
  ↓
1. 关键词匹配 → 选择DAG模板
2. 解析域参数 → 确定目标区域
3. 创建DAG实例 → 提交给调度器
4. 通过多维加权路由分发节点任务
  ↓
输出: DAG实例ID
```

### 4.2 monitor-global（监控Agent）

**职责**：全网四域指标采集，异常检测。

```
1. GET /sim/simulator/metrics → 全量指标
2. 逐域检查阈值：
   - 延迟 > 100ms → 预警
   - 延迟 > 200ms → 严重
   - 丢包 > 1% → 预警
   - 丢包 > 5% → 严重
   - 带宽 > 85% → 预警
3. 发现异常 → 标记异常域 → 输出给 diagnoser
```

### 4.3 diagnoser-agent（诊断Agent）

**职责**：接收异常指标，LLM推理根因。

```
1. 收集异常域的多维指标（延迟、丢包、带宽、吞吐、连接数）
2. 构建诊断prompt → 调用 DeepSeek API
3. 解析LLM输出 → 输出故障类型 + 置信度
4. 超时降级：规则引擎关键词匹配
```

### 4.4 repairer-agent（修复Agent）

**职责**：执行修复操作。

```
1. 根据诊断结果选择修复策略
2. 执行修复：
   - link_congestion → 流量工程重路由
   - ddos → 流量清洗 + 黑洞路由
   - cpu_overload → 扩容/降载
   - misconfig → 配置回滚
   - device_failure → 切换备用设备
3. 记录修复前后指标（metrics_before / metrics_after）
4. retry轮次特殊处理：若故障已清除，返回 "no repair needed"
```

### 4.5 verifier-agent（验证Agent）

**职责**：闭环验证，详见[第7章](#7-闭环验证系统)。

---

## 5. IoAP 通信协议

### 5.1 消息格式

```json
{
  "msg_id":     "uuid",
  "from_agent": "orchestrator-agent",
  "to_agent":   "monitor-global",
  "intent": {
    "type":        "monitoring",
    "description": "采集全网指标",
    "priority":    "high"
  },
  "payload": {
    "params": {},
    "context": {}
  },
  "correlation_id": "dag-remediate-70de908b",
  "ts_ms":      1717200000000
}
```

### 5.2 状态机

```
CREATED → ROUTED → DELIVERED → PROCESSING → COMPLETED
                     ↓             ↓
                  EXPIRED        FAILED
                     ↓             ↓
                  (丢弃)      (写入死信队列)
```

### 5.3 WebSocket 实时推送

IoA 中间件提供 `/ws/dashboard` 端点，所有 Dashboard 客户端可通过 WebSocket 接收实时推送：
- DAG 节点状态变更
- Agent 上下线通知
- 新消息到达

### 5.4 与 MCP/A2A 协议的关系

IoAP（Internet of Agents Protocol）是自研的 Agent 间通信协议，聚焦多维加权路由和 DAG 编排。与现有标准的关系：

| 协议 | 定位 | 本系统使用 |
|------|------|-----------|
| MCP (Model Context Protocol) | LLM ↔ 工具 交互 | 网络模拟器以 MCP Server 形式暴露工具 |
| A2A (Agent-to-Agent) | Agent ↔ Agent 通信 | IoAP 在语义上兼容 A2A，增加路由层 |
| IoAP (自研) | 多维加权路由 + DAG编排 | Agent协同的核心协议 |

---

## 6. 网络模拟器设计

### 6.1 拓扑结构

```
                    Core-Router
                   /    |    \    \
             Edge-E  Edge-N Edge-S Edge-W
             / | \   / | \  / | \  / | \
           Srv1..3 Srv1..3 Srv1..3 Srv1..3
             华东    华北    华南    西南
```

四域星型拓扑，每个域1个Edge路由器 + 3台服务器，共16个节点。

### 6.2 流量模型

| 指标 | 正常范围 | 故障范围 |
|------|----------|----------|
| 延迟 | 10-20ms | 150-300ms |
| 丢包率 | 0.1-0.3% | 3-8% |
| 带宽利用率 | 60-80% | 85-95% |
| 吞吐量 | 500-800Mbps | 100-200Mbps |
| 连接数 | 200-400 | 50-100 |

### 6.3 故障注入类型

| 故障类型 | 影响指标 | 注入方式 |
|----------|----------|----------|
| link_congestion | 延迟↑↑, 丢包↑, 带宽↑ | `POST /simulator/fault/inject?fault_type=link_congestion&target=east-china` |
| link_outage | 延迟∞, 丢包100%, 带宽0 | 同上 |
| cpu_overload | 延迟↑, 吞吐↓ | 同上 |
| ddos | 延迟↑↑↑, 丢包↑↑, 连接数爆炸 | 同上 |
| misconfig | 延迟波动, 丢包随机 | 同上 |
| device_failure | 连接数=0, 所有指标异常 | 同上 |

---

## 7. 闭环验证系统

### 7.1 验证流程

```
┌─────────────┐
│ repairer 完成│
└──────┬──────┘
       ↓
┌─────────────┐
│ 采集当前指标 │ ← GET /sim/simulator/metrics
└──────┬──────┘
       ↓
┌─────────────┐     实时指标兜底检查
│ 指标是否正常？│ ──yes──→ verdict = pass
└──────┬──────┘
       ↓ no
┌─────────────────┐
│ 计算 improvement │ = (before - after) / before
└──────┬──────────┘
       ↓
┌─────────────────┐
│ improvement ≥ 30%│ ──yes──→ verdict = pass
└──────┬──────────┘
       ↓ no
┌─────────────────┐
│ retries < 3 ?   │ ──yes──→ verdict = retry
└──────┬──────────┘
       ↓ no
verdict = fail → 人工介入
```

### 7.2 验证阈值

| 指标 | pass阈值 | 兜底正常值 |
|------|----------|------------|
| 延迟 | improvement ≥ 30% | 绝对值 ≤ 100ms |
| 丢包率 | improvement ≥ 50% | 绝对值 ≤ 1% |
| 带宽利用率 | improvement ≥ 20% | 绝对值 ≤ 85% |

### 7.3 关键设计决策

**问题**：DDoS 场景中，repairer 清除故障后模拟器指标仍在高位，verifier 在 retry 轮次采集到的 before/after 都是正常值，improvement 接近 0，永远不满足 30% 阈值，形成死循环。

**解决方案**：在 improvement 校验之前增加**实时指标兜底检查**——如果当前所有指标都在正常范围内（延迟≤100ms，丢包≤1%，带宽≤85%），不论 improvement 值几何，直接判定 `pass`。

这解决了"修复已生效但时序问题导致对比数据无意义"的通用场景。

---

## 8. 安全与信任

### 8.1 通信安全

- **传输层**：支持 HTTPS/WSS 加密（可选配置）
- **应用层**：Bearer Token 认证（所有管理API）
- **输入验证**：pydantic 模型自动校验消息格式

### 8.2 Agent 身份信任

- 注册时校验 agent_id 命名规则
- 心跳超时自动标记 offline，防止过期Agent接收消息
- DAG执行记录完整审计链（dag_id → node_id → agent_id → status → timestamps）

### 8.3 审计日志

每个DAG节点执行记录包含：
- 开始/结束时间戳
- 分配的Agent
- 执行状态（pending/assigned/running/completed/failed/retrying）
- 输出结果
- 重试次数

---

## 9. 评估指标

### 9.1 系统性能

| 指标 | 目标值 | 实测值 |
|------|--------|--------|
| DAG端到端耗时（link_congestion） | < 15s | ~6s |
| DAG端到端耗时（DDoS） | < 15s | ~6s |
| Agent注册延迟 | < 3s | ~1s |
| 消息路由延迟 | < 100ms | ~50ms |
| 内存占用（5 Agent） | < 1GB | ~500MB |
| 可用性（7天运行） | > 99% | 持续运行中 |

### 9.2 功能完整性

| 功能 | 状态 |
|------|------|
| 自然语言指令 → DAG 编排 | ✅ |
| 语义消息路由（能力匹配） | ✅ |
| DAG Kahn拓扑排序 | ✅ |
| 节点重试 + 超时控制 | ✅ |
| 闭环验证（三态判定） | ✅ |
| 实时指标兜底 | ✅ |
| 6种故障注入类型 | ✅ |
| WebSocket实时推送 | ✅ |
| GUI拓扑图 + 仪表盘 + DAG可视化 | ✅ |
| 本地运行 | ✅ |

---

## 10. 部署架构与演进路线

### 10.1 当前架构（单进程多协程）

```
┌─────────────────────────────────────────────────────────────┐
│                    单 Python 进程                            │
│  ┌─────────────────────────────────────────────────────┐   │
│  │                FastAPI (Uvicorn)                     │   │
│  │  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐   │   │
│  │  │Orchestr.│ │Monitor×4│ │Diagnoser│ │Repairer │   │   │
│  │  │  Agent  │ │  Agents │ │  Agent  │ │  Agent  │   │   │
│  │  └────┬────┘ └────┬────┘ └────┬────┘ └────┬────┘   │   │
│  │       │           │           │           │         │   │
│  │  ┌────┴───────────┴───────────┴───────────┴────┐    │   │
│  │  │         asyncio 事件循环                     │    │   │
│  │  │    (WebSocket + HTTP + 定时任务)             │    │   │
│  │  └─────────────────────────────────────────────┘    │   │
│  └─────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

**优点**：
- 部署简单，单进程即可运行
- 资源占用低（~500MB 内存）
- 无网络通信开销，延迟低

**局限**：
- 单点故障：进程崩溃影响所有 Agent
- 无法跨机器部署
- 受 Python GIL 限制，CPU 密集任务无法真正并行

### 10.2 演进路线图

#### 阶段 1：多进程部署（短期）

```
┌───────────┐  ┌───────────┐  ┌───────────┐  ┌───────────┐
│Orchestrator│  │ Monitor×4 │  │ Diagnoser │  │ Repairer  │
│  进程      │  │  进程     │  │  进程     │  │  进程     │
└─────┬─────┘  └─────┬─────┘  └─────┬─────┘  └─────┬─────┘
      │              │              │              │
      └──────────────┴──────────────┴──────────────┘
                           │
                    ┌──────┴──────┐
                    │  Redis/NATS │
                    │  消息队列   │
                    └─────────────┘
```

- 每个 Agent 类型独立进程
- 使用消息队列替代内存队列
- 进程间通过消息队列通信

#### 阶段 2：容器化部署（中期）

```
┌─────────────────────────────────────────────────────────────┐
│                    Kubernetes 集群                           │
│  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐          │
│  │ Orch.   │ │ Monitor │ │ Diagn.  │ │ Repair. │          │
│  │ Pod     │ │ Pod ×4  │ │ Pod     │ │ Pod     │          │
│  └────┬────┘ └────┬────┘ └────┬────┘ └────┬────┘          │
│       │           │           │           │                │
│  ┌────┴───────────┴───────────┴───────────┴────┐           │
│  │              Service Mesh (Istio)            │           │
│  └─────────────────────────────────────────────┘           │
│  ┌─────────────────────────────────────────────┐           │
│  │         Redis Cluster (消息 + 状态)         │           │
│  └─────────────────────────────────────────────┘           │
└─────────────────────────────────────────────────────────────┘
```

- Docker 容器化每个 Agent
- Kubernetes 编排和自动扩缩容
- Service Mesh 实现服务发现和负载均衡

#### 阶段 3：真正分布式（长期）

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│   华东节点    │     │   华北节点    │     │   华南节点    │
│ ┌──────────┐ │     │ ┌──────────┐ │     │ ┌──────────┐ │
│ │ Monitor  │ │     │ │ Monitor  │ │     │ │ Monitor  │ │
│ │ Agent    │ │     │ │ Agent    │ │     │ │ Agent    │ │
│ └──────────┘ │     │ └──────────┘ │     │ └──────────┘ │
└──────┬───────┘     └──────┬───────┘     └──────┬───────┘
       │                    │                    │
       └────────────────────┼────────────────────┘
                            │
                     ┌──────┴──────┐
                     │  中心编排    │
                     │  节点       │
                     └─────────────┘
```

- Agent 按域分布在不同物理节点
- 跨域通信通过 IoAP 协议
- 真正的故障隔离和高可用

### 10.3 技术选型建议

| 阶段 | 消息队列 | 服务发现 | 容器编排 |
|------|---------|---------|---------|
| 阶段1 | Redis Pub/Sub | Consul | - |
| 阶段2 | NATS JetStream | K8s Service | Kubernetes |
| 阶段3 | NATS JetStream | K8s + DNS | Kubernetes |

---

## 11. 已知限制与改进方向

### 11.1 当前已知限制

| 限制 | 影响 | 改进方向 |
|------|------|---------|
| 单进程架构 | 单点故障 | 迁移到多进程/容器化 |
| SQLite 单连接 | 并发写瓶颈 | 启用 WAL 模式，考虑 PostgreSQL |
| 多维加权路由使用关键词匹配 | "语义"程度有限 | 升级为 embedding 向量匹配 |
| A2A/MCP 集成较浅 | 跨协议协同不充分 | 补充实际跨协议案例 |
| 单一验证场景 | 通用性证明不足 | 添加第二个应用场景 |

### 11.2 多维加权路由说明

> ✅ **已升级**：语义部分已从关键词匹配升级为 **LLM 批量语义评估**。
>
> 路由时将所有候选 Agent 的能力描述 + 任务描述**一次发给 DeepSeek LLM**，
> LLM 理解任务意图与 Agent 能力的语义匹配度，返回每个 Agent 的评分（0~1）。
>
> **缓存策略**：LRU 缓存（128 条，5 分钟 TTL），同类任务直接命中缓存，无需反复调 LLM。
>
> **降级策略**：LLM 不可用时自动回退到关键词匹配，保证系统稳定。
>
> **演进方向**：引入 sentence-transformers embedding 做向量相似度，延迟更低（本地 <50ms vs LLM ~300ms）。

---

*文档版本: v3.0 | 最后更新: 2026-06-04*
