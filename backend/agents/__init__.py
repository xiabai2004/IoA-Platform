"""IoA Agent 统一工厂

create_all_agents(config) → list[BaseAgent]

创建全部 8 个 Agent：
- 1 个 Orchestrator Agent（全局，NL → DAG 入口）
- 4 个 Monitor Agent（每域一个）
- 1 个 Diagnoser Agent（全局）
- 1 个 Repairer Agent（全局）
- 1 个 Reporter Agent（全局）
"""

from agents.base_agent import BaseAgent
from agents.orchestrator_agent.agent import create_orchestrator_agent
from agents.monitor_agent.agent import create_monitor_agents
from agents.diagnoser_agent.agent import create_diagnoser_agent
from agents.repairer_agent.agent import create_repairer_agent
from agents.reporter_agent.agent import create_reporter_agent
from agents.verifier_agent.agent import create_verifier_agent


def create_all_agents(config: dict) -> list[BaseAgent]:
    """创建所有 Agent 并返回列表。

    用于 main.py lifespan 中统一启动。
    """
    agents: list[BaseAgent] = []

    # 编排入口 Agent
    agents.append(create_orchestrator_agent(config))

    # 4 个域监控 Agent
    agents.extend(create_monitor_agents(config))

    # 全局分析 Agent
    agents.append(create_diagnoser_agent(config))
    agents.append(create_repairer_agent(config))
    agents.append(create_reporter_agent(config))
    agents.append(create_verifier_agent(config))

    print(f"[Factory] Total agents created: {len(agents)}")
    return agents
