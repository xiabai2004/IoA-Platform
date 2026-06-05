"""In-process message bus for development and testing."""
from __future__ import annotations

import asyncio
import logging
import uuid
from collections import defaultdict
from typing import Any

from .base import MessageBus, MessageHandler
from exceptions import NoHandlerError, MessageTimeoutError

logger = logging.getLogger(__name__)


class MemoryMessageBus(MessageBus):
    """In-process message bus backed by asyncio.

    All messages stay in the same Python process — useful for
    development, testing, and the single-process demo mode.
    """

    def __init__(self) -> None:
        self._handlers: dict[str, list[MessageHandler]] = defaultdict(list)
        self._connected = False

    async def connect(self) -> None:
        self._connected = True
        logger.info("MemoryMessageBus connected")

    async def close(self) -> None:
        self._handlers.clear()
        self._connected = False
        logger.info("MemoryMessageBus closed")

    async def publish(self, topic: str, message: dict[str, Any]) -> None:
        for handler in self._handlers.get(topic, ()):
            try:
                await handler(topic, message)
            except Exception:
                logger.exception(
                    "Handler for topic %r raised an exception", topic
                )

    async def subscribe(self, topic: str, handler: MessageHandler) -> None:
        self._handlers[topic].append(handler)
        logger.debug("Subscribed to topic %r (handler count: %d)", topic, len(self._handlers[topic]))

    async def request(
        self, topic: str, payload: dict[str, Any], timeout: float = 30.0
    ) -> dict[str, Any]:
        handlers = self._handlers.get(topic, [])
        if not handlers:
            raise NoHandlerError(f"No handler registered for topic {topic!r}", topic=topic)

        future: asyncio.Future[dict[str, Any]] = asyncio.Future()

        async def capture(_topic: str, msg: dict[str, Any]) -> None:
            if not future.done():
                future.set_result(msg)

        # Wrap the first handler with a capture so we can get the return value.
        # In a real NATS setup, request-reply is handled natively.
        # In memory mode, the handler publishes its result to a reply topic.
        reply_topic = f"{topic}._reply.{uuid.uuid4().hex}"
        self._handlers[reply_topic].append(capture)

        payload_with_reply = {**payload, "_reply_to": reply_topic}

        # Fire to the first handler (most agent topics have one handler)
        primary = handlers[0]
        try:
            await primary(topic, payload_with_reply)
        except Exception as exc:
            if not future.done():
                future.set_exception(exc)

        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            raise MessageTimeoutError(
                f"Request to topic {topic!r} timed out after {timeout}s"
            )
        finally:
            self._handlers.pop(reply_topic, None)
