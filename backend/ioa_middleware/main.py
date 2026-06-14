"""IoA 中间件 — FastAPI 组装入口

组装注册中心、消息路由、任务调度器路由，
提供 WebSocket 端点，管理应用生命周期。
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ioa_middleware.config import get_config
from ioa_middleware.bus import create_bus
from ioa_middleware.db import init_db, close_db
from ioa_middleware.registry.api import router as registry_router
from ioa_middleware.registry.health import health_check_loop

from ioa_middleware.router.api import router as message_router
from ioa_middleware.auth import TokenAuthMiddleware
from ioa_middleware.orchestrator.api import router as dag_router
from ioa_middleware.orchestrator.scheduler import init_scheduler, get_scheduler
from ioa_middleware.a2a_server import router as a2a_router, init_a2a_router
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from async_utils import safe_task
from exceptions import SchedulerNotInitializedError

logger = logging.getLogger("main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理。"""
    # ── 启动 ──
    config = get_config()

    # 检查 DeepSeek API Key 是否有效（非占位值）
    _api_key = os.environ.get("DEEPSEEK_API_KEY", "") or config.get("llm", {}).get("api_key", "")
    if not _api_key or _api_key == "sk-your-key-here":
        logger.warning(
            "⚠️  DEEPSEEK_API_KEY 未配置或仍为占位值！"
            " NL 自然语言指令将降级为关键词匹配，DAG 全流程诊断/修复不可用。"
            " 请在 .env 中填入真实的 DeepSeek API Key。"
        )

    # Create message bus
    bus = create_bus(config)
    await bus.connect()
    app.state.bus = bus

    await init_db(config["database"]["path"])

    # 启动后台任务
    safe_task(health_check_loop(), name="health_check")

    # Phase 3: 启动 DAG 调度器
    init_scheduler(bus, config)
    scheduler = get_scheduler()
    safe_task(scheduler.start(), name="dag_scheduler")
    logger.info("DAG scheduler started")

    # Phase 4: 启动所有 Agent
    from agents import create_all_agents
    agents = create_all_agents(bus, config)

    # 桥接：将总线消息路由到注册中心
    from ioa_middleware.registry import store as registry_store
    from ioa_middleware.registry.models import CapabilityProfile
    import json

    async def on_register(_topic: str, msg: dict) -> None:
        """处理 Agent 注册消息并写入 DB。"""
        try:
            profile = CapabilityProfile(
                agent_id=msg["agent_id"],
                domain=msg.get("domain", "global"),
                capabilities=[msg.get("capability", "")],
                status="active",
            )
            await registry_store.register_agent(profile)
        except KeyError as exc:
            logger.warning("Malformed register message, missing field: %s", exc)
        except Exception:
            logger.exception("Unexpected error in on_register for %s", msg.get("agent_id"))

    async def on_heartbeat(_topic: str, msg: dict) -> None:
        """处理 Agent 心跳消息。"""
        try:
            await registry_store.heartbeat(msg["agent_id"])
        except KeyError as exc:
            logger.warning("Malformed heartbeat, missing field: %s", exc)
        except Exception:
            logger.exception("Unexpected error in on_heartbeat")

    await bus.subscribe("registry.register", on_register)
    await bus.subscribe("registry.heartbeat", on_heartbeat)

    for a in agents:
        safe_task(a.start(), name=f"agent:{a.agent_id}")
    # 等待所有 Agent 注册完成（最多等 10s，每 200ms 检查一次）
    from ioa_middleware.registry import store as registry_store
    for _ in range(50):
        registered = await registry_store.list_agents(status="active")
        if len(registered) >= len(agents):
            logger.info("All %d agents registered", len(registered))
            break
        await asyncio.sleep(0.2)
    else:
        logger.warning("Not all agents registered within timeout: %d/%d",
                       len(await registry_store.list_agents(status="active")), len(agents))

    logger.info("IoA Middleware started on port %d", config["middleware"]["port"])
    yield

    # ── 关闭 ──
    # Stop agents
    for a in agents:
        try:
            await a.stop()
        except (ConnectionError, OSError) as exc:
            logger.warning("Error stopping agent %s: %s", a.agent_id, exc)
        except Exception:
            logger.exception("Unexpected error stopping agent %s", a.agent_id)
    await bus.close()
    try:
        scheduler = get_scheduler()
        await scheduler.stop()
        logger.info("Scheduler stopped")
    except (SchedulerNotInitializedError, RuntimeError):
        pass  # Scheduler was never started
    except Exception as e:
        logger.error("Failed to stop scheduler: %s", e)
    await close_db()
    logger.info("IoA Middleware Stopped")


