"""Tests for differentiated repair strategies."""
import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from simulator.faults import FAULT_REPAIR_STRATEGIES, REPAIR_HANDLERS


class TestRepairStrategies:
    """Every fault type must have a valid primary + fallback strategy."""

    @pytest.mark.parametrize("fault_type,expected_primary", [
        ("link_congestion", "traffic_shape"),
        ("link_outage", "link_failover"),
        ("cpu_overload", "restart_service"),
        ("ddos", "acl_deploy"),
        ("misconfig", "restart_service"),
        ("device_failure", "link_failover"),
    ])
    def test_fault_has_primary_strategy(self, fault_type, expected_primary):
        assert fault_type in FAULT_REPAIR_STRATEGIES
        strategy = FAULT_REPAIR_STRATEGIES[fault_type]
        assert "primary" in strategy
        assert strategy["primary"] == expected_primary
        assert strategy["primary"] in REPAIR_HANDLERS

    @pytest.mark.parametrize("fault_type", [
        "link_congestion", "link_outage", "cpu_overload",
        "ddos", "misconfig", "device_failure",
    ])
    def test_fault_has_fallback_strategy(self, fault_type):
        strategy = FAULT_REPAIR_STRATEGIES[fault_type]
        assert "fallback" in strategy
        if strategy["fallback"]:
            assert strategy["fallback"] in REPAIR_HANDLERS

    def test_all_handlers_are_callable(self):
        """Every repair handler must be a callable function."""
        for name, handler in REPAIR_HANDLERS.items():
            assert callable(handler), f"{name} is not callable"

    def test_strategy_count(self):
        """We should have exactly 6 fault types with strategies."""
        assert len(FAULT_REPAIR_STRATEGIES) == 6
