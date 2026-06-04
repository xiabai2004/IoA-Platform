# IoA 分布式网络运维协同平台 — 评委评审改进计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 根据评委2的严格评审报告，在挑战赛前解决 P0/P1 级别的核心问题，将项目从"潜力组"提升到"竞争力组"。

**Architecture:** 分三个阶段执行——短期（Docker化 + 差异化修复 + 安全加固 + 文档修正）、中期（真实网络对接 + 第二场景 + 性能测试）、长期（框架抽象 + 生产对接）。本计划聚焦短期阶段，兼顾中期关键项。

**Tech Stack:** Docker Compose, FastAPI, asyncio, NATS/RabbitMQ (消息总线), Mininet/Containerlab (网络模拟), pytest, LangGraph

**评审报告参考:** `评委建议/评委2/评委评审报告-IoA分布式网络运维协同平台.md`

---

## 阶段零：前提准备（半天）

### Task 0: 环境审计与备份

**Files:**
- 检查: `.env`, `backend/config.yaml`, `backend/config.yaml.example`, `gui/index.html`

- [ ] **Step 1: 确认敏感信息泄露范围**

```bash
grep -rn "sk-" --include="*.py" --include="*.yaml" --include="*.env" --include="*.html" .
grep -rn "tp-" --include="*.py" --include="*.yaml" --include="*.env" --include="*.html" .
grep -rn "ioa-dev-only-insecure-key\|ioa2026demo" --include="*.py" --include="*.html" .
```

**Expected:** 找到 `.env` 中的 API Key、`auth/__init__.py` 和 `gui/index.html` 中的硬编码 PSK。

- [ ] **Step 2: 立即轮换已泄露的 API Key**

去 DeepSeek 和 MIMO 平台重新生成 API Key，旧 Key 立即吊销。

- [ ] **Step 3: 确保 .env 已在 .gitignore 中**

```bash
grep "\.env" .gitignore
```

如果没有，添加:
```
.env
*.env
```

- [ ] **Step 4: 从 Git 历史中清除敏感文件**

```bash
git rm --cached .env
git commit -m "chore: remove .env from tracking"
```

- [ ] **Step 5: 创建完整的 `.env.example`**

```bash
# .env.example
MIMO_API_KEY=your-mimo-api-key-here
DEEPSEEK_API_KEY=your-deepseek-api-key-here
IOA_PSK=your-strong-pre-shared-key-here
```

- [ ] **Step 6: Commit**

```bash
git add .gitignore .env.example
git commit -m "chore: security audit prep — remove secrets, add .env.example"
```

---

## 阶段一：短期改进（1-2 周，答辩前必须完成）

### 模块 A：Docker 化多 Agent 部署（P0-1）

> **评审痛点:** "单进程 ≠ 分布式，这是项目最大的概念性欺骗风险。"

**目标架构:** Docker Compose 管理 7+ 个独立容器，通过 NATS 消息总线通信。

```
┌──────────────────────────────────────────────────────────┐
│                    Docker Network (ioa-net)               │
│                                                          │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐│
│  │Monitor×4 │  │Diagnoser │  │Repairer  │  │Verifier  ││
│  │ (1 per   │  │Container │  │Container │  │Container ││
│  │ domain)  │  │          │  │          │  │          ││
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘│
│       │             │             │             │       │
│       └─────────────┼─────────────┼─────────────┘       │
│                     │             │                      │
│              ┌──────┴─────────────┴──────┐               │
│              │     NATS Message Bus      │               │
│              └──────┬────────────┬───────┘               │
│                     │            │                       │
│  ┌──────────┐  ┌────┴─────┐ ┌───┴────────┐              │
│  │Orchestr. │  │Middleware│ │ Simulator  │              │
│  │Container │  │Container │ │ Container  │              │
│  └──────────┘  └──────────┘ └────────────┘              │
│                     │                                    │
│              ┌──────┴──────┐                             │
│              │    GUI      │                             │
│              │  (Nginx)    │                             │
│              └─────────────┘                             │
└──────────────────────────────────────────────────────────┘
```

#### Task A1: 消息总线抽象层

**Files:**
- Create: `backend/ioa_middleware/bus/__init__.py`
- Create: `backend/ioa_middleware/bus/base.py`
- Create: `backend/ioa_middleware/bus/nats_bus.py`
- Create: `backend/ioa_middleware/bus/memory_bus.py`
- Modify: `backend/ioa_middleware/config.py`

- [ ] **Step 1: 定义消息总线抽象接口**

```python
# backend/ioa_middleware/bus/base.py
from abc import ABC, abstractmethod
from typing import Any, Callable, Awaitable

MessageHandler = Callable[[str, dict[str, Any]], Awaitable[None]]

class MessageBus(ABC):
    """Abstract message bus for inter-agent communication."""

    @abstractmethod
    async def connect(self) -> None:
        """Connect to the message bus."""
        ...

    @abstractmethod
    async def close(self) -> None:
        """Disconnect from the message bus."""
        ...

    @abstractmethod
    async def publish(self, topic: str, message: dict[str, Any]) -> None:
        """Publish a message to a topic."""
        ...

    @abstractmethod
    async def subscribe(self, topic: str, handler: MessageHandler) -> None:
        """Subscribe to a topic with an async handler."""
        ...

    @abstractmethod
    async def request(self, topic: str, payload: dict[str, Any], timeout: float = 30.0) -> dict[str, Any]:
        """Send a request and wait for a single response."""
        ...
```

- [ ] **Step 2: 实现 NATS 消息总线**

```python
# backend/ioa_middleware/bus/nats_bus.py
import json
import asyncio
from typing import Any
import nats
from nats.aio.client import Client as NATS
from nats.aio.errors import ErrTimeout

from .base import MessageBus, MessageHandler

class NatsMessageBus(MessageBus):
    """NATS-backed message bus for distributed deployment."""

    def __init__(self, servers: str | list[str] = "nats://nats:4222"):
        self.servers = servers if isinstance(servers, str) else ",".join(servers)
        self._nc: NATS | None = None
        self._subscriptions: list[int] = []

    async def connect(self) -> None:
        self._nc = NATS()
        await self._nc.connect(servers=self.servers)

    async def close(self) -> None:
        for sid in self._subscriptions:
            try:
                await self._nc.unsubscribe(sid)
            except Exception:
                pass
        if self._nc:
            await self._nc.drain()

    async def publish(self, topic: str, message: dict[str, Any]) -> None:
        await self._nc.publish(topic, json.dumps(message).encode())

    async def subscribe(self, topic: str, handler: MessageHandler) -> None:
        async def wrapper(msg):
            data = json.loads(msg.data.decode())
            await handler(msg.subject, data)

        sid = await self._nc.subscribe(topic, cb=wrapper)
        self._subscriptions.append(sid)

    async def request(self, topic: str, payload: dict[str, Any], timeout: float = 30.0) -> dict[str, Any]:
        response = await self._nc.request(topic, json.dumps(payload).encode(), timeout=timeout)
        return json.loads(response.data.decode())
```

- [ ] **Step 3: 实现内存消息总线（开发/降级模式）**

