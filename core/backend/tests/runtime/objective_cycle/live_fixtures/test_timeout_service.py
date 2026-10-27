"""Focused tests for the live-fixture timeout behavior (runtime oracle)."""

from __future__ import annotations

from backend.tests.runtime.objective_cycle.live_fixtures.timeout_service import (
    remote_call_timeout,
)


def test_remote_call_timeout_reads_env_override(monkeypatch) -> None:
    monkeypatch.setenv("AIX_LIVE_REQUEST_TIMEOUT_SECONDS", "60")
    assert remote_call_timeout() == 60


def test_remote_call_timeout_default_is_forty_five(monkeypatch) -> None:
    """Product default with no env override must be 45 seconds."""
    monkeypatch.delenv("AIX_LIVE_REQUEST_TIMEOUT_SECONDS", raising=False)
    assert remote_call_timeout() == 45
