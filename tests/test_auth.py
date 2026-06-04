"""Tests for authentication and PSK validation."""
import os
import pytest

# Add backend to path
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

# Prevent module-level PSK validation from failing during import.
# The auth module calls _load_psk() at import time which reads IOA_PSK.
os.environ.setdefault("IOA_PSK", "test-key-for-unit-tests-only-do-not-use-in-prod")

from ioa_middleware.auth import _get_psk_unsafe


class TestPSKValidation:
    """Test PSK validation rejects weak/default keys."""

    def test_rejects_missing_psk(self, monkeypatch):
        """Missing PSK should raise RuntimeError."""
        monkeypatch.delenv("IOA_PSK", raising=False)
        with pytest.raises(RuntimeError) as exc:
            _get_psk_unsafe({"auth": {}})
        assert "not configured" in str(exc.value).lower()

    def test_rejects_weak_psk_ioa2026demo(self, monkeypatch):
        """Weak PSK 'ioa2026demo' should raise."""
        monkeypatch.setenv("IOA_PSK", "ioa2026demo")
        with pytest.raises(RuntimeError, match="nsecure"):
            _get_psk_unsafe({})

    def test_rejects_weak_psk_default(self, monkeypatch):
        """Weak PSK 'ioa-dev-only-insecure-key' should raise."""
        monkeypatch.setenv("IOA_PSK", "ioa-dev-only-insecure-key")
        with pytest.raises(RuntimeError, match="nsecure"):
            _get_psk_unsafe({})

    def test_rejects_weak_psk_admin(self, monkeypatch):
        """Weak PSK 'admin' should raise."""
        monkeypatch.setenv("IOA_PSK", "admin")
        with pytest.raises(RuntimeError, match="nsecure"):
            _get_psk_unsafe({})

    def test_accepts_strong_psk(self, monkeypatch):
        """Strong random PSK should be accepted."""
        monkeypatch.setenv("IOA_PSK", "k7Xp2Qv9mN4wR8tY1aL6bJ3cF5hD0eG")
        result = _get_psk_unsafe({})
        assert result == "k7Xp2Qv9mN4wR8tY1aL6bJ3cF5hD0eG"

    def test_accepts_psk_from_config(self, monkeypatch):
        """PSK from config dict should be accepted."""
        monkeypatch.delenv("IOA_PSK", raising=False)
        result = _get_psk_unsafe({
            "auth": {"pre_shared_key": "MyS3cure!Key2024"}
        })
        assert result == "MyS3cure!Key2024"