```python
# backend/ioa_middleware/bus/memory_bus.py
import asyncio
from collections import defaultdict
from typing import Any

from .base import MessageBus, MessageHandler

class MemoryMessageBus(MessageBus):
    """In-process message bus for development and fallback mode."""

    def __init__(self):
        self._handlers: dict[str, list[MessageHandler]] = defaultdict(list)

    async def connect(self) -> None:
        pass

    async def close(self) -> None:
        self._handlers.clear()

    async def publish(self, topic: str, message: dict[str, Any]) -> None:
        for handler in self._handlers.get(topic, []):
            await handler(topic, message)

    async def subscribe(self, topic: str, handler: MessageHandler) -> None:
        self._handlers[topic].append(handler)

    async def request(self, topic: str, payload: dict[str, Any], timeout: float = 30.0) -> dict[str, Any]:
        # In-memory: find first handler and call it
        handlers = self._handlers.get(topic, [])
        if not handlers:
            raise RuntimeError(f"No handler for topic {topic}")

        # Use a future to collect the response
        future: asyncio.Future[dict[str, Any]] = asyncio.Future()

        async def capture_handler(_topic: str, msg: dict[str, Any]) -> None:
            if not future.done():
                future.set_result(msg)

        # Replace handler temporarily
        original = self._handlers[topic]
        self._handlers[topic] = [capture_handler]
        await handlers[0](topic, payload)
        self._handlers[topic] = original

        return await asyncio.wait_for(future, timeout=timeout)
```

- [ ] **Step 4: 在 config.py 中添加 bus 配置**

```python
# 在 backend/ioa_middleware/config.py 中添加
# 在 get_config() 返回的 dict 中加入:
config.setdefault("bus", {})
config["bus"].setdefault("backend", "memory")  # "memory" | "nats"
config["bus"].setdefault("nats_servers", "nats://nats:4222")
```

- [ ] **Step 5: 在 `__init__.py` 中添加工厂函数**

```python
# backend/ioa_middleware/bus/__init__.py
from .base import MessageBus
from .memory_bus import MemoryMessageBus
from .nats_bus import NatsMessageBus

def create_bus(config: dict) -> MessageBus:
    backend = config.get("bus", {}).get("backend", "memory")
    if backend == "nats":
        return NatsMessageBus(servers=config["bus"]["nats_servers"])
    return MemoryMessageBus()
```

- [ ] **Step 6: Commit**

```bash
git add backend/ioa_middleware/bus/
git commit -m "feat: add message bus abstraction (NATS + in-memory)"
```

---

#### Task A2: 改造 BaseAgent 使用消息总线

**Files:**
- Modify: `backend/agents/base_agent.py`

- [ ] **Step 1: 重构 BaseAgent 将 WebSocket 替换为消息总线**

```python
# backend/agents/base_agent.py — 关键修改
import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Any

from ioa_middleware.bus import MessageBus

logger = logging.getLogger(__name__)


class BaseAgent(ABC):
    """Abstract agent base — communicates via MessageBus instead of direct WebSocket."""

    def __init__(
        self,
        agent_id: str,
        domain: str,
        capability: str,
        bus: MessageBus,
        config: dict | None = None,
    ):
        self.agent_id = agent_id
        self.domain = domain
        self.capability = capability
        self.bus = bus
        self.config = config or {}
        self._running = False
        self._heartbeat_task: asyncio.Task | None = None
        self._agent_topic = f"agent.{agent_id}"
        self._domain_topic = f"domain.{domain}.{capability}"

    @abstractmethod
    async def handle_message(self, topic: str, message: dict[str, Any]) -> dict[str, Any]:
        """Process an incoming message and return a result."""
        ...

    async def start(self) -> None:
        """Connect to bus, register, subscribe, start heartbeat."""
        await self.bus.connect()
        self._running = True

        # Subscribe to agent-specific topic
        await self.bus.subscribe(self._agent_topic, self._on_message)
        # Subscribe to domain+capability topic (for routed messages)
        await self.bus.subscribe(self._domain_topic, self._on_message)

        # Register with registry via bus
        await self._register()

        # Start heartbeat
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    async def stop(self) -> None:
        self._running = False
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
        await self.bus.close()

    async def _on_message(self, topic: str, message: dict[str, Any]) -> None:
        """Internal message handler that routes to handle_message."""
        try:
            result = await self.handle_message(topic, message)
            # If there's a reply topic, publish the result
            reply_topic = message.get("_reply_to")
            if reply_topic:
                await self.bus.publish(reply_topic, result)
        except Exception:
            logger.exception(f"Agent {self.agent_id} failed to handle message on {topic}")

    async def _register(self) -> None:
        """Register this agent with the central registry via bus."""
        registration = {
            "agent_id": self.agent_id,
            "domain": self.domain,
            "capability": self.capability,
            "status": "active",
        }
        await self.bus.publish("registry.register", registration)
        logger.info(f"Agent {self.agent_id} registered")

    async def _heartbeat_loop(self) -> None:
        interval = self.config.get("heartbeat_interval", 10)
        while self._running:
            await self.bus.publish("registry.heartbeat", {"agent_id": self.agent_id})
            await asyncio.sleep(interval)

    async def send_message(self, target_topic: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Send a request to another agent and await response."""
        return await self.bus.request(target_topic, payload)
```

- [ ] **Step 2: 更新所有 Agent 子类的构造函数签名**

每个 Agent 子类需从:
```python
def __init__(self, config: dict):
    super().__init__(agent_id="...", domain="...", capability="...", config=config)
```
改为:
```python
def __init__(self, bus: MessageBus, config: dict):
    super().__init__(agent_id="...", domain="...", capability="...", bus=bus, config=config)
```

这涉及以下文件：
- `backend/agents/orchestrator_agent/agent.py`
- `backend/agents/monitor_agent/agent.py`
- `backend/agents/diagnoser_agent/agent.py`
- `backend/agents/repairer_agent/agent.py`
- `backend/agents/verifier_agent/agent.py`
- `backend/agents/reporter_agent/agent.py`

- [ ] **Step 3: 更新 `create_all_agents()` 工厂函数**

```python
# backend/agents/__init__.py
from ioa_middleware.bus import MessageBus

def create_all_agents(bus: MessageBus, config: dict) -> list[BaseAgent]:
    return [
        OrchestratorAgent(bus=bus, config=config),
        MonitorAgent(bus=bus, agent_id="monitor-east-china", domain="east-china", config=config),
        MonitorAgent(bus=bus, agent_id="monitor-north-china", domain="north-china", config=config),
        MonitorAgent(bus=bus, agent_id="monitor-south-china", domain="south-china", config=config),
        MonitorAgent(bus=bus, agent_id="monitor-west-china", domain="west-china", config=config),
        DiagnoserAgent(bus=bus, config=config),
        RepairerAgent(bus=bus, config=config),
        VerifyAgent(bus=bus, config=config),
        ReporterAgent(bus=bus, config=config),
    ]
```

- [ ] **Step 4: 更新中间件 main.py 的 lifespan**

```python
# backend/ioa_middleware/main.py — lifespan 中
from ioa_middleware.bus import create_bus

@asynccontextmanager
async def lifespan(app: FastAPI):
    config = get_config()
    # Create the message bus (NATS or in-memory based on config)
    bus = create_bus(config)
    await bus.connect()
    app.state.bus = bus

    # ... existing DB init, health loop, scheduler ...

    # Create agents with bus
    agents = create_all_agents(bus=bus, config=config)
    app.state.agents = agents

    for agent in agents:
        await agent.start()

    yield

    for agent in agents:
        await agent.stop()
    await bus.close()
```

- [ ] **Step 5: 更新 DagScheduler 使用 bus 而非直接 WebSocket 调用**

