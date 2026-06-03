"""A2A (Agent-to-Agent) 协议服务器

基于 Google A2A 协议规范实现，提供：
- Agent Card 发现（/.well-known/agent.json）
- Task 生命周期管理（创建、执行、完成、失败）
- Message 通信
- Streaming 支持

A2A 协议核心概念：
- AgentCard: 描述 Agent 能力的元数据
- Task: Agent 之间交换单位工作
- Message: Agent 之间的通信消息

与 IoAP 协议的关系：
- A2A 是标准协议，用于外部 Agent 互操作
- IoAP 是内部协议，用于平台内 Agent 通信
- 本模块提供 A2A → IoAP 的桥接

参考: https://github.com/google/A2A
"""

import uuid
import time
import json
import logging
from enum import Enum
from typing import Any
from dataclasses import dataclass, field

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

logger = logging.getLogger("a2a")

# ══════════════════════════════════════════════════════════════
# A2A 协议数据模型
# ══════════════════════════════════════════════════════════════


class TaskState(str, Enum):
    """任务状态枚举"""
    submitted = "submitted"
    working = "working"
    input_required = "input-required"
    completed = "completed"
    canceled = "canceled"
    failed = "failed"


class PartType(str, Enum):
    """消息部分类型"""
    text = "text"
    file = "file"
    data = "data"


class Part(BaseModel):
    """消息部分 — A2A 协议标准格式"""
    type: PartType
    text: str | None = None
    file: dict | None = None
    data: dict | None = None
    metadata: dict | None = None


class Message(BaseModel):
    """A2A 消息"""
    role: str = Field(..., description="消息角色: user 或 agent")
    parts: list[Part] = Field(..., description="消息内容部分")
    metadata: dict | None = None


class Artifact(BaseModel):
    """任务产物"""
    name: str | None = None
    description: str | None = None
    parts: list[Part] = Field(default_factory=list)
    metadata: dict | None = None


class TaskStatus(BaseModel):
    """任务状态"""
    state: TaskState
    message: Message | None = None
    timestamp: str | None = None


