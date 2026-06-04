"""Tests for UCB1 BanditScorer — online learning routing weights."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import pytest
from ioa_middleware.router.bandit_router import BanditScorer, get_bandit


class TestBanditScorer:
    """UCB1 multi-armed bandit — exploration, exploitation, blending."""

    def test_unknown_agent_gets_max_ucb(self):
        """Unknown agent → UCB=1.0 (encourage exploration)."""
        bandit = BanditScorer()
        assert bandit.ucb_score("new-agent") == 1.0

    def test_ucb_prefers_high_reward_agent(self):
        """Agent with higher reward gets higher UCB score (all else equal)."""
        bandit = BanditScorer(c=0.5)
        # Both get same number of trials
        for _ in range(50):
            bandit.record("good", 1.0)
            bandit.record("bad", 0.0)
        assert bandit.ucb_score("good") > bandit.ucb_score("bad")

    def test_ucb_explores_low_trial_agents(self):
        """Agent with fewer trials gets exploration bonus."""
        bandit = BanditScorer(c=1.0)
        # good: many trials, medium reward
        for _ in range(100):
            bandit.record("good", 0.6)
        # new: few trials, same reward → should get higher UCB
        for _ in range(5):
            bandit.record("new", 0.6)

        assert bandit.ucb_score("new") > bandit.ucb_score("good"), \
            "Less-tested agent should get exploration bonus"

    def test_blend_returns_value_between_base_and_ucb(self):
        """Blended score should be between base and UCB."""
        bandit = BanditScorer()
        for _ in range(10):
            bandit.record("agent-x", 1.0)

        base = 0.5
        blended = bandit.blend(base, "agent-x")
        # Since UCB ≈ 1.0 after all-success, blended should be > base
        assert blended >= base
        assert blended <= 1.0

    def test_export_stats_contains_all_fields(self):
        """Export should return structured data."""
        bandit = BanditScorer()
        bandit.record("agent-1", 1.0)
        bandit.record("agent-1", 0.0)

        stats = bandit.export_stats()
        assert "agents" in stats
        assert "total_trials" in stats
        assert stats["total_trials"] == 2
        assert "agent-1" in stats["agents"]
        assert stats["agents"]["agent-1"]["n"] == 2
        assert stats["agents"]["agent-1"]["successes"] == 1
        assert stats["agents"]["agent-1"]["failures"] == 1

    def test_reset_clears_all(self):
        """Reset should clear all statistics on a fresh instance."""
        bandit = BanditScorer()
        bandit.record("agent-1", 1.0)
        assert bandit.export_stats()["total_trials"] == 1
        bandit.reset()
        assert bandit.ucb_score("agent-1") == 1.0  # unknown again
        assert bandit.export_stats()["total_trials"] == 0

    def test_reward_clamped_to_0_1(self):
        """Reward values outside [0,1] should be clamped."""
        bandit = BanditScorer()
        bandit.record("agent-1", 1.5)  # should clamp to 1.0
        bandit.record("agent-1", -0.5)  # should clamp to 0.0

        stats = bandit.export_stats()
        assert stats["agents"]["agent-1"]["mean_reward"] == 0.5

    def test_global_singleton(self):
        """get_bandit() returns the same instance."""
        b1 = get_bandit()
        b2 = get_bandit()
        assert b1 is b2

    def test_convergence_curve(self):
        """Convergence curve returns trial data."""
        bandit = BanditScorer()
        bandit.record("agent-1", 1.0)
        bandit.record("agent-2", 0.0)

        curve = bandit.convergence_curve()
        assert len(curve) == 2
        assert curve[0]["agent_id"] in ("agent-1", "agent-2")