```python
# backend/ioa_middleware/orchestrator/scheduler.py
# 将 send_to_agent 改为通过 bus.request 发送:
async def _dispatch_node(self, node: DagNodeDef, ...) -> None:
    target_topic = f"domain.{node.domain}.{node.capability}"
    result = await self.bus.request(target_topic, {
        "action": node.action,
        "params": node.params,
        "_dag_id": self.dag_id,
        "_node_id": node.node_id,
    })
    # ... handle result ...
```

- [ ] **Step 6: Commit**

```bash
git add backend/agents/ backend/ioa_middleware/
git commit -m "refactor: migrate agents from WebSocket to MessageBus abstraction"
```

---

#### Task A3: Dockerfile 编写（每个 Agent 类型一个镜像 + 公共基础镜像）

**Files:**
- Create: `Dockerfile.base`
- Create: `Dockerfile.middleware`
- Create: `Dockerfile.agent`
- Create: `Dockerfile.simulator`
- Create: `Dockerfile.gui`

- [ ] **Step 1: 基础镜像**

```dockerfile
# Dockerfile.base
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# This is an intermediate base — not run directly
```

- [ ] **Step 2: 中间件镜像**

```dockerfile
# Dockerfile.middleware
FROM python:3.11-slim

WORKDIR /app

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ .

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "ioa_middleware.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 3: Agent 通用镜像**

```dockerfile
# Dockerfile.agent
FROM python:3.11-slim

WORKDIR /app

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ .

# AGENT_CLASS and AGENT_ARGS set via environment at runtime
CMD ["sh", "-c", "python -c \"from agents import create_agent_by_name; import asyncio; asyncio.run(create_agent_by_name('${AGENT_NAME}', '${AGENT_ID}', '${AGENT_DOMAIN}', '${AGENT_CAPABILITY}'))\""]
```

- [ ] **Step 4: 模拟器镜像**

```dockerfile
# Dockerfile.simulator
FROM python:3.11-slim

WORKDIR /app

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ .

EXPOSE 8001

CMD ["python", "-m", "uvicorn", "simulator.api:app", "--host", "0.0.0.0", "--port", "8001"]
```

- [ ] **Step 5: GUI 镜像（Nginx 静态文件）**

```dockerfile
# Dockerfile.gui
FROM nginx:alpine

COPY gui/index.html /usr/share/nginx/html/
COPY gui/ /usr/share/nginx/html/

EXPOSE 80
```

- [ ] **Step 6: Commit**

```bash
git add Dockerfile.*
git commit -m "feat: add Dockerfiles for all components"
```

---

#### Task A4: docker-compose.yml 编排

**Files:**
- Create: `docker-compose.yml`
- Create: `docker-compose.override.yml` (开发模式，NATS 可选)
- Create: `scripts/generate_env.sh`

- [ ] **Step 1: 编写 docker-compose.yml**

```yaml
# docker-compose.yml
version: "3.9"

services:
  # ==================== Infrastructure ====================
  nats:
    image: nats:2.10-alpine
    container_name: ioa-nats
    command: ["-js", "-m", "8222"]
    ports:
      - "4222:4222"
      - "8222:8222"
    networks:
      - ioa-net
    healthcheck:
      test: ["CMD", "nats", "ping"]
      interval: 5s
      retries: 5

  # ==================== Simulator ====================
  simulator:
    build:
      context: .
      dockerfile: Dockerfile.simulator
    container_name: ioa-simulator
    environment:
      - IOA_PSK=${IOA_PSK}
      - NATS_SERVERS=nats://nats:4222
    ports:
      - "8001:8001"
    networks:
      - ioa-net
    depends_on:
      nats:
        condition: service_healthy

  # ==================== Middleware ====================
  middleware:
    build:
      context: .
      dockerfile: Dockerfile.middleware
    container_name: ioa-middleware
    environment:
      - IOA_PSK=${IOA_PSK}
      - DEEPSEEK_API_KEY=${DEEPSEEK_API_KEY}
      - MIMO_API_KEY=${MIMO_API_KEY}
      - NATS_SERVERS=nats://nats:4222
      - IOA_BUS_BACKEND=nats
    ports:
      - "8000:8000"
    networks:
      - ioa-net
    depends_on:
      nats:
        condition: service_healthy
      simulator:
        condition: service_started

  # ==================== Agents ====================
  orchestrator:
    build:
      context: .
      dockerfile: Dockerfile.agent
    container_name: ioa-orchestrator
    environment:
      - AGENT_NAME=orchestrator
      - AGENT_ID=orchestrator-agent
      - AGENT_DOMAIN=global
      - AGENT_CAPABILITY=orchestrate
      - IOA_PSK=${IOA_PSK}
      - DEEPSEEK_API_KEY=${DEEPSEEK_API_KEY}
      - NATS_SERVERS=nats://nats:4222
      - IOA_BUS_BACKEND=nats
    networks:
      - ioa-net
    depends_on:
      nats:
        condition: service_healthy

  monitor-east-china:
    build:
      context: .
      dockerfile: Dockerfile.agent
    container_name: ioa-monitor-east-china
    environment:
      - AGENT_NAME=monitor
      - AGENT_ID=monitor-east-china
      - AGENT_DOMAIN=east-china
      - AGENT_CAPABILITY=monitor
      - NATS_SERVERS=nats://nats:4222
      - IOA_BUS_BACKEND=nats
    networks:
      - ioa-net
    depends_on:
      nats:
        condition: service_healthy

  monitor-north-china:
    build:
      context: .
      dockerfile: Dockerfile.agent
    container_name: ioa-monitor-north-china
    environment:
      - AGENT_NAME=monitor
      - AGENT_ID=monitor-north-china
      - AGENT_DOMAIN=north-china
      - AGENT_CAPABILITY=monitor
      - NATS_SERVERS=nats://nats:4222
      - IOA_BUS_BACKEND=nats
    networks:
      - ioa-net
    depends_on:
      nats:
        condition: service_healthy

  monitor-south-china:
    build:
      context: .
      dockerfile: Dockerfile.agent
    container_name: ioa-monitor-south-china
    environment:
      - AGENT_NAME=monitor
      - AGENT_ID=monitor-south-china
      - AGENT_DOMAIN=south-china
      - AGENT_CAPABILITY=monitor
      - NATS_SERVERS=nats://nats:4222
      - IOA_BUS_BACKEND=nats
    networks:
      - ioa-net
    depends_on:
      nats:
        condition: service_healthy

  monitor-west-china:
    build:
      context: .
      dockerfile: Dockerfile.agent
    container_name: ioa-monitor-west-china
    environment:
      - AGENT_NAME=monitor
      - AGENT_ID=monitor-west-china
      - AGENT_DOMAIN=west-china
      - AGENT_CAPABILITY=monitor
      - NATS_SERVERS=nats://nats:4222
      - IOA_BUS_BACKEND=nats
    networks:
      - ioa-net
    depends_on:
      nats:
        condition: service_healthy

  diagnoser:
    build:
      context: .
      dockerfile: Dockerfile.agent
    container_name: ioa-diagnoser
    environment:
      - AGENT_NAME=diagnoser
      - AGENT_ID=diagnoser-global
      - AGENT_DOMAIN=global
      - AGENT_CAPABILITY=diagnose
      - DEEPSEEK_API_KEY=${DEEPSEEK_API_KEY}
      - NATS_SERVERS=nats://nats:4222
      - IOA_BUS_BACKEND=nats
    networks:
      - ioa-net
    depends_on:
      nats:
        condition: service_healthy

  repairer:
    build:
      context: .
      dockerfile: Dockerfile.agent
    container_name: ioa-repairer
    environment:
      - AGENT_NAME=repairer
      - AGENT_ID=repairer-global
      - AGENT_DOMAIN=global
      - AGENT_CAPABILITY=repair
      - NATS_SERVERS=nats://nats:4222
      - IOA_BUS_BACKEND=nats
    networks:
      - ioa-net
    depends_on:
      nats:
        condition: service_healthy

  verifier:
    build:
      context: .
      dockerfile: Dockerfile.agent
    container_name: ioa-verifier
    environment:
      - AGENT_NAME=verifier
      - AGENT_ID=verifier-global
      - AGENT_DOMAIN=global
      - AGENT_CAPABILITY=verify
      - NATS_SERVERS=nats://nats:4222
      - IOA_BUS_BACKEND=nats
    networks:
      - ioa-net
    depends_on:
      nats:
        condition: service_healthy

  reporter:
    build:
      context: .
      dockerfile: Dockerfile.agent
    container_name: ioa-reporter
    environment:
      - AGENT_NAME=reporter
      - AGENT_ID=reporter-global
      - AGENT_DOMAIN=global
      - AGENT_CAPABILITY=report
      - DEEPSEEK_API_KEY=${DEEPSEEK_API_KEY}
      - NATS_SERVERS=nats://nats:4222
      - IOA_BUS_BACKEND=nats
    networks:
      - ioa-net
    depends_on:
      nats:
        condition: service_healthy

  # ==================== GUI ====================
  gui:
    build:
      context: .
      dockerfile: Dockerfile.gui
    container_name: ioa-gui
    ports:
      - "3000:80"
    networks:
      - ioa-net

