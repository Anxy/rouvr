"""Focused tests for session_factory workhub_binding (lane-ops #23).

Hermetic: fake supported-transport reads only. No network, no database.
"""
import pytest

from backend.session_factory.activation import WORKHUB_BINDING_VERSION
from backend.session_factory.workhub_binding import (
    BindingRefused,
    build_binding,
    build_verifier,
    verify_binding,
)


def selection_block(**overrides):
    values = dict(
        tenant_id="t",
        project_id="lane-ops",
        task_id="task-1",
        task_number=23,
        objective_id="o",
        cycle_id="c",
        selection_id="s",
        mandate_id="m",
        session_id="session",
        worker_id="worker",
        candidate_sha="0123456789abcdef",
        plan_sha256="a" * 64,
        correlation_id="correlation-1",
    )
    values.update(overrides)
    return values


def good_binding(**overrides):
    values = dict(
        schema_version=WORKHUB_BINDING_VERSION,
        tenant_id="t",
        project_id="lane-ops",
        task_id="task-1",
        task_number=23,
        assignment_id="assign-1",
        assignment_revision=2,
        executor_sub="worker",
        workhub_attempt_id="attempt-1",
        attempt_version=1,
        controller_attempt_id="canary-23-a1",
        lease_epoch=5,
        lease_holder="worker",
        contract_ref="workhub://tasks/23/execution-contracts/1",
        authority_ref="route-1",
        plan_digest="p" * 64,
        route_digest="r" * 64,
        selection=selection_block(),
    )
    values.update(overrides)
    return values


class FakeTransport:
    def __init__(self, task=None, binding=None, rows=None):
        self._task = task
        self._binding = binding
        self._rows = rows if rows is not None else []

    async def get_task_by_number(self, *, project_id, task_number):
        return self._task

    async def get_assignment_binding(self, *, project_id, task_number):
        return self._binding

    async def list_task_evidence(self, *, project_id, task_id):
        return list(self._rows)


def live_task(**overrides):
    values = dict(
        id="task-1",
        task_number=23,
        tenant_id="t",
        project_id="lane-ops",
        status="running",
        active_execution_contract_ref=(
            "workhub://tasks/23/execution-contracts/1"
        ),
    )
    values.update(overrides)
    return values


def live_binding(**overrides):
    values = dict(
        task_id="task-1",
        assignment_id="assign-1",
        assignment_record_version=2,
        assignment_status="active",
        executor_sub="worker",
        authority_ref="route-1",
        acceptance_ref="workhub://tasks/23/execution-contracts/1",
        plan_step="authoritative-route-execution",
        plan_digest="p" * 64,
        lease_epoch=5,
        holding_principal="worker",
    )
    values.update(overrides)
    return values


def fence_rows():
    return [
        {
            "evidence_type": "temporal_handoff",
            "evidence_json": {
                "workhub_attempt_id": "attempt-1",
                "attempt_version": 1,
                "executor_sub": "worker",
                "controller_attempt_id": "canary-23-a1",
                "lease_epoch": 5,
                "effect_fence": {
                    "claim_id": "assign-1",
                    "fence_token": {
                        "assignment_revision": 2,
                        "lease_epoch": 5,
                    },
                    "idempotency_key": "temporal-handoff-canary-23-a1-e5",
                },
            },
        },
        {
            "evidence_type": "execution_spec",
            "evidence_json": {
                "contract_ref": "workhub://tasks/23/execution-contracts/1",
                "aix_selection": selection_block(),
            },
        },
    ]


def transport(**overrides):
    kw = dict(
        task=live_task(), binding=live_binding(), rows=fence_rows(),
    )
    kw.update(overrides)
    return FakeTransport(**kw)


@pytest.mark.asyncio
async def test_verify_accepts_exact_live_binding():
    out = await verify_binding(good_binding(), transport=transport())
    assert out["assignment_id"] == "assign-1"
    assert out["workhub_attempt_id"] == "attempt-1"
    assert out["lease_epoch"] == 5


@pytest.mark.asyncio
async def test_verify_returns_canonical_proven_selection():
    # Lane-ops #23 R2: ingress and activation bind outer fields to this
    # exact dict; it must equal the live-contract execution_spec block.
    out = await verify_binding(good_binding(), transport=transport())
    assert out["selection"] == selection_block()
    assert out["selection"] == fence_rows()[1]["evidence_json"]["aix_selection"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutate",
    [
        {"task": None},
        {"binding": None},
        {"rows": []},
    ],
)
async def test_verify_refuses_missing_reads(mutate):
    with pytest.raises(BindingRefused):
        await verify_binding(good_binding(), transport=transport(**mutate))


