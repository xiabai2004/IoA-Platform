"""Tests for DAG scheduler — topological sort, cycle detection, retry."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import pytest
from ioa_middleware.bus import MemoryMessageBus
from ioa_middleware.orchestrator.models import DagDefinition, DagNodeDef
from ioa_middleware.orchestrator.scheduler import DagScheduler


class TestDagTopologicalSort:
    """Kahn algorithm — linear, parallel, and cyclic DAGs."""

    def _make_scheduler(self):
        """Create a DagScheduler with memory bus for unit testing."""
        bus = MemoryMessageBus()
        return DagScheduler(bus=bus, config={})

    def test_linear_dag(self):
        """AA → BB → CC should produce [aa, bb, cc]."""
        nodes = [
            DagNodeDef(node_id="aa", type="monitor", depends_on=[], capability="monitor", domain="east"),
            DagNodeDef(node_id="bb", type="diagnose", depends_on=["aa"], capability="diagnose", domain="global"),
            DagNodeDef(node_id="cc", type="repair", depends_on=["bb"], capability="repair", domain="global"),
        ]
        scheduler = self._make_scheduler()
        order = scheduler._topological_sort(nodes)
        ids = [n.node_id for n in order]
        assert ids == ["aa", "bb", "cc"]

    def test_parallel_dag(self):
        """AA and BB independent → both must come before CC."""
        nodes = [
            DagNodeDef(node_id="aa", type="monitor", depends_on=[], capability="monitor", domain="east"),
            DagNodeDef(node_id="bb", type="monitor", depends_on=[], capability="monitor", domain="west"),
            DagNodeDef(node_id="cc", type="report", depends_on=["aa", "bb"], capability="report", domain="global"),
        ]
        scheduler = self._make_scheduler()
        order = scheduler._topological_sort(nodes)
        ids = [n.node_id for n in order]
        assert ids[0] in ("aa", "bb")
        assert ids[1] in ("aa", "bb")
        assert ids[2] == "cc"

    def test_cycle_detection(self):
        """AA → BB → AA should raise ValueError."""
        nodes = [
            DagNodeDef(node_id="aa", type="monitor", depends_on=["bb"], capability="monitor", domain="east"),
            DagNodeDef(node_id="bb", type="diagnose", depends_on=["aa"], capability="diagnose", domain="global"),
        ]
        scheduler = self._make_scheduler()
        with pytest.raises(ValueError, match="cycle"):
            scheduler._topological_sort(nodes)

    def test_single_node_dag(self):
        """Single node DAG should return [aa]."""
        nodes = [
            DagNodeDef(node_id="aa", type="monitor", depends_on=[], capability="monitor", domain="east"),
        ]
        scheduler = self._make_scheduler()
        order = scheduler._topological_sort(nodes)
        ids = [n.node_id for n in order]
        assert ids == ["aa"]
