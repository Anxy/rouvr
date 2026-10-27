"""C020 ObjectiveContract + mandatory understanding tests."""

from __future__ import annotations

import pytest

from backend.aix_core.runtime.objective.objective_contract import (
    ObjectiveContractError,
    assert_planning_allowed,
    understand_objective,
    unknowns_were_not_assumed,
)
from backend.aix_core.runtime.objective.durable_api_backend import DurableObjectiveCycleApiBackend


def test_admitted_when_measurable_outcomes_present() -> None:
    c = understand_objective(
        tenant_id="t1",
        raw_objective="Add healthcheck endpoint",
        definition_of_done=["GET /health returns 200"],
    )
    assert c.admission == "ADMITTED"
    assert unknowns_were_not_assumed(c)
    assert_planning_allowed(c)


def test_insufficient_evidence_on_ambiguity() -> None:
    c = understand_objective(
        tenant_id="t1",
        raw_objective="Somehow figure it out, whatever works",
        definition_of_done=["done somehow"],
    )
    assert c.admission == "INSUFFICIENT_EVIDENCE"
    assert c.unknowns
    with pytest.raises(ObjectiveContractError) as exc:
        assert_planning_allowed(c)
    assert exc.value.code == "INSUFFICIENT_EVIDENCE"


def test_unknowns_never_silently_become_assumptions() -> None:
    c = understand_objective(
        tenant_id="t1",
        raw_objective="Assume we use Postgres. Maybe rewrite the API somehow.",
        definition_of_done=["API rewritten"],
    )
    assert any(a.source == "stated" for a in c.assumptions)
    assert any(u.blocks_planning for u in c.unknowns)
    assert unknowns_were_not_assumed(c)
    assert c.admission == "INSUFFICIENT_EVIDENCE"


def test_planning_blocked_without_contract() -> None:
    with pytest.raises(ObjectiveContractError) as exc:
        assert_planning_allowed(None)
    assert exc.value.code == "OBJECTIVE_CONTRACT_REQUIRED"


@pytest.mark.asyncio
async def test_start_cycle_blocked_on_insufficient_evidence() -> None:
    backend = DurableObjectiveCycleApiBackend(mode="inprocess", require_workhub_load=False)
    admit = await backend.command(
        operation="admit_objective",
        tenant_id="tenant-c020",
        actor_id="actor-1",
        resource_id=None,
        body={
            "workhub_task_id": "wh-c020",
            "objective": "figure it out somehow, TBD",
            "task_class": "bounded_repair",
            "definition_of_done": ("something",),
            "repository_id": "repo",
            "allowed_files": (),
        },
        idempotency_key="admit-c020-ambig",
        expected_version=None,
    )
    assert admit.get("ok") is True, admit
    oid = admit["objective_id"]
    contract = backend._objective_contracts[oid]  # noqa: SLF001
    assert contract.admission == "INSUFFICIENT_EVIDENCE"
    started = await backend.command(
        operation="start_cycle",
        tenant_id="tenant-c020",
        actor_id="actor-1",
        resource_id=oid,
        body={"candidate_sha": "c020sha1"},
        idempotency_key="start-c020-ambig",
        expected_version=None,
    )
    assert started["ok"] is False
    assert started["error"]["code"] == "INSUFFICIENT_EVIDENCE"


@pytest.mark.asyncio
async def test_start_cycle_allowed_when_contract_admitted() -> None:
    backend = DurableObjectiveCycleApiBackend(mode="inprocess", require_workhub_load=False)
    admit = await backend.command(
        operation="admit_objective",
        tenant_id="tenant-c020",
        actor_id="actor-1",
        resource_id=None,
        body={
            "workhub_task_id": "wh-c020b",
            "objective": "Add a README section describing restart safety",
            "task_class": "bounded_repair",
            "definition_of_done": ("README has restart safety section",),
            "repository_id": "repo",
            "allowed_files": ("README.md",),
        },
        idempotency_key="admit-c020-ok",
        expected_version=None,
    )
    assert admit.get("ok") is True, admit
    started = await backend.command(
        operation="start_cycle",
        tenant_id="tenant-c020",
        actor_id="actor-1",
        resource_id=admit["objective_id"],
        body={"candidate_sha": "c020sha2"},
        idempotency_key="start-c020-ok",
        expected_version=None,
    )
    assert started.get("ok") is True, started
