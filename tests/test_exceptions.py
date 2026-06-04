"""Tests for the IoA exception hierarchy."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import pytest
from exceptions import (
    IoAError,
    CommunicationError,
    AgentError,
    DiagnosisError,
    RepairError,
    VerificationError,
    ConfigError,
)


class TestExceptionHierarchy:
    """All custom exceptions should inherit from IoAError."""

    def test_communication_error_is_recoverable(self):
        err = CommunicationError("NATS timeout")
        assert err.recoverable is True
        assert err.code == "COMMUNICATION_ERROR"
        assert isinstance(err, IoAError)

    def test_config_error_is_not_recoverable(self):
        err = ConfigError("Missing IOA_PSK")
        assert err.recoverable is False
        assert err.code == "CONFIG_ERROR"
        assert isinstance(err, IoAError)

    def test_agent_error_carries_agent_id(self):
        err = AgentError("Handler failed", agent_id="monitor-east-china")
        assert err.agent_id == "monitor-east-china"
        assert err.recoverable is True

    def test_repair_error_carries_action(self):
        err = RepairError("ACL deploy failed", action="acl_deploy")
        assert err.action == "acl_deploy"

    def test_all_exceptions_are_ioAError(self):
        errors = [
            CommunicationError("x"),
            AgentError("x"),
            DiagnosisError("x"),
            RepairError("x"),
            VerificationError("x"),
            ConfigError("x"),
        ]
        for err in errors:
            assert isinstance(err, IoAError)
            assert isinstance(err, Exception)
