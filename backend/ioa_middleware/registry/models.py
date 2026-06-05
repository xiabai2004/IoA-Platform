"""Agent 注册中心 — Pydantic 模型

严格按照架构方案 v2 §3.1.1 Capability Profile JSON Schema 定义。
支持 Agent 能力自描述规范 v1.0。
"""

from pydantic import BaseModel, Field
from typing import Literal, Any

Domain = Literal["east-china", "north-china", "south-china", "west-china", "global"]
AgentStatus = Literal["active", "degraded", "offline"]


class AgentMetadata(BaseModel):
    """Agent 元数据 — 能力自描述信息。"""
    version: str = Field(default="1.0.0", description="Agent 版本号（语义化版本）")
    description: str = Field(default="", description="Agent 功能描述")
    supported_tasks: list[str] = Field(default_factory=list, description="支持的任务类型")
    input_schema: dict[str, Any] | None = Field(None, description="输入参数 JSON Schema")
    output_schema: dict[str, Any] | None = Field(None, description="输出结果 JSON Schema")
    constraints: dict[str, Any] | None = Field(None, description="约束条件")
    performance: dict[str, Any] | None = Field(None, description="性能指标")


class CapabilityProfile(BaseModel):
    """Agent 能力描述 — 注册时提交的完整画像。

    支持能力自描述规范 v1.0，包含：
    - 基础信息：agent_id, domain, capabilities
    - 协议支持：protocols
    - 元数据：metadata（版本、描述、支持的任务、输入输出 Schema 等）
    """
    agent_id: str = Field(..., pattern=r"^[a-z0-9-]{3,64}$", description="Agent 唯一标识")
    domain: Domain = Field(..., description="所属域")
    capabilities: list[str] = Field(..., min_length=1, description="能力标签列表")
    protocols: list[str] = Field(
        default=["ioap-v1"],
        description="支持的通信协议（ioap-v1 运行时激活；mcp-v2024/a2a-v1 服务端就绪）"
    )
    model: str | None = Field(None, description="使用的 LLM 模型（可选）")
    load: float = Field(default=0.0, ge=0.0, le=1.0, description="当前负载（0-1）")
    status: AgentStatus = Field(default="active", description="Agent 状态")
    last_heartbeat_ms: int | None = Field(None, description="最后心跳时间戳")
    endpoint: str | None = Field(None, description="Agent 访问端点")
    metadata: AgentMetadata | None = Field(None, description="能力自描述元数据")

    class Config:
        json_schema_extra = {
            "example": {
                "agent_id": "monitor-east-china",
                "domain": "east-china",
                "capabilities": ["monitor"],
                "protocols": ["ioap-v1", "mcp-v2024", "a2a-v1"],
                "model": None,
                "load": 0.0,
                "status": "active",
                "endpoint": "agent://monitor-east-china",
                "metadata": {
                    "version": "1.0.0",
                    "description": "华东域监控 Agent - 采集网络指标，检测异常",
                    "supported_tasks": ["metrics_collection", "anomaly_detection"],
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "domain": {"type": "string"}
                        }
                    },
                    "output_schema": {
                        "type": "object",
                        "properties": {
                            "metrics": {"type": "object"},
                            "anomalies": {"type": "array"}
                        }
                    }
                }
            }
        }


class HeartbeatRequest(BaseModel):
    """心跳请求。"""
    agent_id: str = Field(..., description="Agent 唯一标识")


class DeregisterRequest(BaseModel):
    """注销请求。"""
    agent_id: str = Field(..., description="Agent 唯一标识")


class QueryRequest(BaseModel):
    """能力查询请求。"""
    capability: str | None = Field(None, description="能力标签")
    domain: Domain | None = Field(None, description="目标域")
    status: AgentStatus | None = Field("active", description="Agent 状态")


class CapabilityMatchResult(BaseModel):
    """能力匹配结果。"""
    agent_id: str
    domain: str
    capabilities: list[str]
    score: float = Field(..., description="匹配度评分（0-1）")
    metadata: AgentMetadata | None = None
