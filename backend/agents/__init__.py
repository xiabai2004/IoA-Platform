"""IoA Agent 统一工厂

create_all_agents(config) → list[BaseAgent]

创建全部 9 个 Agent：
- 1 个 Orchestrator Agent（全局，NL → DAG 入口）
- 4 个 Monitor Agent（每域一个）
- 1 个 Diagnoser Agent（全局）
- 1 个 Repairer Agent（全局）
- 1 个 Verifier Agent（全局）
- 1 个 Reporter Agent（全局）

工具调用协议：
- 优先使用 MCP 协议（Model Context Protocol）
- MCP 不可用时自动降级为 HTTP 直连
"""

import logging
from agents.base_agent import BaseAgent
from agents.tool_client import create_tool_client
from agents.orchestrator_agent.agent import create_orchestrator_agent
from agents.monitor_agent.agent import create_monitor_agents
from agents.diagnoser_agent.agent import create_diagnoser_agent
from agents.repairer_agent.agent import create_repairer_agent
from agents.reporter_agent.agent import create_reporter_agent
from agents.verifier_agent.agent import create_verifier_agent

logger = logging.getLogger("agents.factory")


def create_all_agents(config: dict) -> list[BaseAgent]:
    """创建所有 Agent 并返回列表。

    用于 main.py lifespan 中统一启动。
    所有 Agent 共享同一个 ToolClient 实例（优先 MCP 协议）。
    """
    # 创建共享的 ToolClient（优先 MCP，降级 HTTP）
    tool_client = create_tool_client(config, prefer_mcp=True)
    logger.info("ToolClient created with protocol: %s", tool_client.protocol)

    agents: list[BaseAgent] = []

    # 编排入口 Agent
    agents.append(create_orchestrator_agent(config))

    # 4 个域监控 Agent
    agents.extend(create_monitor_agents(config))

    # 全局分析 Agent（共享 ToolClient）
    agents.append(create_diagnoser_agent(config))
    agents.append(create_repairer_agent(config))
    agents.append(create_reporter_agent(config))
    agents.append(create_verifier_agent(config))

    logger.info("Total agents created: %d (tool protocol: %s)", len(agents), tool_client.protocol)
    return agents