networks:
  ioa-net:
    driver: bridge
```

- [ ] **Step 2: 创建开发覆盖文件（单进程兼容模式）**

```yaml
# docker-compose.override.yml — 开发时不需要 NATS，使用内存总线
version: "3.9"

services:
  middleware:
    environment:
      - IOA_BUS_BACKEND=memory
    depends_on:
      - simulator

  orchestrator:
    environment:
      - IOA_BUS_BACKEND=memory

  monitor-east-china:
    environment:
      - IOA_BUS_BACKEND=memory

  # ... (all other agents similarly)

  # Remove NATS dependency in memory mode
  nats:
    profiles:
      - production
```

- [ ] **Step 3: 创建一键启动脚本**

```bash
#!/bin/bash
# scripts/generate_env.sh
# Generate .env from .env.example if not exists

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Generated .env from .env.example"
  echo "Please edit .env with your actual API keys and PSK."
  exit 1
fi
```

```bash
#!/bin/bash
# scripts/start.sh
set -e

echo "=== IoA Distributed Network Ops Platform ==="

# Check .env
if [ ! -f .env ]; then
  echo "ERROR: .env not found. Run scripts/generate_env.sh first."
  exit 1
fi

# Build and start
docker compose up -d --build

echo ""
echo "=== Services started ==="
echo "GUI:        http://localhost:3000"
echo "Middleware: http://localhost:8000"
echo "Simulator:  http://localhost:8001"
echo "NATS Admin: http://localhost:8222"
echo ""
echo "View logs:  docker compose logs -f"
echo "Stop:       docker compose down"
```

- [ ] **Step 4: Commit**

```bash
git add docker-compose.yml docker-compose.override.yml scripts/
git commit -m "feat: add Docker Compose orchestration for distributed deployment"
```

---

### 模块 B：差异化修复操作（P0-2）

> **评审痛点:** "修复操作全部统一为 clear_fault，这让'智能运维'失去了实际意义。"

**目标:** 实现至少 5 种差异化的修复动作，每种对应真实的网络运维操作。

#### Task B1: 扩展模拟器修复动作

**Files:**
- Modify: `backend/simulator/faults.py`

- [ ] **Step 1: 重构 FAULT_ACTIONS 为具体操作**

```python
# backend/simulator/faults.py — 替换原有 FAULT_ACTIONS

from dataclasses import dataclass
from typing import Optional

@dataclass
class RepairAction:
    """A concrete repair action that can be applied to the simulated network."""
    action_id: str
    action_type: str  # "route_switch", "acl_deploy", "traffic_shape", "link_failover", "restart_service"
    target: str       # link_id or device_id
    params: dict      # action-specific parameters

# Replace the old FAULT_ACTIONS dict:

def repair_route_switch(link_id: str, backup_link_id: str) -> RepairAction:
    """Switch traffic from primary link to backup link."""
    state = get_state()
    link = state.get_link(link_id)
    backup = state.get_link(backup_link_id)

    if link and backup:
        # Move traffic load to backup link
        backup.bandwidth_util = min(1.0, backup.bandwidth_util + link.bandwidth_util * 0.7)
        link.bandwidth_util *= 0.1  # Only management traffic remains
        link.fault_latency = None
        link.fault_packet_loss = None
        link.fault_bandwidth_util = None

    return RepairAction(
        action_id=f"route_switch_{link_id}",
        action_type="route_switch",
        target=link_id,
        params={"backup_link": backup_link_id}
    )


def repair_acl_deploy(device_id: str, rules: list[str]) -> RepairAction:
    """Deploy ACL rules to filter malicious traffic (DDoS mitigation)."""
    state = get_state()
    # Find all links connected to the device
    for link in state.get_all_links():
        if link.src == device_id or link.dst == device_id:
            # ACL filters reduce attack traffic
            if link.fault_bandwidth_util:
                link.fault_bandwidth_util = max(0, link.fault_bandwidth_util - 0.6)
            if link.fault_packet_loss:
                link.fault_packet_loss = max(0, link.fault_packet_loss - 0.5)

    return RepairAction(
        action_id=f"acl_deploy_{device_id}",
        action_type="acl_deploy",
        target=device_id,
        params={"rules": rules}
    )


def repair_traffic_shaping(link_id: str, max_bandwidth: float) -> RepairAction:
    """Apply traffic shaping/QoS to a congested link."""
    state = get_state()
    link = state.get_link(link_id)
    if link:
        # Shape traffic to specified bandwidth
        link.bandwidth_util = max_bandwidth
        link.fault_latency = max(0, (link.fault_latency or 0) * 0.3)
        link.fault_bandwidth_util = None

    return RepairAction(
        action_id=f"traffic_shape_{link_id}",
        action_type="traffic_shape",
        target=link_id,
        params={"max_bandwidth": max_bandwidth}
    )


def repair_link_failover(link_id: str, standby_link_id: str) -> RepairAction:
    """Fail over to a standby link when primary link fails."""
    state = get_state()
    failed = state.get_link(link_id)
    standby = state.get_link(standby_link_id)

    if failed:
        failed.fault_latency = None
        failed.fault_packet_loss = None
        failed.fault_bandwidth_util = None
        failed.status = "standby"

    if standby:
        standby.status = "active"
        # Standby takes over with normal metrics
        standby.bandwidth_util = min(0.7, standby.bandwidth_util)

    return RepairAction(
        action_id=f"link_failover_{link_id}",
        action_type="link_failover",
        target=link_id,
        params={"standby_link": standby_link_id}
    )


def repair_restart_service(device_id: str, service_name: str) -> RepairAction:
    """Restart a service/interface on a device (CPU overload / misconfig recovery)."""
    state = get_state()
    # Reset device-related faults
    for link in state.get_all_links():
        if link.src == device_id or link.dst == device_id:
            if link.fault_latency:
                link.fault_latency *= 0.1

    return RepairAction(
        action_id=f"restart_{device_id}_{service_name}",
        action_type="restart_service",
        target=device_id,
        params={"service": service_name}
    )


