"""Message bus abstraction — factory + re-exports."""
from __future__ import annotations

import os

from .base import MessageBus
from .memory_bus import MemoryMessageBus

__all__ = ["MessageBus", "MemoryMessageBus", "NatsMessageBus", "create_bus"]


def create_bus(config: dict | None = None) -> MessageBus:
    """Create the appropriate message bus based on configuration.

    Reads IOA_BUS_BACKEND from environment or config:
      - ``"memory"`` → MemoryMessageBus (in-process, dev mode)
      - ``"nats"``  → NatsMessageBus  (distributed, prod mode)
    """
    config = config or {}
    backend = (
        os.environ.get("IOA_BUS_BACKEND")
        or config.get("bus", {}).get("backend", "memory")
    )

    if backend == "nats":
        servers = (
            os.environ.get("NATS_SERVERS")
            or config.get("bus", {}).get("nats_servers", "nats://nats:4222")
        )
        from .nats_bus import NatsMessageBus  # late import to avoid requiring nats-py
        return NatsMessageBus(servers=servers)

    return MemoryMessageBus()