@pytest.mark.asyncio
async def test_verify_refuses_moved_assignment_or_lease():
    moved = live_binding(assignment_record_version=3)
    with pytest.raises(BindingRefused, match="BINDING_ASSIGNMENT_MOVED"):
        await verify_binding(good_binding(), transport=transport(binding=moved))
    lapsed = live_binding(lease_epoch=None, holding_principal=None)
    with pytest.raises(BindingRefused, match="BINDING_LEASE_LAPSED"):
        await verify_binding(good_binding(), transport=transport(binding=lapsed))
    foreign = live_binding(executor_sub="intruder")
    with pytest.raises(BindingRefused, match="BINDING_EXECUTOR_MISMATCH"):
        await verify_binding(good_binding(), transport=transport(binding=foreign))


@pytest.mark.asyncio
async def test_verify_refuses_absent_route_fields():
    bare = live_binding()
    del bare["authority_ref"]
    with pytest.raises(BindingRefused, match="BINDING_ROUTE_FIELD_ABSENT"):
        await verify_binding(good_binding(), transport=transport(binding=bare))


@pytest.mark.asyncio
async def test_verify_refuses_unproven_attempt():
    rows = [{"evidence_type": "note", "evidence_json": {}}]
    with pytest.raises(BindingRefused, match="BINDING_ATTEMPT_UNPROVEN"):
        await verify_binding(good_binding(), transport=transport(rows=rows))


@pytest.mark.asyncio
async def test_verify_refuses_moved_contract():
    task = live_task(
        active_execution_contract_ref="workhub://tasks/23/execution-contracts/2"
    )
    with pytest.raises(BindingRefused, match="BINDING_CONTRACT_MOVED"):
        await verify_binding(good_binding(), transport=transport(task=task))


def test_build_binding_carries_closed_field_set():
    built = build_binding(
        tenant_id="t", project_id="lane-ops", task_id="task-1",
        task_number=23, assignment_id="assign-1", assignment_revision=2,
        executor_sub="worker", workhub_attempt_id="attempt-1",
        attempt_version=1, controller_attempt_id="canary-23-a1",
        lease_epoch=5, lease_holder="worker",
        contract_ref="workhub://tasks/23/execution-contracts/1",
        authority_ref="route-1", plan_digest="p" * 64, route_digest="r" * 64,
        selection=selection_block(),
    )
    assert built["schema_version"] == WORKHUB_BINDING_VERSION
    assert set(built) == {
        "schema_version", "tenant_id", "project_id", "task_id",
        "task_number", "assignment_id", "assignment_revision",
        "executor_sub", "workhub_attempt_id", "attempt_version",
        "controller_attempt_id", "lease_epoch", "lease_holder",
        "contract_ref", "authority_ref", "plan_digest", "route_digest",
        "selection",
    }


@pytest.mark.asyncio
async def test_build_verifier_wraps_transport():
    verifier = build_verifier(transport())
    out = await verifier(good_binding())
    assert out["lease_epoch"] == 5


def test_required_key_set_matches_activation_contract():
    from backend.session_factory.activation import WORKHUB_BINDING_KEYS
    from backend.session_factory.workhub_binding import (
        _BINDING_REQUIRED_KEYS,
    )

    assert _BINDING_REQUIRED_KEYS == set(WORKHUB_BINDING_KEYS)


@pytest.mark.asyncio
async def test_verify_malformed_binding_refuses_without_500():
    for bad in ({}, {"schema_version": 1}, {"schema_version": 2},
                dict(good_binding(), selection=None),
                dict(good_binding(), lease_epoch="five")):
        with pytest.raises(BindingRefused):
            await verify_binding(bad, transport=transport())


@pytest.mark.asyncio
async def test_verify_refuses_fabricated_attempt_identity_and_version():
    forged = good_binding(
        workhub_attempt_id="NONEXISTENT-ATTEMPT", attempt_version=987)
    with pytest.raises(BindingRefused, match="BINDING_ATTEMPT_UNPROVEN"):
        await verify_binding(forged, transport=transport())


@pytest.mark.asyncio
async def test_verify_refuses_substring_key_and_tampered_selection():
    rows = [dict(row) for row in fence_rows()]
    rows[0]["evidence_json"]["effect_fence"]["idempotency_key"] = (
        "prefix-temporal-handoff-canary-23-a1-e5-suffix")
    with pytest.raises(BindingRefused, match="BINDING_ATTEMPT_UNPROVEN"):
        await verify_binding(good_binding(), transport=transport(rows=rows))
    tampered = good_binding()
    tampered["selection"] = selection_block(candidate_sha="deadbeef")
    with pytest.raises(BindingRefused, match="BINDING_SELECTION_UNPROVEN"):
        await verify_binding(tampered, transport=transport())
