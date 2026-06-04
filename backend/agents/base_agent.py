"""Agent 抽象基类

所有 Agent 继承此类，复用：
- 注册/心跳/消息收发
- WebSocket 消息接收（连接消息总线）
- ToolClient 工具调用接口
"""

from abc import ABC, abstractmethod
import asyncio
import json
import httpx
import uuid
import time
import logging
from typing import Optional

logger = logging.getLogger("agent")


class BaseAgent(ABC):
    """Agent 基类 — 注册、心跳、消息收发、工具调用。"""

    def __init__(
        self,
        agent_id: str,
        domain: str,
        capabilities: list[str],
        registry_url: str = "http://localhost:8000/registry",
        router_url: str = "http://localhost:8000/messages",
        tool_client=None,
        config: dict | None = None,
        description: str = "",
        supported_tasks: list[str] | None = None,
    ):
        self.agent_id = agent_id
        self.domain = domain
        self.capabilities = capabilities
        self.description = description
        self.supported_tasks = supported_tasks or []
        self.registry_url = registry_url
        self.router_url = router_url
        self.tool_client = tool_client          # ToolClient 实例（Phase 3 注入）
        self.message_queue: asyncio.Queue = asyncio.Queue()
        self._http = httpx.AsyncClient(timeout=30.0)
        self._running = False

        # 从配置加载认证令牌
        self._auth_token = self._load_auth_token(config)

    def _load_auth_token(self, config: dict | None = None) -> str:
        """从配置加载认证令牌。"""
        if config:
            return config.get("auth", {}).get("pre_shared_key", "")

        try:
            from ioa_middleware.config import get_config
            cfg = get_config()
            return cfg.get("auth", {}).get("pre_shared_key", "")
        except Exception:
            logger.warning("Failed to load auth token from config")
            return ""

    # ── 注册/心跳 ──────────────────────────────────────

    async def register(self) -> None:
        """向注册中心注册，携带能力描述 metadata。"""
        profile = {
            "agent_id": self.agent_id,
            "domain": self.domain,
            "capabilities": self.capabilities,
            "protocols": ["ioap-v1", "mcp-v2024", "a2a-v1"],
            "status": "active",
            "endpoint": f"agent://{self.agent_id}",
            "metadata": {
                "description": self.description,
                "supported_tasks": self.supported_tasks,
            },
        }
        r = await self._http.post(
            f"{self.registry_url}/register",
            json=profile,
            headers={"Authorization": f"Bearer {self._auth_token}"},
        )
        r.raise_for_status()
        logger.info("[%s] Registered (domain=%s, cap=%s, desc=%s)",
                    self.agent_id, self.domain, self.capabilities, self.description[:40] if self.description else "none")

    async def heartbeat_loop(self) -> None:
        """每 10s 发送心跳。"""
        while self._running:
            try:
                await self._http.post(
                    f"{self.registry_url}/heartbeat",
                    json={"agent_id": self.agent_id},
                    headers={"Authorization": f"Bearer {self._auth_token}"},
                )
            except Exception as e:
                logger.warning("[%s] Heartbeat failed: %s", self.agent_id, e)
            await asyncio.sleep(10)

    # ── 消息收发 ──────────────────────────────────────

    async def send_message(
        self,
        intent: dict,
        payload: dict,
        correlation_id: str,
        to_agent: Optional[str] = None,
    ) -> str:
        """构造 IoAP 消息并发送到路由总线，返回 msg_id。"""
        msg = {
            "msg_id": str(uuid.uuid4()),
            "from_agent": self.agent_id,
            "to_agent": to_agent,
            "intent": intent,
            "payload": payload,
            "correlation_id": correlation_id,
            "ts_ms": int(time.time() * 1000),
        }
        r = await self._http.post(
            f"{self.router_url}",
            json=msg,
            headers={"Authorization": f"Bearer {self._auth_token}"},
        )
        r.raise_for_status()
        return msg["msg_id"]

    async def send_result(
        self, correlation_id: str, dag_id: str, node_id: str, result: dict
    ) -> str:
        """发送 DAG 节点执行结果（路由到 orchestrator）。"""
        return await self.send_message(
            intent={"type": "result", "description": f"Node {node_id} completed", "priority": "normal"},
            payload={"dag_id": dag_id, "node_id": node_id, "result": result},
            correlation_id=correlation_id,
            to_agent="orchestrator",
        )

    async def _ws_listen(self) -> None:
        """WebSocket 监听：连接消息总线，接收发给自己的消息。"""
        import websockets
        ws_url = (self.router_url.replace("http://", "ws://").replace("https://", "wss://")
                  + f"/ws?agent_id={self.agent_id}&token={self._auth_token}")
        while self._running:
            try:
                async with websockets.connect(ws_url) as ws:
                    while self._running:
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
                            msg = json.loads(raw)
                            # 只处理发给自己的 task 消息
                            msg_type = msg.get("type", "")
                            if msg_type == "ping":
                                await ws.send(json.dumps({"type": "pong"}))
                            elif msg.get("to_agent") == self.agent_id or msg.get("to_agent") is None:
                                await self.message_queue.put(msg)
                        except asyncio.TimeoutError:
                            continue
            except Exception:
                pass
            await asyncio.sleep(3)

    async def listen_loop(self) -> None:
        """从消息队列取消息并处理。"""
        while self._running:
            try:
                msg = await asyncio.wait_for(self.message_queue.get(), timeout=1.0)
                await self.handle_message(msg)
            except asyncio.TimeoutError:
                continue

    @abstractmethod
    async def handle_message(self, msg: dict) -> None:
        """处理收到的消息 — 子类必须实现。"""
        ...

    # ── 生命周期 ──────────────────────────────────────

    async def start(self) -> None:
        """启动 Agent：注册 + 心跳 + WebSocket + 消息监听。"""
        self._running = True
        await self.register()
        asyncio.create_task(self.heartbeat_loop())
        asyncio.create_task(self._ws_listen())
        asyncio.create_task(self.listen_loop())
        logger.info("[%s] Started (capabilities=%s)", self.agent_id, self.capabilities)

    async def stop(self) -> None:
        """停止 Agent：注销 + 关闭 HTTP。"""
        self._running = False
        try:
            await self._http.post(
                f"{self.registry_url}/deregister",
                json={"agent_id": self.agent_id},
                headers={"Authorization": f"Bearer {self._auth_token}"},
            )
        except Exception:
            pass
        await self._http.aclose()
        logger.info("[%s] Stopped", self.agent_id)
