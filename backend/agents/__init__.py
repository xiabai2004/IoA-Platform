"""Agent package — factory to create all agents."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .base_agent import BaseAgent
    from ioa_middleware.bus import MessageBus


def create_all_agents(bus: "MessageBus", config: dict) -> list["BaseAgent"]:
    """Create all 9 agent instances (5 types × various domains).

    Returns a list ready for ``await agent.start()``.
    """
    from .base_agent import BaseAgent
    from .orchestrator_agent.agent import OrchestratorAgent
    from .monitor_agent.agent import MonitorAgent
    from .diagnoser_agent.agent import DiagnoserAgent
    from .repairer_agent.agent import RepairerAgent
    from .verifier_agent.agent import VerifyAgent
    from .reporter_agent.agent import ReporterAgent

    return [
        # Orchestrator — 1 instance, global domain
        OrchestratorAgent(bus=bus, config=config),

        # Monitors — 4 instances, one per domain
        MonitorAgent(bus=bus, agent_id="monitor-east-china", domain="east-china", config=config),
        MonitorAgent(bus=bus, agent_id="monitor-north-china", domain="north-china", config=config),
        MonitorAgent(bus=bus, agent_id="monitor-south-china", domain="south-china", config=config),
        MonitorAgent(bus=bus, agent_id="monitor-west-china", domain="west-china", config=config),

        # Diagnoser — 1 instance, global domain
        DiagnoserAgent(bus=bus, config=config),

        # Repairer — 1 instance, global domain
        RepairerAgent(bus=bus, config=config),

        # Verifier — 1 instance, global domain
        VerifyAgent(bus=bus, config=config),

        # Reporter — 1 instance, global domain
        ReporterAgent(bus=bus, config=config),
    ]