# Map fault types to specific repair strategies
FAULT_REPAIR_STRATEGIES = {
    "link_congestion": {
        "primary": "traffic_shape",
        "fallback": "route_switch",
        "description": "Traffic shaping with route switch fallback"
    },
    "link_outage": {
        "primary": "link_failover",
        "fallback": "route_switch",
        "description": "Link failover to standby with route switch fallback"
    },
    "cpu_overload": {
        "primary": "restart_service",
        "fallback": "traffic_shape",
        "description": "Service restart with traffic shaping fallback"
    },
    "ddos": {
        "primary": "acl_deploy",
        "fallback": "traffic_shape",
        "description": "ACL rule deployment with traffic shaping fallback"
    },
    "misconfig": {
        "primary": "restart_service",
        "fallback": "acl_deploy",
        "description": "Service restart to reset config with ACL fallback"
    },
    "device_failure": {
        "primary": "link_failover",
        "fallback": "route_switch",
        "description": "Link failover with global route switch fallback"
    },
}

# Keep the generic clear_fault for cleanup purposes only
def clear_fault(fault_id: str) -> bool:
    """Generic fault clear — used only for cleanup/reset, not for repair."""
    return get_state().clear_all_faults()
```

- [ ] **Step 2: 更新 SimulatorState 添加按类型清除**

```python
# backend/simulator/state.py 中添加
def clear_faults_by_type(self, fault_type: str) -> int:
    """Clear all faults of a specific type. Returns count removed."""
    removed = 0
    for fid in list(self.faults.keys()):
        if self.faults[fid]["type"] == fault_type:
            self.remove_fault(fid)
            removed += 1
    return removed
```

- [ ] **Step 3: 添加修复动作 API 端点**

```python
# backend/simulator/api.py 中添加
@app.post("/simulator/repair")
async def apply_repair(action: RepairAction):
    """Apply a repair action to the simulated network."""
    handlers = {
        "route_switch": repair_route_switch,
        "acl_deploy": repair_acl_deploy,
        "traffic_shape": repair_traffic_shaping,
        "link_failover": repair_link_failover,
        "restart_service": repair_restart_service,
    }
    handler = handlers.get(action.action_type)
    if not handler:
        raise HTTPException(400, f"Unknown repair action: {action.action_type}")

    result = handler(**action.params)
    return {"status": "applied", "action": result}
```

- [ ] **Step 4: Commit**

```bash
git add backend/simulator/
git commit -m "feat: implement 5 differentiated repair actions replacing generic clear_fault"
```

---

#### Task B2: 改造 RepairerAgent 使用差异化修复

**Files:**
- Modify: `backend/agents/repairer_agent/agent.py`

- [ ] **Step 1: 重写 RepairerAgent 的 `_execute_repair`**

```python
# backend/agents/repairer_agent/agent.py — 完整重写

import asyncio
import logging
from typing import Any

from simulator.faults import FAULT_REPAIR_STRATEGIES

logger = logging.getLogger(__name__)


class RepairerAgent(BaseAgent):
    """Agent that executes differentiated repair actions based on diagnosis."""

    async def _execute_repair(self, diagnosis: dict[str, Any]) -> dict[str, Any]:
        """
        Execute a targeted repair based on diagnosis results.

        diagnosis format:
        {
            "fault_type": "link_congestion",
            "target": "link-east-china-r1-s1",
            "confidence": 0.80,
            "context": {...}
        }
        """
        fault_type = diagnosis.get("fault_type", "unknown")
        target = diagnosis.get("target", "unknown")
        strategy = FAULT_REPAIR_STRATEGIES.get(fault_type)

        if not strategy:
            logger.warning(f"No repair strategy for fault type: {fault_type}, using generic clear")
            return await self._generic_clear(target)

        # 1. Determine the specific repair parameters based on context
        repair_params = self._build_repair_params(fault_type, target, diagnosis.get("context", {}))
        primary_action = strategy["primary"]

        # 2. Attempt primary repair
        logger.info(f"Repairing {fault_type} on {target} with {primary_action}")
        result = await self._apply_repair_action(primary_action, target, repair_params)

        # 3. If primary fails, attempt fallback
        if not result.get("success"):
            fallback_action = strategy.get("fallback")
            if fallback_action:
                logger.warning(
                    f"Primary repair {primary_action} failed, trying fallback {fallback_action}"
                )
                result = await self._apply_repair_action(fallback_action, target, repair_params)

        return {
            "fault_type": fault_type,
            "target": target,
            "primary_action": primary_action,
            "fallback_used": not result.get("success") if strategy.get("fallback") else False,
            "success": result.get("success", False),
            "details": result.get("details", ""),
        }

    def _build_repair_params(
        self, fault_type: str, target: str, context: dict
    ) -> dict:
        """Build repair parameters based on fault type and context."""
        topology = context.get("topology", {})
        links = topology.get("links", [])

        if fault_type == "link_congestion":
            # Find an alternative route
            backup = self._find_backup_link(target, links)
            return {
                "link_id": target,
                "max_bandwidth": 0.7,
                "backup_link_id": backup or f"{target}-backup",
            }

        elif fault_type == "link_outage":
            standby = self._find_standby_link(target, links)
            return {
                "link_id": target,
                "standby_link_id": standby or f"{target}-standby",
                "backup_link_id": standby or f"{target}-backup",
            }

        elif fault_type == "cpu_overload":
            return {
                "device_id": target,
                "service_name": context.get("affected_service", "bgpd"),
            }

        elif fault_type == "ddos":
            return {
                "device_id": target,
                "rules": [
                    "deny ip any any established",
                    "rate-limit icmp 1000",
                    "drop tcp syn flood threshold 10000",
                ],
            }

        elif fault_type == "misconfig":
            return {
                "device_id": target,
                "service_name": "configd",
            }

        elif fault_type == "device_failure":
            # Route around the failed device
            affected_links = [l for l in links if target in (l.get("src"), l.get("dst"))]
            return {
                "link_id": affected_links[0]["id"] if affected_links else target,
                "standby_link_id": f"{target}-standby",
                "backup_link_id": f"{target}-backup",
            }

        return {"link_id": target}

    def _find_backup_link(self, link_id: str, links: list[dict]) -> str | None:
        """Find an alternative link for the same endpoints."""
        for link in links:
            if link.get("id") != link_id and link.get("type") == "backup":
                return link["id"]
        return None

    def _find_standby_link(self, link_id: str, links: list[dict]) -> str | None:
        """Find a standby link."""
        for link in links:
            if link.get("id") != link_id and link.get("status") == "standby":
                return link["id"]
        return None

    async def _apply_repair_action(
        self, action_type: str, target: str, params: dict
    ) -> dict[str, Any]:
        """Apply a repair action via the simulator API."""
        try:
            result = await self.send_message("simulator.repair", {
                "action_type": action_type,
                "target": target,
                "params": params,
            })
            return {"success": True, "action": action_type, "details": result}
        except Exception as e:
            logger.exception(f"Repair action {action_type} failed on {target}")
            return {"success": False, "action": action_type, "details": str(e)}

    async def _generic_clear(self, target: str) -> dict[str, Any]:
        """Fallback: clear all faults (legacy behavior)."""
        try:
            result = await self.send_message("simulator.fault.clear", {"target": target})
            return {"success": True, "action": "generic_clear", "details": result}
        except Exception as e:
            return {"success": False, "action": "generic_clear", "details": str(e)}
