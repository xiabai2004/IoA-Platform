"""Abstract message bus interface for inter-agent communication."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Callable, Awaitable

MessageHandler = Callable[[str, dict[str, Any]], Awaitable[None]]


class MessageBus(ABC):
    """Abstract message bus for inter-agent communication.

    Agents communicate exclusively through the bus — never via direct
    WebSocket or function calls.  In development mode a MemoryMessageBus
    runs in-process; in production a NatsMessageBus connects to NATS.
    """

    @abstractmethod
    async def connect(self) -> None:
        """Connect to the message bus."""
        ...

    @abstractmethod
    async def close(self) -> None:
        """Disconnect from the message bus gracefully."""
        ...

    @abstractmethod
    async def publish(self, topic: str, message: dict[str, Any]) -> None:
        """Publish a fire-and-forget message to a topic."""
        ...

    @abstractmethod
    async def subscribe(self, topic: str, handler: MessageHandler) -> None:
        """Subscribe to a topic with an async callback handler."""
        ...

    @abstractmethod
    async def request(
        self, topic: str, payload: dict[str, Any], timeout: float = 30.0
    ) -> dict[str, Any]:
        """Send a request and wait for a single response (RPC pattern)."""
        ...
