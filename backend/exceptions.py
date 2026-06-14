"""IoA exception hierarchy — enables fine-grained error handling.

Usage:
    from exceptions import IoAError, DagValidationError, MessageBusError

    # 抛出时携带额外信息，上层可按 code 精细化处理
    raise DagValidationError("DAG contains a cycle", dag_id=dag_id)

    # API 层做异常 → HTTP 状态码映射
    except DagValidationError as e:
        return JSONResponse({"error": e.code, "detail": e.detail}, status_code=422)
"""
from __future__ import annotations

import asyncio


class IoAError(Exception):
    """Base exception for all IoA platform errors."""

    def __init__(
        self,
        message: str,
        code: str = "INTERNAL_ERROR",
        detail: dict | None = None,
        recoverable: bool = False,
        **extra,
    ) -> None:
        extra.pop("code", None)  # 防止 **extra 携带重复 code
        self.message = message
        self.code = code
        self.detail = detail or {}
        self.recoverable = recoverable
        self.extra = extra
        super().__init__(message)


# ── Agent ──────────────────────────────────────────────────

class AgentError(IoAError):
    """Agent-level processing errors."""

    def __init__(self, message: str, agent_id: str = "", **kw) -> None:
        self.agent_id = agent_id
        super().__init__(message, code="AGENT_ERROR", recoverable=True, **kw)


class AgentNotFoundError(IoAError):
    """Agent not found or not available."""

    def __init__(self, message: str, agent_id: str = "", **kw) -> None:
        self.agent_id = agent_id
        super().__init__(message, code="AGENT_NOT_FOUND", **kw)


class MCPConnectionError(IoAError):
    """MCP session or tool connection failure."""

    def __init__(self, message: str, **kw) -> None:
        super().__init__(message, code="MCP_CONNECTION_ERROR", recoverable=True, **kw)


# ── DAG / Orchestrator ─────────────────────────────────────

class DagError(IoAError):
    """DAG orchestration base error."""

    def __init__(self, message: str, code: str = "DAG_ERROR", **kw) -> None:
        super().__init__(message, code=code, **kw)


class DagValidationError(DagError):
    """DAG definition validation failure (cycle, duplicate node, unknown dep)."""

    def __init__(self, message: str, **kw) -> None:
        super().__init__(message, code="DAG_VALIDATION_ERROR", **kw)


class SchedulerNotInitializedError(IoAError):
    """Scheduler singleton not yet created."""

    def __init__(self, message: str = "DagScheduler not initialized", **kw) -> None:
        super().__init__(message, code="SCHEDULER_NOT_INITIALIZED", **kw)


# ── Message Bus ─────────────────────────────────────────────

class MessageBusError(IoAError):
    """Message bus communication error."""

    def __init__(self, message: str, code: str = "MESSAGE_BUS_ERROR", **kw) -> None:
        super().__init__(message, code=code, recoverable=True, **kw)


class NoHandlerError(MessageBusError):
    """No handler registered for a topic."""

    def __init__(self, message: str, topic: str = "", **kw) -> None:
        self.topic = topic
        super().__init__(message, code="NO_HANDLER", **kw)


class MessageTimeoutError(IoAError):
    """Request-reply pattern timed out."""

    def __init__(self, message: str, **kw) -> None:
        super().__init__(message, code="MESSAGE_TIMEOUT", recoverable=True, **kw)


# ── Communication / Network ────────────────────────────────

class CommunicationError(IoAError):
    """Message bus / network errors — may be retried."""

    def __init__(self, message: str, **kw) -> None:
        super().__init__(message, code="COMMUNICATION_ERROR", recoverable=True, **kw)


# ── Diagnosis / Repair / Verify ─────────────────────────────

class DiagnosisError(IoAError):
    """Diagnosis failures — no root cause identified."""

    def __init__(self, message: str, **kw) -> None:
        super().__init__(message, code="DIAGNOSIS_ERROR", recoverable=True, **kw)


class RepairError(IoAError):
    """Repair action failures."""

    def __init__(self, message: str, action: str = "", **kw) -> None:
        self.action = action
        super().__init__(message, code="REPAIR_ERROR", recoverable=True, **kw)


class VerificationError(IoAError):
    """Verification failures — post-repair metrics still abnormal."""

    def __init__(self, message: str, **kw) -> None:
        super().__init__(message, code="VERIFICATION_ERROR", recoverable=True, **kw)


# ── Auth / Config ──────────────────────────────────────────

class AuthError(IoAError):
    """Authentication/authorization failure."""

    def __init__(self, message: str, **kw) -> None:
        super().__init__(message, code="AUTH_ERROR", **kw)


class ConfigError(IoAError):
    """Configuration errors — not recoverable without intervention."""

    def __init__(self, message: str, **kw) -> None:
        super().__init__(message, code="CONFIG_ERROR", **kw)


# ── Database ───────────────────────────────────────────────

class DatabaseError(IoAError):
    """Database operation error."""

    def __init__(self, message: str, code: str = "DATABASE_ERROR", **kw) -> None:
        super().__init__(message, code=code, recoverable=True, **kw)


class DatabaseNotInitializedError(DatabaseError):
    """Database connection not established."""

    def __init__(self, message: str = "Database not initialized", **kw) -> None:
        super().__init__(message, code="DATABASE_NOT_INITIALIZED", **kw)


# ── Simulator ──────────────────────────────────────────────

class SimulatorNotInitializedError(IoAError):
    """SimulatorState singleton not yet created."""

    def __init__(self, message: str = "SimulatorState not initialized", **kw) -> None:
        super().__init__(message, code="SIMULATOR_NOT_INITIALIZED", **kw)


# ── Helpers ─────────────────────────────────────────────────

def is_runtime_error(exc: BaseException) -> bool:
    """判断是否为可预期的运行时错误（非编程缺陷）。

    用于 except Exception 块中的分流：
        except Exception as exc:
            if is_runtime_error(exc):
                logger.warning("Transient error: %s", exc)
            else:
                logger.critical("Programming bug: %s", exc, exc_info=True)
                raise
    """
    return isinstance(exc, (
        ConnectionError,
        TimeoutError,
        OSError,
        asyncio.CancelledError,
        IoAError,  # 所有 IoA 业务异常都是预期的
    ))
