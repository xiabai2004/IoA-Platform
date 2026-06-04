"""Base agent class — all specialized agents inherit from this."""
from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Any

from ioa_middleware.bus import MessageBus

logger = logging.getLogger(__name__)


class BaseAgent(ABC):
    """Abstract base for all IoA agents.

    Agents communicate exclusively through a :class:`MessageBus`.
    Each agent subscribes to its own topic (``agent.<id>``) and
    a domain + capability topic (``domain.<domain>.<capability>``).
    """

    def __init__(
        self,
        agent_id: str,
        domain: str,
        capability: str,
        bus: MessageBus,
        config: dict[str, Any] | None = None,
    ) -> None:
        self.agent_id = agent_id
        self.domain = domain
        self.capability = capability
        self.bus = bus
        self.config = config or {}
        self._running = False
        self._heartbeat_task: asyncio.Task[None] | None = None

        # Topics
        self._agent_topic = f"agent.{agent_id}"
        self._domain_topic = f"domain.{domain}.{capability}"

    # ------------------------------------------------------------------
    # Subclass contract
    # ------------------------------------------------------------------

    @abstractmethod
    async def handle_message(
        self, topic: str, message: dict[str, Any]
    ) -> dict[str, Any]:
        """Process an incoming message and return a result dict."""
        ...

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Connect to bus, subscribe to topics, register, begin heartbeat."""
        await self.bus.connect()
        self._running = True

        await self.bus.subscribe(self._agent_topic, self._on_message)
        await self.bus.subscribe(self._domain_topic, self._on_message)

        await self._register()

        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        logger.info("Agent %s started (domain=%s cap=%s)", self.agent_id, self.domain, self.capability)

    async def stop(self) -> None:
        """Gracefully stop the agent."""
        self._running = False
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
            self._heartbeat_task = None
        await self.bus.close()
        logger.info("Agent %s stopped", self.agent_id)

    # ------------------------------------------------------------------
    # Communication helpers
    # ------------------------------------------------------------------

    async def send_message(
        self, target_topic: str, payload: dict[str, Any], timeout: float = 30.0
    ) -> dict[str, Any]:
        """Send a request to another agent/service and await response."""
        return await self.bus.request(target_topic, payload, timeout=timeout)

    async def publish(self, topic: str, message: dict[str, Any]) -> None:
        """Fire-and-forget publish to a topic."""
        await self.bus.publish(topic, message)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _on_message(self, topic: str, message: dict[str, Any]) -> None:
        """Internal callback: route incoming message to handle_message."""
        reply_topic = message.get("_reply_to")
        from_agent = message.get("from_agent", "")
        try:
            result = await self.handle_message(topic, message)
        except Exception:
            logger.exception(
                "Agent %s failed to handle message on %s", self.agent_id, topic
            )
            result = {"error": "internal_error", "agent": self.agent_id}

        # Reply via bus: to _reply_to topic first, then to sender's agent topic
        # Echo dag_id/node_id from the request so scheduler can correlate results
        orig_payload = message.get("payload", {})
        if isinstance(result, dict):
            result.setdefault("dag_id", orig_payload.get("dag_id", ""))
            result.setdefault("node_id", orig_payload.get("node_id", ""))
        result_msg = {
            "type": "result",
            "from_agent": self.agent_id,
            "to_agent": from_agent,
            "payload": {"result": result} if isinstance(result, dict) else {"result": {"data": result}},
            "correlation_id": message.get("correlation_id", ""),
            "msg_id": message.get("msg_id", ""),
        }
        if reply_topic:
            await self.bus.publish(reply_topic, result_msg)
        if from_agent:
            await self.bus.publish(f"agent.{from_agent}", result_msg)

    async def _register(self) -> None:
        """Register this agent with the central registry."""
        registration = {
            "agent_id": self.agent_id,
            "domain": self.domain,
            "capability": self.capability,
            "status": "active",
        }
        await self.bus.publish("registry.register", registration)
        logger.debug("Agent %s sent registration", self.agent_id)

    async def _heartbeat_loop(self) -> None:
        interval = self.config.get("heartbeat_interval", 10)
        while self._running:
            try:
                await self.bus.publish(
                    "registry.heartbeat", {"agent_id": self.agent_id}
                )
            except Exception:
                logger.warning(
                    "Agent %s heartbeat failed", self.agent_id, exc_info=True
                )
            await asyncio.sleep(interval)
