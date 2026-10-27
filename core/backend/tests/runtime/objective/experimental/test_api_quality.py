from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from backend.aix_core.runtime.objective.experimental.api_quality import (
    CycleApiContractError,
    IdempotencyGuard,
    PageRequest,
    TenantCursorCodec,
    canonical_idempotency_fingerprint,
    require_version_precondition,
    validate_filters,
)

NOW = datetime(2026, 7, 30, 10, 0, tzinfo=UTC)


def test_page_limit_is_bounded() -> None:
    assert PageRequest().limit == 50
    with pytest.raises(ValidationError):
        PageRequest(limit=101)


def test_tenant_cursor_round_trip() -> None:
    codec = TenantCursorCodec(secret=b"cursor-secret", clock=lambda: NOW)
    cursor = codec.encode(tenant_id="tenant-1", sort_key="created_at", last_id="cycle-9")

    payload = codec.decode(cursor, tenant_id="tenant-1")
    assert payload["last_id"] == "cycle-9"


def test_cursor_rejects_tampering_and_cross_tenant_use() -> None:
    codec = TenantCursorCodec(secret=b"cursor-secret", clock=lambda: NOW)
    cursor = codec.encode(tenant_id="tenant-1", sort_key="created_at", last_id="cycle-9")

    with pytest.raises(CycleApiContractError, match="PAGINATION_CURSOR_INVALID"):
        codec.decode(cursor + "x", tenant_id="tenant-1")
    with pytest.raises(CycleApiContractError, match="PAGINATION_CURSOR_TENANT_MISMATCH"):
        codec.decode(cursor, tenant_id="tenant-2")


def test_cursor_expires() -> None:
    codec = TenantCursorCodec(secret=b"cursor-secret", max_age_seconds=10, clock=lambda: NOW)
    cursor = codec.encode(tenant_id="tenant-1", sort_key="created_at", last_id="cycle-9")
    later = TenantCursorCodec(
        secret=b"cursor-secret",
        max_age_seconds=10,
        clock=lambda: datetime.fromtimestamp(int(NOW.timestamp()) + 11, tz=UTC),
    )

    with pytest.raises(CycleApiContractError, match="PAGINATION_CURSOR_EXPIRED"):
        later.decode(cursor, tenant_id="tenant-1")


def test_idempotency_fingerprint_is_canonical() -> None:
    left = canonical_idempotency_fingerprint(
        method="post", path="/aix/cycles", tenant_id="tenant-1", body={"b": 2, "a": 1}
    )
    right = canonical_idempotency_fingerprint(
        method="POST", path="/aix/cycles", tenant_id="tenant-1", body={"a": 1, "b": 2}
    )

    assert left == right


def test_idempotency_duplicate_returns_original_reference() -> None:
    guard = IdempotencyGuard()
    first = guard.register(
        tenant_id="tenant-1", idempotency_key="key-1", fingerprint="fp-1", result_reference="cycle-1"
    )
    second = guard.register(
        tenant_id="tenant-1", idempotency_key="key-1", fingerprint="fp-1", result_reference="cycle-2"
    )

    assert first == ("cycle-1", False)
    assert second == ("cycle-1", True)


def test_idempotency_key_payload_conflict_is_409() -> None:
    guard = IdempotencyGuard()
    guard.register(tenant_id="tenant-1", idempotency_key="key-1", fingerprint="fp-1", result_reference="cycle-1")

    with pytest.raises(CycleApiContractError, match="IDEMPOTENCY_KEY_PAYLOAD_CONFLICT") as exc:
        guard.register(
            tenant_id="tenant-1", idempotency_key="key-1", fingerprint="fp-2", result_reference="cycle-2"
        )
    assert exc.value.http_status == 409


def test_version_precondition_is_required_and_checked() -> None:
    require_version_precondition(if_match='W/"7"', current_version=7)

    with pytest.raises(CycleApiContractError, match="VERSION_PRECONDITION_REQUIRED") as missing:
        require_version_precondition(if_match=None, current_version=7)
    assert missing.value.http_status == 428
    with pytest.raises(CycleApiContractError, match="VERSION_PRECONDITION_FAILED") as stale:
        require_version_precondition(if_match='"6"', current_version=7)
    assert stale.value.http_status == 412


def test_filters_are_allowlisted() -> None:
    assert validate_filters({"state": "READY", "actor": None}, allowed_fields={"state", "actor"}) == {
        "state": "READY"
    }

    with pytest.raises(CycleApiContractError, match="FILTER_FIELD_FORBIDDEN"):
        validate_filters({"raw_sql": "drop"}, allowed_fields={"state"})
