"""Remote-call helper that depends on the loader, not the decoy config module."""

from __future__ import annotations

from backend.tests.runtime.objective_cycle.live_fixtures.timeout_loader import (
    get_request_timeout_seconds,
)


def remote_call_timeout() -> int:
    """Timeout used for the live-fixture remote call path."""
    return get_request_timeout_seconds()