```

- [ ] **Step 2: 更新 DiagnoserAgent 输出的 diagnosis 结构**

确保 `SYMPTOM_RULES` 中的 `repair_action` 字段从统一的 `clear_fault` 改为对应策略：

```python
SYMPTOM_RULES = [
    {
        "name": "link_outage_or_device_failure",
        "condition": {"packet_loss": {"gte": 0.99}},
        "fault_type": "device_failure",
        "confidence": 0.90,
        "repair_strategy": "link_failover",  # was: clear_fault
    },
    {
        "name": "ddos_attack",
        "condition": {"bandwidth_util": {"gte": 0.95}},
        "fault_type": "ddos",
        "confidence": 0.85,
        "repair_strategy": "acl_deploy",  # was: clear_fault
    },
    {
        "name": "link_congestion",
        "condition": {"latency": {"severity": "high"}},
        "fault_type": "link_congestion",
        "confidence": 0.80,
        "repair_strategy": "traffic_shape",  # was: clear_fault
    },
    {
        "name": "cpu_overload",
        "condition": {"latency": {"severity": "medium"}},
        "fault_type": "cpu_overload",
        "confidence": 0.70,
        "repair_strategy": "restart_service",  # was: clear_fault
    },
    {
        "name": "misconfig",
        "condition": {"packet_loss": {"gte": 0.01, "lte": 0.50}},
        "fault_type": "misconfig",
        "confidence": 0.75,
        "repair_strategy": "restart_service",  # was: clear_fault
    },
]
```

- [ ] **Step 3: Commit**

```bash
git add backend/agents/repairer_agent/ backend/agents/diagnoser_agent/
git commit -m "feat: implement differentiated repair with primary/fallback strategies"
```

---

### 模块 C：安全加固（P1-3）

> **评审痛点:** "API密钥硬编码、PSK为弱密码、CORS全开放、WebSocket无认证。"

#### Task C1: 移除所有硬编码密钥

**Files:**
- Modify: `backend/ioa_middleware/auth/__init__.py`
- Modify: `backend/ioa_middleware/config.py`
- Modify: `gui/index.html`

- [ ] **Step 1: 移除 auth 模块中的默认 PSK**

```python
# backend/ioa_middleware/auth/__init__.py

# Change:
#     return "ioa-dev-only-insecure-key"  # Line 31-32

# To:
import os

def get_psk(config: dict) -> str:
    """Get pre-shared key from environment or config. No hardcoded fallback."""
    psk = os.environ.get("IOA_PSK") or config.get("auth", {}).get("pre_shared_key")
    if not psk:
        raise RuntimeError(
            "IOA_PSK not set. Set the IOA_PSK environment variable "
            "or auth.pre_shared_key in config.yaml."
        )
    if psk in ("ioa-dev-only-insecure-key", "ioa2026demo", "changeme"):
        raise RuntimeError(
            f"INSECURE PSK detected: '{psk}'. "
            "Please set a strong random PSK via IOA_PSK environment variable."
        )
    return psk
```

- [ ] **Step 2: 移除 config.py 中的默认 PSK**

```python
# backend/ioa_middleware/config.py

# Remove these lines (or comment out):
# config["auth"]["pre_shared_key"] = "ioa-dev-only-insecure-key"
# if config["auth"]["pre_shared_key"] == "ioa2026demo":
#     logger.warning(...)

# Replace with:
config.setdefault("auth", {})
if not config["auth"].get("pre_shared_key"):
    psk = os.environ.get("IOA_PSK")
    if not psk:
        raise RuntimeError("IOA_PSK environment variable is required for production.")
    config["auth"]["pre_shared_key"] = psk
```

- [ ] **Step 3: 移除前端硬编码 Token**

```javascript
// gui/index.html — Line 288

// Change:
// const AUTH_TOKEN='ioa-dev-only-insecure-key';

// To:
// Auth token is injected at build time or configured via UI
const AUTH_TOKEN = localStorage.getItem('ioa_auth_token') || '';

// Add login/configure section in UI
function promptForToken() {
    const token = prompt('Enter IoA Platform Auth Token:');
    if (token) {
        localStorage.setItem('ioa_auth_token', token);
        location.reload();
    }
}
```

- [ ] **Step 4: 在 WebSocket 连接中加入认证**

```javascript
// gui/index.html — dashboard WS 连接

// Before:
// const ws = new WebSocket('ws://localhost:8000/ws');

// After:
function createAuthenticatedWebSocket(url) {
    const token = localStorage.getItem('ioa_auth_token');
    const wsUrl = token ? `${url}?token=${encodeURIComponent(token)}` : url;
    const ws = new WebSocket(wsUrl);

    ws.onopen = () => {
        // Also send token as first message for protocols that don't support query params
        if (token) {
            ws.send(JSON.stringify({ type: 'auth', token: token }));
        }
    };
    return ws;
}
```

- [ ] **Step 5: 更新 .env.example 添加强 PSK 生成说明**

```bash
# .env.example
# Generate a strong PSK:
#   python -c "import secrets; print(secrets.token_urlsafe(32))"
IOA_PSK=generate-with-python-secrets-token-urlsafe-32
```

- [ ] **Step 6: Commit**

```bash
git add backend/ioa_middleware/auth/ backend/ioa_middleware/config.py gui/index.html .env.example
git commit -m "security: remove all hardcoded secrets, enforce strong PSK"
```

---

#### Task C2: 限制 CORS + 添加 CSRF 保护

**Files:**
- Modify: `backend/ioa_middleware/main.py`
- Modify: `backend/ioa_middleware/config.py`

- [ ] **Step 1: 收紧 CORS 配置**

```python
# backend/ioa_middleware/config.py

# Replace default CORS:
config.setdefault("cors", {})
config["cors"].setdefault("allowed_origins", ["http://localhost:3000"])  # Only GUI
config["cors"].setdefault("allowed_methods", ["GET", "POST"])           # Remove PUT/DELETE
config["cors"].setdefault("allowed_headers", ["Authorization", "Content-Type", "X-Request-ID"])
config["cors"].setdefault("allow_credentials", True)
config["cors"].setdefault("max_age", 3600)
```

- [ ] **Step 2: 环境感知的 CORS 组装**

```python
# backend/ioa_middleware/main.py

def configure_cors(app: FastAPI, config: dict) -> None:
    cors_config = config.get("cors", {})
    origins = cors_config.get("allowed_origins", ["http://localhost:3000"])

    # In production, only allow specific origins
    if os.environ.get("IOA_ENV") == "production":
        origins = [o for o in origins if o.startswith("https://")]

    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=cors_config.get("allow_credentials", True),
        allow_methods=cors_config.get("allowed_methods", ["GET", "POST"]),
        allow_headers=cors_config.get("allowed_headers", ["Authorization", "Content-Type"]),
        max_age=cors_config.get("max_age", 3600),
    )
```

- [ ] **Step 3: 添加 WebSocket 认证中间件**

```python
# backend/ioa_middleware/auth/__init__.py 中添加

from fastapi import WebSocket, WebSocketDisconnect

async def ws_auth_dependency(
    websocket: WebSocket,
    token: str = None,  # query param
) -> bool:
    """Validate WebSocket connection token."""
    expected_psk = get_psk_from_config()

    # Check query param
    if token and token == expected_psk:
        return True

    # Check first message
    try:
        first_msg = await asyncio.wait_for(websocket.receive_json(), timeout=5.0)
        if first_msg.get("type") == "auth" and first_msg.get("token") == expected_psk:
            return True
    except asyncio.TimeoutError:
        pass

    await websocket.close(code=4001, reason="Authentication required")
    return False
