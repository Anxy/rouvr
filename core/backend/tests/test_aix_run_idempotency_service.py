from __future__ import annotations

from pathlib import Path

from backend.services.aix_run_idempotency_service import AixRunIdempotencyStore


def test_claim_then_get_then_mark_completed(tmp_path: Path) -> None:
    store = AixRunIdempotencyStore(db_path=tmp_path / "idem.sqlite3")
    inserted, record = store.claim(
        tenant_id="default",
        idempotency_key="idem-1",
        project_id="bh-migrated-7-aix",
        task_id="task-1",
    )
    assert inserted is True
    assert record.status == "in_progress"

    fetched = store.get(tenant_id="default", idempotency_key="idem-1")
    assert fetched is not None
    assert fetched.status == "in_progress"

    payload = {"status": "completed", "run_id": "run-1", "task_id": "task-1"}
    store.mark_completed(
        tenant_id="default",
        idempotency_key="idem-1",
        project_id="bh-migrated-7-aix",
        run_id="run-1",
        task_id="task-1",
        status="completed",
        result_payload=payload,
    )
    done = store.get(tenant_id="default", idempotency_key="idem-1")
    assert done is not None
    assert done.status == "completed"
    assert done.run_id == "run-1"
    assert done.result_payload == payload


def test_claim_existing_key_returns_inserted_false(tmp_path: Path) -> None:
    store = AixRunIdempotencyStore(db_path=tmp_path / "idem.sqlite3")
    first_inserted, _ = store.claim(
        tenant_id="default",
        idempotency_key="idem-2",
        project_id="bh-migrated-7-aix",
        task_id=None,
    )
    second_inserted, second = store.claim(
        tenant_id="default",
        idempotency_key="idem-2",
        project_id="bh-migrated-7-aix",
        task_id=None,
    )
    assert first_inserted is True
    assert second_inserted is False
    assert second.status == "in_progress"
