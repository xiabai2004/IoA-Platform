"""IoA exception hierarchy — enables fine-grained error handling."""
from __future__ import annotations


class IoAError(Exception):
    """Base exception for all IoA platform errors."""

    def __init__(
        self,
        message: str,
        code: str = "INTERNAL_ERROR",
        recoverable: bool = False,
    ) -> None:
        self.message = message
        self.code = code
        self.recoverable = recoverable
        super().__init__(message)


class CommunicationError(IoAError):
    """Message bus / network errors — may be retried."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="COMMUNICATION_ERROR", recoverable=True)


class AgentError(IoAError):
    """Agent-level processing errors."""

    def __init__(self, message: str, agent_id: str = "") -> None:
        self.agent_id = agent_id
        super().__init__(message, code="AGENT_ERROR", recoverable=True)


class DiagnosisError(IoAError):
    """Diagnosis failures — no root cause identified."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="DIAGNOSIS_ERROR", recoverable=True)


class RepairError(IoAError):
    """Repair action failures."""

    def __init__(self, message: str, action: str = "") -> None:
        self.action = action
        super().__init__(message, code="REPAIR_ERROR", recoverable=True)


class VerificationError(IoAError):
    """Verification failures — post-repair metrics still abnormal."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="VERIFICATION_ERROR", recoverable=True)


class ConfigError(IoAError):
    """Configuration errors — not recoverable without intervention."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="CONFIG_ERROR", recoverable=False)
