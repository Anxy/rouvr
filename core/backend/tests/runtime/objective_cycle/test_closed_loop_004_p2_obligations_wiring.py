"""AIX-CLOSED-LOOP-004 (AIX-OHC-002) Packet P2: admission converts objective/
definition_of_done into the durable ObjectiveObligationV1 set, on the real
`DurableObjectiveCycleApiBackend.command()`/`query()` path -- not just the
`derive_obligations()` unit tested in test_obligations.py.
"""
from __future__ import annotations

import pytest

from backend.aix_core.runtime.objective.durable_api_backend import DurableObjectiveCycleApiBackend


@pytest.mark.asyncio
async def test_admit_response_carries_one_obligation_per_definition_of_done_entry() -> None:
    backend = DurableObjectiveCycleApiBackend(mode="inprocess", real_execute_enabled=False, require_workhub_load=False)
    admit = await backend.command(
        operation="admit_objective",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=None,
        body={
            "workhub_task_id": "task-501",
            "objective": "Refactor CronJobController.php",
            "task_class": "bounded_repair",
            "definition_of_done": (
                "Group public cron methods by domain",
                "Extract repeated DB/query boilerplate",
                "Remove unused imports",
                "Preserve public method names",
                "Preserve HTTP/middleware behavior",
            ),
            "repository_id": "hyeve-integration-service",
            "allowed_files": (),
            "project_id": "proj-1",
            "task_number": 501,
        },
        idempotency_key="admit-1",
        expected_version=None,
    )

    assert admit["ok"] is True
    descriptions = [o["description"] for o in admit["obligations"]]
    for expected in (
        "Group public cron methods by domain",
        "Extract repeated DB/query boilerplate",
        "Remove unused imports",
        "Preserve public method names",
        "Preserve HTTP/middleware behavior",
    ):
        assert expected in descriptions
    # Scope facets from the objective may add obligations (additive, not replacing DoD).
    assert len(admit["obligations"]) >= 5
    assert len(admit["obligation_ids"]) == len(admit["obligations"])
    assert len(set(admit["obligation_ids"])) == len(admit["obligations"])
    assert all(o["status"] == "UNPLANNED" for o in admit["obligations"])
    assert all(o["objective_id"] == admit["objective_id"] for o in admit["obligations"])
    assert sum(1 for o in admit["obligations"] if o["source"] == "definition_of_done") >= 5

    fetched = await backend.query(
        operation="get_objective", tenant_id="tenant-1", resource_id=admit["objective_id"], child_id=None, filters={}
    )
    assert fetched["ok"] is True
    assert fetched["item"]["obligation_ids"] == admit["obligation_ids"]


@pytest.mark.asyncio
async def test_admit_with_no_definition_of_done_still_gets_one_obligation() -> None:
    backend = DurableObjectiveCycleApiBackend(mode="inprocess", real_execute_enabled=False, require_workhub_load=False)
    admit = await backend.command(
        operation="admit_objective",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=None,
        body={
            "workhub_task_id": "task-502",
            "objective": "Add SMTP_PASSWORD support",
            "task_class": "bounded_repair",
            "definition_of_done": (),
            "repository_id": "hyeve-integration-service",
            "allowed_files": (),
            "project_id": "proj-1",
            "task_number": 502,
        },
        idempotency_key="admit-2",
        expected_version=None,
    )
    assert admit["ok"] is True
    assert len(admit["obligations"]) >= 1
    assert any(o["source"] == "objective_text" for o in admit["obligations"])
    blob = " ".join(o["description"] for o in admit["obligations"]).casefold()
    assert "smtp" in blob or "password" in blob or "config" in blob


@pytest.mark.asyncio
async def test_envelope_definition_of_done_no_longer_silently_collapses_real_dod(
) -> None:
    """P0 finding #1: `ObjectiveEnvelopeV1.definition_of_done` used to always be the
    raw objective text, even when a real `definition_of_done` was supplied. Fixed
    alongside P2 since it is the same "don't silently replace explicit DoD" defect."""
    backend = DurableObjectiveCycleApiBackend(mode="inprocess", real_execute_enabled=False, require_workhub_load=False)
    admit = await backend.command(
        operation="admit_objective",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=None,
        body={
            "workhub_task_id": "task-503",
            "objective": "Some vague objective text nobody should see reflected below",
            # A task_class already in SUPPORTED_TASK_CLASSES (not "bounded_repair",
            # which only constructs an envelope via the task_family fallback and is not
            # guaranteed to for arbitrary objective text) so this test's own assertion
            # about envelope construction succeeding is not confounded by that mapping.
            "task_class": "docs_only_change",
            "definition_of_done": ("Only this real requirement",),
            "repository_id": "hyeve-integration-service",
            "allowed_files": (),
            "project_id": "proj-1",
            "task_number": 503,
        },
        idempotency_key="admit-3",
        expected_version=None,
    )
    assert admit["ok"] is True
    assert admit["envelope_constructed"] is True
    envelope = backend._envelopes[admit["objective_id"]]
    assert envelope.definition_of_done == "Only this real requirement"
