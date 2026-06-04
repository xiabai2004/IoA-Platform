"""Agent 注册中心 — SQLite 存储层

支持能力自描述规范 v1.0，包含 metadata 字段。
"""

import time
import json
import logging
from ioa_middleware.db import execute, fetch_all, fetch_one
from ioa_middleware.registry.models import CapabilityProfile

logger = logging.getLogger("registry.store")


async def register_agent(profile: CapabilityProfile) -> None:
    """注册（或重新注册）一个 Agent。已存在则覆盖更新。

    支持能力自描述元数据存储。
    """
    # 序列化 metadata
    metadata_json = None
    if profile.metadata:
        metadata_json = json.dumps(profile.metadata.model_dump())

    await execute(
        """INSERT OR REPLACE INTO agents
           (agent_id, domain, capabilities, protocols, model, load, status, endpoint,
            last_heartbeat_ms, registered_at_ms, metadata)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            profile.agent_id,
            profile.domain,
            json.dumps(profile.capabilities),
            json.dumps(profile.protocols),
            profile.model,
            profile.load,
            profile.status,
            profile.endpoint,
            int(time.time() * 1000),
            int(time.time() * 1000),
            metadata_json,
        ),
    )
    logger.info("Agent %s registered (domain=%s, caps=%s, version=%s)",
                profile.agent_id, profile.domain, profile.capabilities,
                profile.metadata.version if profile.metadata else "N/A")


async def heartbeat(agent_id: str) -> bool:
    """更新心跳时间。返回 True 表示 Agent 存在。"""
    now_ms = int(time.time() * 1000)
    cursor = await execute(
        "UPDATE agents SET last_heartbeat_ms = ?, status = 'active' WHERE agent_id = ?",
        (now_ms, agent_id),
    )
    return cursor.rowcount > 0


async def deregister_agent(agent_id: str) -> bool:
    """注销 Agent（软删除：标记为 offline）。"""
    cursor = await execute(
        "DELETE FROM agents WHERE agent_id = ?",
        (agent_id,),
    )
    return cursor.rowcount > 0


async def get_agent(agent_id: str) -> dict | None:
    """获取单个 Agent 信息。"""
    return await fetch_one("SELECT * FROM agents WHERE agent_id = ?", (agent_id,))


async def list_agents(domain: str | None = None, status: str | None = "active") -> list[dict]:
    """列出 Agent，可按域和状态过滤。"""
    if domain:
        return await fetch_all(
            "SELECT * FROM agents WHERE domain = ? AND status = ?", (domain, status)
        )
    return await fetch_all("SELECT * FROM agents WHERE status = ?", (status,))


async def query_by_capability(capability: str, status: str = "active") -> list[dict]:
    """按能力标签查询 Agent — 用 JSON 包含匹配。"""
    all_agents = await list_agents(status=status)
    matched = []
    for a in all_agents:
        caps = json.loads(a["capabilities"]) if isinstance(a["capabilities"], str) else a["capabilities"]
        if capability in caps:
            matched.append(a)
    return matched


async def mark_offline(agent_id: str) -> None:
    """将 Agent 标记为 offline。"""
    await execute(
        "UPDATE agents SET status = 'offline' WHERE agent_id = ?",
        (agent_id,),
    )
