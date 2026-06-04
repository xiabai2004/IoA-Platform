"""A2A (Agent-to-Agent) 协议客户端

用于与其他 A2A 兼容的 Agent 进行通信。

A2A 协议核心概念：
- AgentCard: 描述 Agent 能力的元数据
- Task: Agent 之间交换单位工作
- Message: Agent 之间的通信消息

使用方式：
    client = A2AClient("http://other-agent:8000/a2a")

    # 发现 Agent 能力
    card = await client.discover()

    # 发送任务
    task = await client.send_task("检查网络状态")

    # 获取任务结果
    result = await client.get_task(task.id)
"""

import httpx
import logging
from typing import AsyncIterator

logger = logging.getLogger("a2a.client")


class A2AClient:
    """A2A 协议客户端

    用于与其他 A2A 兼容的 Agent 进行通信
    """

    def __init__(self, base_url: str, timeout: float = 30.0):
        """
        Args:
            base_url: A2A 服务的基础 URL，如 http://localhost:8000/a2a
            timeout: HTTP 请求超时时间
        """
        self._base_url = base_url.rstrip("/")
        self._http = httpx.AsyncClient(timeout=timeout)
        self._agent_card = None

    async def discover(self) -> dict:
        """发现 Agent 能力（获取 Agent Card）

        A2A 协议要求 Agent 在 /.well-known/agent.json 暴露能力描述

        Returns:
            Agent Card 字典
        """
        url = f"{self._base_url}/.well-known/agent.json"
        resp = await self._http.get(url)
        resp.raise_for_status()
        self._agent_card = resp.json()

        logger.info("Discovered A2A agent: %s (skills: %s)",
                     self._agent_card.get("name"),
                     [s.get("id") for s in self._agent_card.get("skills", [])])

        return self._agent_card

    async def send_task(self, message: str,
                        session_id: str | None = None,
                        metadata: dict | None = None) -> dict:
        """发送任务给 Agent

        Args:
            message: 任务描述（自然语言）
            session_id: 会话 ID（可选，用于多轮对话）
            metadata: 额外元数据

        Returns:
            任务对象字典
        """
        task_params = {
            "message": {
                "role": "user",
                "parts": [
                    {"type": "text", "text": message}
                ],
            },
            "acceptedOutputModes": ["text", "data"],
        }

        if session_id:
            task_params["sessionId"] = session_id
        if metadata:
            task_params["metadata"] = metadata

        url = f"{self._base_url}/tasks/send"
        resp = await self._http.post(url, json=task_params)
        resp.raise_for_status()

        task = resp.json()
        logger.info("A2A task sent: %s (state: %s)",
                     task.get("id"), task.get("status", {}).get("state"))

        return task

    async def get_task(self, task_id: str) -> dict:
        """获取任务状态

        Args:
            task_id: 任务 ID

        Returns:
            任务对象字典
        """
        url = f"{self._base_url}/tasks/{task_id}"
        resp = await self._http.post(url)
        resp.raise_for_status()
        return resp.json()

    async def cancel_task(self, task_id: str) -> dict:
        """取消任务

        Args:
            task_id: 任务 ID

        Returns:
            任务对象字典
        """
        url = f"{self._base_url}/tasks/{task_id}/cancel"
        resp = await self._http.post(url)
        resp.raise_for_status()
        return resp.json()

    async def send_task_subscribe(self, message: str,
                                   session_id: str | None = None) -> AsyncIterator[dict]:
        """发送任务并订阅状态更新（SSE 流式）

        Args:
            message: 任务描述
            session_id: 会话 ID

        Yields:
            任务状态更新事件
        """
        task_params = {
            "message": {
                "role": "user",
                "parts": [
                    {"type": "text", "text": message}
                ],
            },
            "acceptedOutputModes": ["text", "data"],
        }

        if session_id:
            task_params["sessionId"] = session_id

        url = f"{self._base_url}/tasks/send_subscribe"

        async with self._http.stream("POST", url, json=task_params) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if line.startswith("data: "):
                    data = line[6:]
                    if data == "[DONE]":
                        break
                    import json
                    yield json.loads(data)

    async def close(self):
        """关闭客户端"""
        await self._http.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()


class A2AAgentDiscovery:
    """A2A Agent 发现工具

    用于发现和管理网络中的 A2A Agent
    """

    def __init__(self):
        self._known_agents: dict[str, dict] = {}

    async def discover_agent(self, url: str) -> dict | None:
        """发现单个 Agent

        Args:
            url: Agent 的 A2A 基础 URL

        Returns:
            Agent Card 或 None
        """
        try:
            async with A2AClient(url) as client:
                card = await client.discover()
                self._known_agents[url] = card
                return card
        except Exception as e:
            logger.warning("Failed to discover agent at %s: %s", url, e)
            return None

    async def discover_multiple(self, urls: list[str]) -> list[dict]:
        """批量发现 Agent

        Args:
            urls: Agent URL 列表

        Returns:
            Agent Card 列表
        """
        import asyncio
        tasks = [self.discover_agent(url) for url in urls]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return [r for r in results if r is not None and not isinstance(r, Exception)]

    def get_known_agents(self) -> dict[str, dict]:
        """获取已知 Agent 列表"""
        return self._known_agents.copy()

    def find_agents_by_skill(self, skill_id: str) -> list[dict]:
        """根据技能查找 Agent

        Args:
            skill_id: 技能 ID

        Returns:
            具有该技能的 Agent Card 列表
        """
        matched = []
        for url, card in self._known_agents.items():
            skills = card.get("skills", [])
            if any(s.get("id") == skill_id for s in skills):
                matched.append({"url": url, "card": card})
        return matched
