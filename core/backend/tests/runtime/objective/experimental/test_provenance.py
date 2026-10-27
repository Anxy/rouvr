from __future__ import annotations

from datetime import UTC, datetime

import pytest

from backend.aix_core.runtime.objective.experimental.provenance import (
    GENESIS_HASH,
    ContextAuthority,
    ContextProvenanceLedger,
)

NOW = datetime(2026, 7, 29, 12, 0, tzinfo=UTC)


def _ledger() -> ContextProvenanceLedger:
    return ContextProvenanceLedger(clock=lambda: NOW)


def test_provenance_records_source_time_hash_tenant_and_authority() -> None:
    record = _ledger().append(
        item_id="ctx-1",
        tenant_id="tenant-1",
        source_uri="codeintel://snapshot/s-1/backend/policy.py#L1-L10",
        authority=ContextAuthority.CODEINTEL,
        content="source text",
        repository_id="aix",
        commit_sha="a" * 40,
    )

    assert record.observed_at == NOW
    assert record.content_sha256 == "a28130a037ced440a70f1e1e896d9f6cf7147adc31e233e588f58dde12902a2d"
    assert record.previous_record_sha256 == GENESIS_HASH
    assert record.authority is ContextAuthority.CODEINTEL


def test_provenance_hash_chain_verifies() -> None:
    ledger = _ledger()
    first = ledger.append(
        item_id="ctx-1",
        tenant_id="tenant-1",
        source_uri="workhub://tasks/task-1",
        authority=ContextAuthority.WORKHUB,
        content="objective",
    )
    second = ledger.append(
        item_id="ctx-2",
        tenant_id="tenant-1",
        source_uri="human://clarification/answer-1",
        authority=ContextAuthority.HUMAN,
        content="bounded answer",
    )

    assert second.previous_record_sha256 == first.record_sha256
    assert ledger.verify() is True


def test_provenance_records_are_immutable() -> None:
    record = _ledger().append(
        item_id="ctx-1",
        tenant_id="tenant-1",
        source_uri="workhub://tasks/task-1",
        authority=ContextAuthority.WORKHUB,
        content="objective",
    )

    with pytest.raises(Exception):
        record.tenant_id = "other"


def test_provenance_rejects_duplicate_item_per_tenant() -> None:
    ledger = _ledger()
    kwargs = {
        "item_id": "ctx-1",
        "tenant_id": "tenant-1",
        "source_uri": "workhub://tasks/task-1",
        "authority": ContextAuthority.WORKHUB,
        "content": "objective",
    }
    ledger.append(**kwargs)

    with pytest.raises(ValueError, match="DUPLICATE_CONTEXT_ITEM"):
        ledger.append(**kwargs)


def test_provenance_same_item_id_isolated_by_tenant() -> None:
    ledger = _ledger()
    for tenant_id in ("tenant-1", "tenant-2"):
        ledger.append(
            item_id="ctx-1",
            tenant_id=tenant_id,
            source_uri=f"workhub://{tenant_id}/tasks/task-1",
            authority=ContextAuthority.WORKHUB,
            content="objective",
        )

    assert len(ledger.list_for_tenant("tenant-1")) == 1
    assert ledger.list_for_tenant("tenant-1")[0].tenant_id == "tenant-1"


def test_provenance_rejects_naive_timestamp() -> None:
    ledger = ContextProvenanceLedger(clock=lambda: datetime(2026, 7, 29, 12, 0))

    with pytest.raises(ValueError, match="timezone-aware"):
        ledger.append(
            item_id="ctx-1",
            tenant_id="tenant-1",
            source_uri="workhub://tasks/task-1",
            authority=ContextAuthority.WORKHUB,
            content="objective",
        )


def test_provenance_metadata_is_in_record_hash() -> None:
    left = _ledger().append(
        item_id="ctx-1",
        tenant_id="tenant-1",
        source_uri="policy://task-classes/v1",
        authority=ContextAuthority.REPOSITORY_POLICY,
        content="policy",
        metadata={"version": "v1"},
    )
    right = _ledger().append(
        item_id="ctx-1",
        tenant_id="tenant-1",
        source_uri="policy://task-classes/v1",
        authority=ContextAuthority.REPOSITORY_POLICY,
        content="policy",
        metadata={"version": "v2"},
    )

    assert left.record_sha256 != right.record_sha256
