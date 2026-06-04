"""NATS-backed message bus for distributed deployment."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from .base import MessageBus, MessageHandler

logger = logging.getLogger(__name__)


class NatsMessageBus(MessageBus):
    """Message bus backed by NATS — suitable for multi-container deployment.

    Each agent container connects to the shared NATS server and
    communicates via pub/sub + request/reply patterns.
    """

    def __init__(self, servers: str | list[str] = "nats://nats:4222") -> None:
        self.servers = servers if isinstance(servers, str) else ",".join(servers)
        self._nc: Any = None   # nats.aio.client.Client
        self._js: Any = None   # JetStream context (optional)
        self._subscriptions: list[int] = []

    async def connect(self) -> None:
        try:
            import nats
        except ImportError:
            raise ImportError(
                "nats-py is required for distributed mode. "
                "Install it with: pip install nats-py"
            )
        self._nc = await nats.connect(servers=self.servers)
        logger.info("NatsMessageBus connected to %s", self.servers)

    async def close(self) -> None:
        for sid in self._subscriptions:
            try:
                await self._nc.unsubscribe(sid)
            except Exception:
                pass
        self._subscriptions.clear()
        if self._nc:
            await self._nc.drain()
        logger.info("NatsMessageBus closed")

    async def publish(self, topic: str, message: dict[str, Any]) -> None:
        await self._nc.publish(topic, json.dumps(message).encode())

    async def subscribe(self, topic: str, handler: MessageHandler) -> None:
        async def wrapper(msg: Any) -> None:
            try:
                data = json.loads(msg.data.decode())
            except json.JSONDecodeError:
                logger.warning("Invalid JSON on topic %r", msg.subject)
                return
            await handler(msg.subject, data)

        sid = await self._nc.subscribe(topic, cb=wrapper)
        self._subscriptions.append(sid)

    async def request(
        self, topic: str, payload: dict[str, Any], timeout: float = 30.0
    ) -> dict[str, Any]:
        response = await self._nc.request(
            topic, json.dumps(payload).encode(), timeout=timeout
        )
        return json.loads(response.data.decode())
