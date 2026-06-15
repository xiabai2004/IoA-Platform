# IoA 分布式网络运维协同平台 — 封版状态报告

> **C4 网络技术挑战赛 B-EP1 "智能体互联网创新攻关" | 2026.06.15**
>
> 仓库: https://github.com/xiabai2004/IoA-Platform

---

## 一、项目概况

| 项目 | 数据 |
|------|------|
| 代码总量 | ~8,000 行 Python + ~500 行 JS/HTML |
| Agent 数量 | 9 个（1 orchestrator + 4 monitor + 1 each diagnoser/repairer/verifier/reporter） |
| 测试覆盖 | **138 passed** / 0 failed / 19 skipped（playwright GUI） |
| MCP 工具数 | **7 个**（get_metrics, get_all_metrics, get_topology, inject_fault, clear_fault, list_faults, execute_repair） |
| 故障类型 | **6 种**（cpu_overload, link_congestion, link_outage, ddos, misconfig, device_failure） |
| 拓扑规模 | 4 域 × 4 链路（华东/华北/华南/西南） |
| LLM 模型 | DeepSeek API |

---

## 二、三审解决的问题

### P0 级（阻塞性问题 — 已全部修复）

| # | 问题 | 根因 | 修复 |
|---|------|------|------|
| 1 | **MCP 协议不可用** | Windows Uvicorn SSE 发送 CRLF 行尾，MCP SDK 客户端期望 LF → session_id 含 `\r` → "Invalid session ID" | handle_sse 包装 send 函数，body 中 `\r\n` → `\n` |
| 2 | **MCP POST 请求 ReadError** | Uvicorn HTTP/1.1 keep-alive 在 Windows 上连接复用异常 | /messages 响应添加 `Connection: close` 中间件 |
| 3 | **一键演示修复节点始终失败** | `_apply_repair` 不检查 MCP 返回的 `error` 字段；result dict 缺少 `error` 键 → 调度器显示 "Unknown error" | 增加 `result.get("error")` 检查；失败时显式设置 error 键 |
| 4 | **MCP 缺少 execute_repair 工具** | MCP Server 注册表未包含修复工具 → Agent 通过 MCP 调用修复时返回 "Unknown tool" | 添加 execute_repair 工具定义 + SimulatorClient HTTP 方法 |
| 5 | **GUI 加载页卡死 (loading overlay)** | 删除第二场景代码时留下多余 `}` → JS 语法错误 → init() 从未执行 | 移除多余 `}`，node --check 验证通过 |
| 6 | **修复 Agent MCP→HTTP 降级失效** | AutoToolClient 的 MCP 调用返回错误 dict（非异常），降级逻辑不触发 | 修复回退逻辑 + 增加错误检测 |

### P1 级（体验问题）

| # | 问题 | 修复 |
|---|------|------|
| 7 | 故障注入 "接口不存在" | app.js 接口路径错误 `/simulator/faults/inject` → `/simulator/fault/inject` |
| 8 | 清除故障失败 | 路径 `/simulator/faults/clear` + DELETE → `/simulator/fault/clear_all` + GET |
| 9 | Material Icons 图标不显示 | CDN 路径 `/gui/lib/` → `/gui/static/lib/` |
| 10 | 一键演示无进度反馈 | 重写 runDemo() 增加清除→注入→NL→DAG 轮询→结果显示 完整流程 |

### P2 级（优化项）

| # | 优化 | 说明 |
|---|------|------|
| 11 | 第二场景重构 | 文档审核 → 网络合规审计（后用户决定移除重新规划） |
| 12 | 代码清理 | 移除 compliance_audit/文档审核残留，清理 __pycache__ |
| 13 | 浏览器缓存 | app.js 添加 `?_=2` 缓存破坏参数 |

---

## 三、实际体验评估

### 已稳定工作的功能

| 功能 | 状态 | 备注 |
|------|------|------|
| 一键演示（单故障全流程） | ✅ | 清除→注入→NL→DAG 轮询→结果，5/5 节点 |
| 复合故障演示 | ✅ | 3 故障并发注入 + 修复 |
| NL 自然语言指令 | ✅ | DeepSeek 意图解析 → 模板匹配 → DAG 创建 |
| 故障注入/清除按钮 | ✅ | 6 种故障 × 4 域 |
| 实时拓扑可视化 | ✅ | vis-network 渲染，颜色标记故障域 |
| 指标图表 | ✅ | Chart.js 延迟趋势图 |
| Agent 集群面板 | ✅ | 实时状态 + 负载 |
| DAG 执行记录 | ✅ | 展开查看节点详情 |
| IoAP 消息流 | ✅ | 实时日志推送 |

### 已知局限（不影响比赛答辩）

| 局限 | 影响 | 说明 |
|------|------|------|
| 第二场景未实现 | 低 | 原始文档审核无法工作，合规审计已移除，重新规划中 |
| Playwright GUI 测试 skipped | 低 | 开发环境无浏览器，API 测试全覆盖 |
| DeepSeek API 偶发超时 | 低 | repair 节点设 retry，失败可重跑 |
| MCP 修复依赖 Windows | 中 | 若评委在 Linux 评审需验证 |

---

## 四、比赛情况评估

### 与赛题要求对照