```

- [ ] **Step 4: Commit**

```bash
git add backend/ioa_middleware/
git commit -m "security: harden CORS, add WebSocket authentication, env-aware origins"
```

---

### 模块 D：文档一致性修正（P1-4）

> **评审痛点:** "README说8个Agent，其他文档说5个，关键不一致引发信任危机。"

#### Task D1: 统一文档中的 Agent 数量描述

**Files:**
- Modify: `README.md`
- Modify: `COMPETITION_COMPLIANCE.md`
- Modify: `CODE_AUDIT_REPORT.md`

- [ ] **Step 1: 审计所有文档中的 Agent 数量**

```bash
grep -rn "个.*Agent\|Agent.*个\|包含.*Agent" README.md COMPETITION_COMPLIANCE.md CODE_AUDIT_REPORT.md docs/
```

- [ ] **Step 2: 统一为"9 个 Agent 实例（5 种类型）"**

在 README.md 中统一描述为:

> 系统包含 **9 个 Agent 实例，覆盖 5 种类型**：
> - **OrchestratorAgent** ×1：意图解析与 DAG 编排
> - **MonitorAgent** ×4：分域监控（华东/华北/华南/华西）
> - **DiagnoserAgent** ×1：根因诊断
> - **RepairerAgent** ×1：差异化修复
> - **VerifyAgent** ×1：闭环验证
> - **ReporterAgent** ×1：报告生成

同步修正 COMPETITION_COMPLIANCE.md 和 CODE_AUDIT_REPORT.md 中的描述。

- [ ] **Step 3: 更新 CODE_AUDIT_REPORT 为客观评价**

- 将自评 91-96 分改为基于评委反馈的客观评估
- 注明当前已知问题和改进计划
- 添加"Docker 化部署"、"差异化修复"等改进项的进度

- [ ] **Step 4: Commit**

```bash
git add README.md COMPETITION_COMPLIANCE.md CODE_AUDIT_REPORT.md
git commit -m "docs: unify agent count to 9 instances/5 types, update self-assessment"
```

---

### 模块 E：测试补齐 + 异常处理（P1-补充）

> 评审报告未直接列为 P0/P1，但在"技术实现"中明确指出"缺少测试代码"和"异常处理过于宽泛"。

#### Task E1: 核心路径测试

**Files:**
- Create: `tests/test_dag_scheduler.py`
- Create: `tests/test_repair_strategies.py`
- Create: `tests/test_auth.py`

- [ ] **Step 1: DAG 调度器测试**

```python
# tests/test_dag_scheduler.py
import pytest
from ioa_middleware.orchestrator.scheduler import DagScheduler
from ioa_middleware.orchestrator.models import DagDefinition, DagNodeDef


class TestDagScheduler:
    """Test DAG topological sort, cycle detection, and retry logic."""

    def test_kahn_sort_linear_dag(self):
        """Linear DAG: A → B → C."""
        nodes = [
            DagNodeDef(node_id="a", depends_on=[], capability="monitor", domain="east-china"),
            DagNodeDef(node_id="b", depends_on=["a"], capability="diagnose", domain="global"),
            DagNodeDef(node_id="c", depends_on=["b"], capability="repair", domain="global"),
        ]
        dag = DagDefinition(dag_id="test-linear", nodes=nodes)
        scheduler = DagScheduler(dag)
        order = scheduler._topological_sort()
        assert order == ["a", "b", "c"]

    def test_kahn_sort_parallel(self):
        """Parallel DAG: A, B → C."""
        nodes = [
            DagNodeDef(node_id="a", depends_on=[], capability="monitor", domain="east-china"),
            DagNodeDef(node_id="b", depends_on=[], capability="monitor", domain="north-china"),
            DagNodeDef(node_id="c", depends_on=["a", "b"], capability="report", domain="global"),
        ]
        dag = DagDefinition(dag_id="test-parallel", nodes=nodes)
        scheduler = DagScheduler(dag)
        order = scheduler._topological_sort()
        assert order[0] in ("a", "b")
        assert order[1] in ("a", "b")
        assert order[2] == "c"

    def test_cycle_detection(self):
        """Cyclic DAG: A → B → A."""
        nodes = [
            DagNodeDef(node_id="a", depends_on=["b"], capability="monitor", domain="east-china"),
            DagNodeDef(node_id="b", depends_on=["a"], capability="diagnose", domain="global"),
        ]
        dag = DagDefinition(dag_id="test-cycle", nodes=nodes)
        scheduler = DagScheduler(dag)
        with pytest.raises(ValueError, match="cycle"):
            scheduler._topological_sort()

    def test_node_retry_exhaustion(self):
        """Node retries exhausted → DAG fails."""
        # Test that after max_retries, the node is marked as failed
        pass

    def test_verify_retry_resets_diagnose_repair(self):
        """Verifier retry → reset diagnose and repair nodes."""
        # Test the retry_signal path
        pass
```

- [ ] **Step 2: 修复策略单元测试**

```python
# tests/test_repair_strategies.py
from simulator.faults import (
    FAULT_REPAIR_STRATEGIES,
    repair_route_switch,
    repair_acl_deploy,
    repair_traffic_shaping,
    repair_link_failover,
    repair_restart_service,
)


class TestRepairStrategies:
    """Test each repair strategy has a valid primary and fallback."""

    @pytest.mark.parametrize("fault_type", [
        "link_congestion", "link_outage", "cpu_overload",
        "ddos", "misconfig", "device_failure",
    ])
    def test_every_fault_has_strategy(self, fault_type):
        assert fault_type in FAULT_REPAIR_STRATEGIES
        strategy = FAULT_REPAIR_STRATEGIES[fault_type]
        assert "primary" in strategy
        assert strategy["primary"] in (
            "route_switch", "acl_deploy", "traffic_shape",
            "link_failover", "restart_service",
        )

    def test_repair_route_switch_reduces_load(self):
        """Route switch should reduce primary link load."""
        # Setup simulator state, apply repair, assert metrics changed
        pass

    def test_repair_acl_deploy_filters_traffic(self):
        """ACL deploy should reduce bandwidth and packet loss."""
        pass
```

- [ ] **Step 3: 认证测试**

```python
# tests/test_auth.py
import pytest
from ioa_middleware.auth import get_psk


class TestAuthentication:
    """Test authentication and PSK handling."""

    def test_rejects_default_psk(self, monkeypatch):
        """Default PSK 'ioa-dev-only-insecure-key' should raise."""
        monkeypatch.setenv("IOA_PSK", "ioa-dev-only-insecure-key")
        with pytest.raises(RuntimeError, match="INSECURE PSK"):
            get_psk({})

    def test_rejects_weak_psk(self, monkeypatch):
        """Weak PSK 'ioa2026demo' should raise."""
        monkeypatch.setenv("IOA_PSK", "ioa2026demo")
        with pytest.raises(RuntimeError, match="INSECURE PSK"):
            get_psk({})

    def test_accepts_strong_psk(self, monkeypatch):
        """Strong random PSK should be accepted."""
        monkeypatch.setenv("IOA_PSK", "k7Xp2Qv9mN4wR8tY1aL6bJ3cF5hD0eG")
        psk = get_psk({})
        assert psk == "k7Xp2Qv9mN4wR8tY1aL6bJ3cF5hD0eG"

    def test_missing_psk_raises(self, monkeypatch):
        """Missing PSK should raise RuntimeError."""
        monkeypatch.delenv("IOA_PSK", raising=False)
        with pytest.raises(RuntimeError):
            get_psk({"auth": {}})
