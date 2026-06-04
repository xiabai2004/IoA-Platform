"""Standalone agent entrypoint — runs a single agent in its own process.

Environment variables:
    AGENT_NAME      — agent class name: orchestrator|monitor|diagnoser|repairer|verifier|reporter
    AGENT_ID        — unique agent ID (e.g. "monitor-east-china")
    AGENT_DOMAIN    — domain (e.g. "east-china", "global")
    AGENT_CAPABILITY— capability (e.g. "monitor", "diagnose")
    NATS_SERVERS    — NATS server URL (nats://nats:4222)
    IOA_BUS_BACKEND — "nats" (default) or "memory"
    IOA_PSK         — pre-shared key
    DEEPSEEK_API_KEY— LLM API key (orchestrator/diagnoser/reporter only)
"""
import asyncio
import logging
import os
import sys

# Add backend to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/../backend")

from ioa_middleware.bus import create_bus

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("agent_entrypoint")

AGENT_FACTORIES = {
    "orchestrator": lambda bus, cfg: __import__("agents.orchestrator_agent.agent", fromlist=["OrchestratorAgent"]).OrchestratorAgent(bus=bus, config=cfg),
    "monitor":       lambda bus, cfg, aid, dom: __import__("agents.monitor_agent.agent", fromlist=["MonitorAgent"]).MonitorAgent(bus=bus, agent_id=aid, domain=dom, config=cfg),
    "diagnoser":     lambda bus, cfg: __import__("agents.diagnoser_agent.agent", fromlist=["DiagnoserAgent"]).DiagnoserAgent(bus=bus, config=cfg),
    "repairer":      lambda bus, cfg: __import__("agents.repairer_agent.agent", fromlist=["RepairerAgent"]).RepairerAgent(bus=bus, config=cfg),
    "verifier":      lambda bus, cfg: __import__("agents.verifier_agent.agent", fromlist=["VerifyAgent"]).VerifyAgent(bus=bus, config=cfg),
    "reporter":      lambda bus, cfg: __import__("agents.reporter_agent.agent", fromlist=["ReporterAgent"]).ReporterAgent(bus=bus, config=cfg),
}


async def main():
    agent_name = os.environ.get("AGENT_NAME", "")
    agent_id = os.environ.get("AGENT_ID", f"{agent_name}-agent")
    agent_domain = os.environ.get("AGENT_DOMAIN", "global")
    agent_capability = os.environ.get("AGENT_CAPABILITY", agent_name)

    if agent_name not in AGENT_FACTORIES:
        logger.fatal("Unknown AGENT_NAME=%r. Must be one of: %s", agent_name, list(AGENT_FACTORIES))
        sys.exit(1)

    config = {
        "heartbeat_interval": 10,
        "bus": {"backend": os.environ.get("IOA_BUS_BACKEND", "nats")},
    }

    bus = create_bus(config)
    await bus.connect()
    logger.info("Bus connected (backend=%s)", config["bus"]["backend"])

    factory = AGENT_FACTORIES[agent_name]
    if agent_name == "monitor":
        agent = factory(bus, config, agent_id, agent_domain)
    else:
        agent = factory(bus, config)

    await agent.start()
    logger.info("Agent %s started — listening on topics", agent.agent_id)

    # Keep running until SIGTERM
    stop_event = asyncio.Event()
    loop = asyncio.get_event_loop()
    try:
        loop.add_signal_handler(__import__("signal").SIGTERM, stop_event.set)
        loop.add_signal_handler(__import__("signal").SIGINT, stop_event.set)
    except NotImplementedError:
        pass  # Windows

    await stop_event.wait()
    logger.info("Shutting down agent %s...", agent.agent_id)
    await agent.stop()
    await bus.close()


if __name__ == "__main__":
    asyncio.run(main())