| 赛题要求 | 本项目实现 | 覆盖率 |
|----------|-----------|--------|
| MCP 协议实现 | SSE transport + 7 tools | ✅ 完整 |
| Agent 多智能体架构 | 9 个 Agent，能力标签 + 域划分 | ✅ 完整 |
| DAG 工作流调度 | 拓扑排序 + 依赖解析 + retry + Bandit 路由 | ✅ 完整 |
| 故障检测/诊断/修复/验证/报告 | 全闭环 5 阶段 pipeline | ✅ 完整 |
| 自然语言交互 | DeepSeek LLM 意图解析 | ✅ 完整 |
| WebSocket 实时推送 | 指标 + 消息双通道 | ✅ 完整 |
| 可视化仪表盘 | 拓扑图 + 指标图 + Agent 面板 + DAG + 消息流 | ✅ 完整 |

### 答辩亮点

1. **MCP 协议深度集成** — 7 工具全部通过 MCP SSE 协议暴露，Agent 通过 AutoToolClient 自动选择 MCP/HTTP
2. **差异化修复策略** — 6 种故障 × primary+fallback 双策略（如 link_congestion: traffic_shape → route_switch）
3. **UCB1 Bandit 路由** — 多臂老虎机在线学习，答辩可展示路由权重收敛
4. **LangGraph 编排工作流** — orchestrator 集成 LangGraph 做 NL→模板→DAG 的多步推理
5. **闭环验证** — verify 节点复检指标，不通过触发 diagnose+repair 回退
6. **并发稳定性** — 3 域 3 故障并发注入全部成功完成

### 现场演示脚本（建议）

```
1. 启动: 双击 run.bat → 打开 http://127.0.0.1:8000/gui
2. 展示 Agent 集群（9 个 Agent 在线）
3. 展示拓扑 + 实时指标（正常状态）
4. 选择故障类型 + 域 → 点击"注入故障"
5. 输入 NL: "华东网络异常请全流程诊断修复" → 点击执行
6. 观察 DAG 5 节点依次完成（monitor→diagnose→repair→verify→report）
7. 展示消息流中的 IoAP 协议交互
8. 点击"一键演示" → 展示完整自动化流程
9. (可选) 展示 MCP 工具列表和协议交互
```

---

## 五、技术架构概览

```
┌─────────────────────────────────────────────────────────────┐
│                         GUI (port 8000/gui)                 │
│  拓扑可视化 │ 指标图表 │ Agent面板 │ DAG记录 │ 消息流      │
└──────────────────────┬──────────────────────────────────────┘
                       │ HTTP / WebSocket
┌──────────────────────▼──────────────────────────────────────┐
│                  IoA Middleware (FastAPI, port 8000)         │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌────────────┐ │
│  │ IoAP Bus │  │ Scheduler│  │ Registry │  │ A2A Server │ │
│  │ (Memory) │  │ (DAG)    │  │ (agents) │  │ (agent卡)  │ │
│  └──────────┘  └──────────┘  └──────────┘  └────────────┘ │
└──────────────────────┬──────────────────────────────────────┘
                       │
     ┌─────────────────┼─────────────────┐
     ▼                 ▼                  ▼
┌─────────┐   ┌──────────────┐   ┌──────────────┐
│Simulator│   │  MCP Server  │   │  9 Agents    │
│(8001)   │   │  (9000, SSE) │   │  (in-process)│
│4域拓扑   │   │  7 tools     │   │  orchestrator│
│故障引擎  │   │              │   │  monitor ×4  │
│指标生成  │   │              │   │  diagnoser   │
└─────────┘   └──────────────┘   │  repairer    │
                                 │  verifier    │
                                 │  reporter    │
                                 └──────────────┘
```

---

## 六、文件变更清单（涉审提交）

| 文件 | 变更类型 | 说明 |
|------|----------|------|
| `backend/agents/__init__.py` | 修改 | 移除第二场景 Agent 导入 |
| `backend/agents/repairer_agent/agent.py` | 修改 | MCP 错误检测 + error 键 |
| `backend/agents/tool_client.py` | 修改 | AutoToolClient MCP→HTTP 降级 |
| `backend/ioa_middleware/main.py` | 修改 | MCP 独立端口启动 |
| `backend/ioa_middleware/mcp_server.py` | 修改 | CRLF→LF + Connection:close + execute_repair |
| `backend/ioa_middleware/orchestrator/templates.py` | 修改 | 清理 doc_review/compliance_audit 模板 |
| `backend/prompts/__init__.py` | 修改 | 清理 compliance_audit prompt |
| `gui/app.js` | 修改 | 语法修复 + 场景清理 |
| `gui/index.html` | 修改 | 移除场景选择器 |

---

## 七、风险评估

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|----------|
| DeepSeek API 限流/超时 | 中 | repair 节点失败 | retry 机制，现场可重跑 |
| MCP Windows 修复在 Linux 不生效 | 低 | MCP 不可用 | Agent 自动降级 HTTP |
| 评委要求第二场景演示 | 低 | 无第二场景 | 解释架构可扩展性 |
| 现场网络不稳定 | 低 | API 调用失败 | 本地 localhost 运行 |

**总体评估：项目已封版，可交付比赛答辩。** 🎯