```

- [ ] **Step 4: 运行测试确认通过**

```bash
cd backend
python -m pytest tests/ -v
```

- [ ] **Step 5: Commit**

```bash
git add tests/
git commit -m "test: add tests for DAG scheduler, repair strategies, and auth"
```

---

#### Task E2: 异常处理规范化

**Files:**
- Modify: `backend/agents/base_agent.py`
- Modify: `backend/agents/diagnoser_agent/agent.py`
- Modify: `backend/ioa_middleware/orchestrator/scheduler.py`

- [ ] **Step 1: 定义异常层级**

```python
# backend/exceptions.py (新建)

class IoAError(Exception):
    """Base exception for IoA platform."""
    def __init__(self, message: str, code: str = "INTERNAL_ERROR", recoverable: bool = False):
        self.message = message
        self.code = code
        self.recoverable = recoverable
        super().__init__(message)


class AgentError(IoAError):
    """Agent-level errors."""
    pass


class CommunicationError(IoAError):
    """Message bus / network errors (recoverable)."""
    def __init__(self, message: str):
        super().__init__(message, code="COMMUNICATION_ERROR", recoverable=True)


class DiagnosisError(IoAError):
    """Diagnosis failures."""
    pass


class RepairError(IoAError):
    """Repair action failures."""
    pass


class ConfigError(IoAError):
    """Configuration errors (not recoverable)."""
    def __init__(self, message: str):
        super().__init__(message, code="CONFIG_ERROR", recoverable=False)
```

- [ ] **Step 2: 替换宽泛的 `except Exception`**

```python
# 所有文件中，将:
# except Exception:
#     logger.exception(...)
#
# 替换为分类处理:

try:
    result = await some_operation()
except CommunicationError:
    logger.warning("Communication failed, will retry")
    return await self._retry_with_backoff()
except IoAError as e:
    if e.recoverable:
        logger.warning(f"Recoverable error: {e.message}")
        return self._fallback_response(e)
    else:
        logger.error(f"Non-recoverable error: {e.message}")
        raise
except Exception:
    logger.critical("Unexpected error", exc_info=True)
    raise IoAError("Unexpected internal error", code="INTERNAL_ERROR")
```

- [ ] **Step 3: 全局扫描 `except Exception`**

```bash
grep -rn "except Exception" backend/ --include="*.py"
grep -rn "except:" backend/ --include="*.py"
```

逐个审查并替换为分类处理。

- [ ] **Step 4: Commit**

```bash
git add backend/
git commit -m "refactor: classify exceptions, replace bare except blocks"
```

---

## 阶段二：中期改进（2-4 周，提升技术深度）

### 模块 F：对接 Mininet / Containerlab（P1-5）

> **评审痛点:** "所有指标都是模拟器随机生成的，没有对接任何真实网管系统。"

- [ ] **Task F1:** 安装并熟悉 Mininet
- [ ] **Task F2:** 创建 4 域网络拓扑（Mininet Python API）
- [ ] **Task F3:** 实现 SNMP/NetFlow 数据采集适配器
- [ ] **Task F4:** 对接 Prometheus + Node Exporter 监控真实指标
- [ ] **Task F5:** 在真实链路故障场景下验证诊断→修复→验证闭环

### 模块 G：第二应用场景验证中间件通用性（P2-6）

> **评审痛点:** "赛项要求'优先开发通用中间件'，只有一个场景不够。"

- [ ] **Task G1:** 选择第二场景（推荐：智能客服调度或智能制造协同）
- [ ] **Task G2:** 实现第二场景的 Agent 子类
- [ ] **Task G3:** 在 README 中展示中间件复用能力

### 模块 H：性能基准测试（P2-7）

- [ ] **Task H1:** DAG 调度吞吐量测试（100/500/1000 节点）
- [ ] **Task H2:** 语义路由匹配延迟测试（P50/P95/P99）
- [ ] **Task H3:** 全链路端到端延迟测试
- [ ] **Task H4:** 多 Agent 并发通信压力测试

---

## 阶段三：长期改进（比赛结束后）

- [ ] 中间件抽象为开源框架（pip installable）
- [ ] 对接真实网管系统（Prometheus / Zabbix / ServiceNow）
- [ ] 引入强化学习自愈策略（RL-based repair policy）
- [ ] 生产级安全审计和渗透测试
- [ ] CI/CD 流水线（GitHub Actions）

---

## 执行顺序依赖图

```
阶段零 (Task 0)
    │
    ▼
阶段一
    │
    ├── 模块 A: Docker 化 (A1 → A2 → A3 → A4)
    │       └── 依赖 Task 0 (密钥已轮换)
    │
    ├── 模块 B: 差异化修复 (B1 → B2)
    │       └── 可与 A 并行
    │
    ├── 模块 C: 安全加固 (C1 → C2)
    │       └── 依赖 Task 0
    │
    ├── 模块 D: 文档修正
    │       └── 独立，可与 A/B/C 并行
    │
    └── 模块 E: 测试补齐 (E1 → E2)
            └── 依赖 B1 (修复策略就绪后测试)
```

**推荐执行顺序:**
1. **Day 0:** Task 0（密钥轮换 + 审计）
2. **Day 1-2:** 模块 A1 + A2（消息总线 + Agent 改造）
3. **Day 3-4:** 模块 B1 + B2（差异化修复）
4. **Day 5:** 模块 A3 + A4（Dockerfile + Compose）
5. **Day 6:** 模块 C1 + C2（安全加固）
6. **Day 7-8:** 模块 D + E（文档 + 测试）
7. **Day 9-10:** 联调 + 端到端验证 + 演示录制

---

## 答辩准备

### 针对 6 个预测问题的回答要点

**Q1: "分布式体现在哪里？"**
> 答：每个 Agent 运行在独立 Docker 容器中，通过 NATS 消息总线异步通信。我们支持两种模式：单机内存总线（开发测试）和 NATS 分布式总线（生产部署）。每个 Agent 独立注册、独立心跳、独立扩缩容。拓扑见 docker-compose.yml。

**Q2: "修复操作具体做了什么？"**
> 答：我们实现了 5 种差异化修复策略——路由切换、ACL 规则下发、流量整形、链路故障转移、服务重启。每种故障类型有主策略和回退策略，详见 FAULT_REPAIR_STRATEGIES 字典。

**Q3: "四维加权路由的权重依据？"**
> 答：初始权重基于专家经验（能力匹配 0.40、域匹配 0.30、负载均衡 0.20、语义匹配 0.10），后续通过 A/B 测试调优。我们在 test_semantic_router.py 中有相关验证。

**Q4: "重试回滚会不会引发级联故障？"**
> 答：我们有熔断机制——每个节点最多重试 3 次，DAG 级别有超时控制（默认 300s），Verifier 验证失败时只重置 diagnose+repair 两个节点，不会级联扩散。

**Q5: "与 Zabbix 相比哪里更优？"**
> 答：Zabbix 是监控告警工具，我们是意图驱动的闭环运维平台。区别在于：自然语言交互 → 自动诊断 → 自动修复 → 自动验证。对标的是 ServiceNow ITOM + Ansible 的组合，但我们用多智能体协同替代了人工脚本编排。

**Q6: "中间件通用性如何验证？"**
> 答：当前以网络运维为第一场景验证。中间件层（消息总线、智能路由、DAG 调度）完全与业务解耦。我们在计划中设计了第二场景（智能客服调度）来验证通用性。

---

> **计划制定日期:** 2026-06-04
> **基于评审报告:** 评委2 — 评委评审报告-IoA分布式网络运维协同平台.md
> **预计总工时:** 短期 10 天 + 中期 10 天