def create_app() -> FastAPI:
    """创建并配置 FastAPI 应用。"""
    config = get_config()

    app = FastAPI(
        title="IoA Middleware API",
        description="""
## IoA 分布式网络运维协同平台

基于智能体互联网（Internet of Agents）架构的跨域分布式任务协同调度系统。

### 核心功能

- **Agent 注册中心** (`/registry`): Agent 注册、心跳、能力查询
- **消息路由总线** (`/messages`): IoAP 消息发送、接收、WebSocket 推送
- **DAG 调度引擎** (`/dag`): DAG 任务提交、状态查询、取消

### 认证方式

所有写操作需要 Bearer Token 认证：
```
Authorization: Bearer <pre_shared_key>
```

开放端点（无需认证）：
- `/docs` - Swagger UI
- `/openapi.json` - OpenAPI Schema
- `/health` - 健康检查
- `/gui` - Web 控制台
- `/registry/agents` - Agent 列表（只读）
- `/registry/query` - 能力查询（只读）

### 快速开始

1. 启动服务: `python run.py`
2. 访问 GUI: http://127.0.0.1:8000/gui
3. 查看 API 文档: http://127.0.0.1:8000/docs

### 相关文档

- [Agent 能力自描述规范](/docs/AGENT_CAPABILITIES.md)
- [IoAP 协议规范](/docs/IOAP_PROTOCOL.md)
        """,
        version="1.0.0",
        contact={
            "name": "IoA Platform Team",
            "url": "http://localhost:8000",
        },
        license_info={
            "name": "MIT License",
            "url": "https://opensource.org/licenses/MIT",
        },
        openapi_tags=[
            {
                "name": "Registry",
                "description": "Agent 注册中心 - Agent 注册、心跳、能力查询",
            },
            {
                "name": "Messages",
                "description": "消息路由总线 - IoAP 消息发送、接收、WebSocket 推送",
            },
            {
                "name": "DAG",
                "description": "DAG 调度引擎 - DAG 任务提交、状态查询、取消",
            },
            {
                "name": "A2A",
                "description": "A2A 协议 - Agent-to-Agent 标准协议，支持 Agent Card 发现和任务管理",
            },
            {
                "name": "Simulator",
                "description": "网络模拟器 - 指标查询、故障注入、拓扑数据",
            },
        ],
        lifespan=lifespan,
    )

    # CORS 配置 - 环境感知
    cors_config = config.get("cors", {})
    origins = cors_config.get("allowed_origins", ["http://localhost:3000"])

    # 生产环境只允许 HTTPS 来源
    if os.environ.get("IOA_ENV") == "production":
        origins = [o for o in origins if o.startswith("https://")]

    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_methods=cors_config.get("allowed_methods", ["GET", "POST"]),
        allow_headers=cors_config.get("allowed_headers", ["Authorization", "Content-Type", "X-Request-ID"]),
        allow_credentials=True,
        max_age=3600,
    )

    # 注册认证中间件（在 CORS 之后）
    app.add_middleware(TokenAuthMiddleware)

    # 注册路由
    app.include_router(registry_router, prefix="/registry", tags=["Registry"])

    # Phase 2: 注册消息路由（含 WebSocket /messages/ws）
    app.include_router(message_router, prefix="/messages", tags=["Messages"])

    # Phase 3: 注册调度器路由
    app.include_router(dag_router, prefix="/dag", tags=["DAG"])

    # Phase 5: 注册 A2A 路由
    # A2A 路由需要桥接到 IoAP 消息系统
    async def ioap_send_func(msg: dict) -> dict:
        """IoAP 消息发送函数（A2A 桥接 — 直接通过 MessageBus，无 HTTP 环回）"""
        to_agent = msg.get("to_agent", "")
        if bus is not None and to_agent:
            await bus.publish(f"agent.{to_agent}", msg)
            return {"status": "ok", "routed": True}
        # 降级：直接走 /messages 持久化 + 分发
        import httpx
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"http://127.0.0.1:{port}/messages",
                json=msg,
                headers={"Authorization": f"Bearer {psk}"},
            )
            resp.raise_for_status()
            return resp.json()

    init_a2a_router(ioap_send_func)
    app.include_router(a2a_router, tags=["A2A"])

    # MCP Server (SSE transport) — enables McpToolClient streamable HTTP connection
    from ioa_middleware.mcp_server import create_mcp_sse_app
    _sim_url = config.get("simulator_url", "http://127.0.0.1:8001") if config else "http://127.0.0.1:8001"
    mcp_app = create_mcp_sse_app(_sim_url)
    app.mount("/mcp", mcp_app)
    logger.info("MCP server mounted at /mcp (SSE transport)")

    # Health 端点
    @app.get("/health", tags=["Health"])
    async def health():
        """健康检查端点"""
        from ioa_middleware.registry import store
        try:
            agents = await store.list_agents(status="active")
            auth_enabled = os.environ.get("IOA_AUTH_ENABLED", "true").lower() != "false"
            deepseek_key = config.get("llm", {}).get("deepseek", {}).get("api_key", "")
            return {
                "status": "ok",
                "agents_count": len(agents),
                "auth_enabled": auth_enabled,
                "llm_available": bool(deepseek_key),
                "timestamp": int(__import__('time').time() * 1000),
            }
        except Exception as e:
            return {"status": "error", "detail": str(e)}

    # GUI 仪表盘
    _GUI_DIR = Path(__file__).resolve().parent.parent.parent / "gui"

    @app.get("/")
    async def root():
        return RedirectResponse(url="/gui")

    @app.get("/gui")
    async def gui():
        html = (_GUI_DIR / "index.html").read_text(encoding="utf-8")
        return HTMLResponse(html)

    # 静态文件（CSS、JS）— 挂载到 /gui/static 避免与 /gui 路由冲突
    app.mount("/gui/static", StaticFiles(directory=str(_GUI_DIR)), name="gui_static")

    # WebSocket 仪表盘实时推送
    from fastapi import WebSocket, WebSocketDisconnect
    import asyncio as _asyncio

    _dash_clients: list[WebSocket] = []

    async def broadcast_dashboard(event: dict):
        """向所有仪表盘 WebSocket 客户端广播事件"""
        dead = []
        for ws in _dash_clients:
            try:
                await ws.send_json(event)
            except (WebSocketDisconnect, RuntimeError):
                dead.append(ws)
        for ws in dead:
            try:
                _dash_clients.remove(ws)
            except ValueError:
                pass

    # 暴露给其他模块使用
    app.state.broadcast_dashboard = broadcast_dashboard

    @app.websocket("/ws/dashboard")
    async def ws_dashboard(ws: WebSocket):
        # Token 校验（与 auth 中间件保持一致）
        auth_enabled = os.environ.get("IOA_AUTH_ENABLED", "false").lower() == "true"
        psk = os.environ.get("IOA_PSK", "")
        if auth_enabled:
            token = ws.query_params.get("token", "")
            if not token or token != psk:
                await ws.close(code=4001, reason="Unauthorized")
                return
        await ws.accept()
        _dash_clients.append(ws)
        try:
            while True:
                # 每 2 秒发送心跳
                await _asyncio.sleep(2.0)
                try:
                    await ws.send_json({
                        "type": "dashboard_ping",
                        "ts_ms": int(__import__('time').time() * 1000),
                    })
                except (WebSocketDisconnect, RuntimeError):
                    break
        except WebSocketDisconnect:
            pass
        finally:
            if ws in _dash_clients:
                _dash_clients.remove(ws)

    return app


app = create_app()