class Task(BaseModel):
    """A2A 任务 — 核心数据结构"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    sessionId: str | None = None
    status: TaskStatus
    artifacts: list[Artifact] = Field(default_factory=list)
    metadata: dict | None = None
    history: list[Message] = Field(default_factory=list)


class TaskSendParams(BaseModel):
    """任务发送参数"""
    id: str | None = Field(default_factory=lambda: str(uuid.uuid4()))
    sessionId: str | None = None
    message: Message
    acceptedOutputModes: list[str] = Field(default_factory=lambda: ["text"])
    metadata: dict | None = None


class AgentCard(BaseModel):
    """Agent Card — 描述 Agent 能力的元数据

    类似于 OpenAPI Spec，但用于 Agent 发现
    """
    name: str
    description: str
    url: str
    version: str = "1.0.0"
    documentationUrl: str | None = None
    provider: dict | None = None
    capabilities: dict = Field(default_factory=dict)
    authentication: dict | None = None
    defaultInputModes: list[str] = Field(default_factory=lambda: ["text"])
    defaultOutputModes: list[str] = Field(default_factory=lambda: ["text"])
    skills: list[dict] = Field(default_factory=list)


# ══════════════════════════════════════════════════════════════
# A2A 任务存储
# ══════════════════════════════════════════════════════════════


class TaskStore:
    """任务存储 — 内存存储（可扩展为 SQLite）"""

    def __init__(self):
        self._tasks: dict[str, Task] = {}
        self._sessions: dict[str, list[str]] = {}  # sessionId → taskIds

    def create_task(self, params: TaskSendParams) -> Task:
        """创建新任务"""
        task = Task(
            id=params.id or str(uuid.uuid4()),
            sessionId=params.sessionId,
            status=TaskStatus(
                state=TaskState.submitted,
                timestamp=_now_iso(),
            ),
            history=[params.message],
            metadata=params.metadata,
        )
        self._tasks[task.id] = task

        if task.sessionId:
            if task.sessionId not in self._sessions:
                self._sessions[task.sessionId] = []
            self._sessions[task.sessionId].append(task.id)

        return task

    def get_task(self, task_id: str) -> Task | None:
        """获取任务"""
        return self._tasks.get(task_id)

    def update_task_status(self, task_id: str, state: TaskState,
                           message: Message | None = None) -> Task | None:
        """更新任务状态"""
        task = self._tasks.get(task_id)
        if not task:
            return None

        task.status = TaskStatus(
            state=state,
            message=message,
            timestamp=_now_iso(),
        )

        if message:
            task.history.append(message)

        return task

    def add_artifact(self, task_id: str, artifact: Artifact) -> Task | None:
        """添加任务产物"""
        task = self._tasks.get(task_id)
        if not task:
            return None
        task.artifacts.append(artifact)
        return task

    def get_tasks_by_session(self, session_id: str) -> list[Task]:
        """获取会话的所有任务"""
        task_ids = self._sessions.get(session_id, [])
        return [self._tasks[tid] for tid in task_ids if tid in self._tasks]


def _now_iso() -> str:
    """获取当前时间的 ISO 格式"""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


# ══════════════════════════════════════════════════════════════
# Agent Card 配置
# ══════════════════════════════════════════════════════════════


def create_agent_card(base_url: str = "http://localhost:8000") -> AgentCard:
    """创建 IoA 平台的 Agent Card

    Agent Card 是 A2A 协议的核心，描述 Agent 的能力
    类似于 Web API 的 OpenAPI Spec
    """
    return AgentCard(
        name="IoA Network Operations Agent",
        description="基于智能体互联网的分布式网络运维协同平台，提供网络监控、故障诊断、自动修复、闭环验证等能力",
        url=f"{base_url}/a2a",
        version="1.0.0",
        documentationUrl=f"{base_url}/docs",
        provider={
            "organization": "IoA Team",
            "url": "https://xiabai.site",
        },
        capabilities={
            "streaming": True,
            "pushNotifications": True,
            "stateTransitionHistory": True,
        },
        authentication={
            "schemes": ["bearer"],
            "credentials": "Bearer token required",
        },
        defaultInputModes=["text"],
        defaultOutputModes=["text", "data"],
        skills=[
            {
                "id": "network_monitoring",
                "name": "网络监控",
                "description": "采集网络域指标（延迟、丢包、带宽使用率）",
                "tags": ["monitor", "metrics", "network"],
                "examples": [
                    "检查华东网络状态",
                    "获取所有域的网络指标",
                ],
            },
            {
                "id": "fault_diagnosis",
                "name": "故障诊断",
                "description": "分析网络异常，识别故障类型和根因",
                "tags": ["diagnose", "fault", "analysis"],
                "examples": [
                    "诊断华东网络延迟高的原因",
                    "分析丢包率上升的根因",
                ],
            },
            {
                "id": "auto_repair",
                "name": "自动修复",
                "description": "执行网络故障修复操作",
                "tags": ["repair", "fix", "remediate"],
                "examples": [
                    "修复华东网络故障",
                    "清除所有网络故障",
                ],
            },
            {
                "id": "closed_loop_verification",
                "name": "闭环验证",
                "description": "验证修复效果，三态判定（pass/retry/fail）",
                "tags": ["verify", "validation", "closed-loop"],
                "examples": [
                    "验证修复是否成功",
                    "检查指标是否恢复正常",
                ],
            },
            {
                "id": "full_remediation",
                "name": "全流程修复",
                "description": "从监控到验证的完整故障修复流程",
                "tags": ["full", "remediation", "end-to-end"],
                "examples": [
                    "华东网络异常，帮我诊断修复",
                    "全流程处理网络故障",
                ],
            },
        ],
    )


# ══════════════════════════════════════════════════════════════
# A2A → IoAP 桥接器
# ══════════════════════════════════════════════════════════════


class A2AToIoAPBridge:
    """A2A 到 IoAP 协议桥接器

    将 A2A 协议的 Task/Message 转换为 IoAP 协议的消息格式
    """

    def __init__(self, ioap_send_func):
        """
        Args:
            ioap_send_func: IoAP 消息发送函数 async (msg: dict) -> dict
        """
        self._ioap_send = ioap_send_func

    async def send_task_as_ioap(self, task_params: TaskSendParams,
                                 to_agent: str = "orchestrator-agent") -> dict:
        """将 A2A Task 转换为 IoAP 消息并发送"""

        # 提取文本内容
        user_text = ""
        for part in task_params.message.parts:
            if part.type == PartType.text and part.text:
                user_text += part.text

        # 构造 IoAP 消息
        ioap_msg = {
            "msg_id": str(uuid.uuid4()),
            "from_agent": "a2a-client",
            "to_agent": to_agent,
            "intent": {
                "type": "task",
                "description": user_text[:200],
                "priority": "normal",
            },
            "payload": {
                "a2a_task_id": task_params.id,
                "a2a_session_id": task_params.sessionId,
                "message": user_text,
                "source": "a2a",
            },
            "correlation_id": task_params.id,
            "ts_ms": int(time.time() * 1000),
        }

        # 发送 IoAP 消息
        result = await self._ioap_send(ioap_msg)
        return result

    @staticmethod
    def ioap_result_to_a2a_message(ioap_result: dict) -> Message:
        """将 IoAP 结果转换为 A2A Message"""
        parts = []

        # 提取输出内容
        output = ioap_result.get("output", ioap_result)
        if isinstance(output, dict):
            # 结构化数据
            parts.append(Part(
                type=PartType.data,
                data=output,
            ))
            # 文本摘要
            message = output.get("message", "")
            if message:
                parts.append(Part(
                    type=PartType.text,
                    text=message,
                ))
        elif isinstance(output, str):
            parts.append(Part(
                type=PartType.text,
                text=output,
            ))

        return Message(
            role="agent",
            parts=parts,
            metadata={"source": "ioa"},
        )


# ══════════════════════════════════════════════════════════════
# A2A API 路由
# ══════════════════════════════════════════════════════════════


router = APIRouter(prefix="/a2a", tags=["A2A"])

# 全局实例
_task_store = TaskStore()
_bridge: A2AToIoAPBridge | None = None


def init_a2a_router(ioap_send_func) -> APIRouter:
    """初始化 A2A 路由"""
    global _bridge
    _bridge = A2AToIoAPBridge(ioap_send_func)
    return router


# ── Agent Card 发现 ──────────────────────────────────────────


@router.get(
    "/.well-known/agent.json",
    summary="Agent Card 发现",
    description="返回 Agent Card，描述本 Agent 的能力、技能和认证方式。这是 A2A 协议的标准发现端点。",
    response_model=AgentCard,
)
async def discover_agent_card(request: Request):
    """Agent Card 发现端点

    A2A 协议要求 Agent 在 /.well-known/agent.json 暴露自己的能力描述
    类似于 Web API 的 OpenAPI Spec
    """
    base_url = str(request.base_url).rstrip("/")
    card = create_agent_card(base_url)
    return card.model_dump()


# ── Task 管理 ────────────────────────────────────────────────


@router.post(
    "/tasks/send",
    summary="发送任务",
    description="向 Agent 发送一个任务。Agent 会异步执行任务并更新状态。",
    response_model=Task,
)
async def send_task(params: TaskSendParams):
    """发送任务给 Agent

    A2A 协议的核心端点，用于创建和执行任务
    """
    if not _bridge:
        raise HTTPException(status_code=500, detail="A2A bridge not initialized")

    # 创建任务
    task = _task_store.create_task(params)

    # 更新状态为 working
    _task_store.update_task_status(task.id, TaskState.working)

    try:
        # 桥接到 IoAP 协议执行
        result = await _bridge.send_task_as_ioap(params)

        # 转换结果为 A2A 消息
        response_msg = A2AToIoAPBridge.ioap_result_to_a2a_message(result)

        # 添加产物
        artifact = Artifact(
            name="result",
            description="任务执行结果",
            parts=response_msg.parts,
        )
        _task_store.add_artifact(task.id, artifact)

        # 更新状态为 completed
        _task_store.update_task_status(
            task.id,
            TaskState.completed,
            message=response_msg,
        )

    except Exception as e:
        logger.exception("A2A task execution failed")
        error_msg = Message(
            role="agent",
            parts=[Part(type=PartType.text, text=f"Task failed: {str(e)}")],
        )
        _task_store.update_task_status(
            task.id,
            TaskState.failed,
            message=error_msg,
        )

    return _task_store.get_task(task.id)


@router.post(
    "/tasks/send_subscribe",
    summary="发送任务并订阅状态更新",
    description="发送任务并通过 SSE 流式接收状态更新。",
)
async def send_task_subscribe(params: TaskSendParams):
    """发送任务并订阅状态更新（SSE 流式响应）"""
    from fastapi.responses import StreamingResponse
    import asyncio

    if not _bridge:
        raise HTTPException(status_code=500, detail="A2A bridge not initialized")

    # 创建任务
    task = _task_store.create_task(params)

    async def event_stream():
        """SSE 事件流"""
        # 发送 submitted 状态
        yield f"data: {json.dumps({'task': task.model_dump()}, default=str)}\n\n"

        # 更新为 working
        _task_store.update_task_status(task.id, TaskState.working)
        yield f"data: {json.dumps({'task': _task_store.get_task(task.id).model_dump()}, default=str)}\n\n"

        try:
            # 执行任务
            result = await _bridge.send_task_as_ioap(params)
            response_msg = A2AToIoAPBridge.ioap_result_to_a2a_message(result)

            # 添加产物
            artifact = Artifact(
                name="result",
                description="任务执行结果",
                parts=response_msg.parts,
            )
            _task_store.add_artifact(task.id, artifact)

            # 更新为 completed
            _task_store.update_task_status(
                task.id,
                TaskState.completed,
                message=response_msg,
            )

            yield f"data: {json.dumps({'task': _task_store.get_task(task.id).model_dump()}, default=str)}\n\n"

        except Exception as e:
            error_msg = Message(
                role="agent",
                parts=[Part(type=PartType.text, text=f"Task failed: {str(e)}")],
            )
            _task_store.update_task_status(
                task.id,
                TaskState.failed,
                message=error_msg,
            )
            yield f"data: {json.dumps({'task': _task_store.get_task(task.id).model_dump()}, default=str)}\n\n"

        # 结束流
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )


@router.post(
    "/tasks/{task_id}",
    summary="获取任务状态",
    description="根据任务 ID 获取任务的当前状态和结果。",
    response_model=Task,
)
async def get_task(task_id: str):
    """获取任务状态"""
    task = _task_store.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    return task


@router.post(
    "/tasks/{task_id}/cancel",
    summary="取消任务",
    description="取消正在执行的任务。",
    response_model=Task,
)
async def cancel_task(task_id: str):
    """取消任务"""
    task = _task_store.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

    if task.status.state in (TaskState.completed, TaskState.canceled, TaskState.failed):
        raise HTTPException(
            status_code=400,
            detail=f"Task {task_id} is already {task.status.state.value}",
        )

    cancel_msg = Message(
        role="agent",
        parts=[Part(type=PartType.text, text="Task canceled by user")],
    )
    _task_store.update_task_status(task_id, TaskState.canceled, cancel_msg)

    return _task_store.get_task(task_id)


# ── 健康检查 ─────────────────────────────────────────────────


@router.get(
    "/health",
    summary="A2A 服务健康检查",
    description="检查 A2A 服务是否正常运行。",
)
async def health_check():
    """健康检查"""
    return {
        "status": "ok",
        "protocol": "a2a",
        "version": "1.0.0",
        "task_count": len(_task_store._tasks),
    }
