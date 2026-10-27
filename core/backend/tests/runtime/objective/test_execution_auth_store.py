"""ExecutionAuthStore: durable, restart-safe execution authorization.

No contract-engine dependency; runs in the default suite.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from backend.aix_core.runtime.objective.execution_auth_store import ExecutionAuthStore


def test_seal_and_resolve_roundtrip(tmp_path) -> None:
    store = ExecutionAuthStore(tmp_path / "ws")
    assert store.resolve(tenant_id="t", cycle_id="c") is None
    record = store.seal(tenant_id="t", cycle_id="c", bearer="Bearer abc", actor_id="op")
    assert record is not None
    assert store.resolve(tenant_id="t", cycle_id="c") == "Bearer abc"


def test_resolve_survives_process_restart(tmp_path) -> None:
    base = tmp_path / "ws"
    first = ExecutionAuthStore(base)
    first.seal(tenant_id="t", cycle_id="c", bearer="Bearer abc")
    second = ExecutionAuthStore(base)
    assert second.resolve(tenant_id="t", cycle_id="c") == "Bearer abc"


def test_empty_bearer_never_seals(tmp_path) -> None:
    store = ExecutionAuthStore(tmp_path / "ws")
    assert store.seal(tenant_id="t", cycle_id="c", bearer="  ") is None
    assert store.resolve(tenant_id="t", cycle_id="c") is None


def test_expired_record_fails_closed(tmp_path) -> None:
    store = ExecutionAuthStore(tmp_path / "ws")
    record = store.seal(tenant_id="t", cycle_id="c", bearer="Bearer abc")
    assert record is not None
    path = next((tmp_path / "ws" / ".aix-exec-auth").glob("*.json"))
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["expires_at"] = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    path.write_text(json.dumps(raw), encoding="utf-8")
    fresh = ExecutionAuthStore(tmp_path / "ws")
    assert fresh.resolve(tenant_id="t", cycle_id="c") is None


def test_cross_tenant_isolation(tmp_path) -> None:
    store = ExecutionAuthStore(tmp_path / "ws")
    store.seal(tenant_id="t1", cycle_id="c", bearer="Bearer one")
    assert store.resolve(tenant_id="t2", cycle_id="c") is None
    assert store.resolve(tenant_id="t1", cycle_id="c") == "Bearer one"


def test_revoke_closes_execution(tmp_path) -> None:
    store = ExecutionAuthStore(tmp_path / "ws")
    store.seal(tenant_id="t", cycle_id="c", bearer="Bearer abc")
    store.revoke(tenant_id="t", cycle_id="c")
    assert ExecutionAuthStore(tmp_path / "ws").resolve(tenant_id="t", cycle_id="c") is None
