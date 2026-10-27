"""Lane-ops #23 R2/R5 regression: outer selection binding + terminal state.

Hermetic: in-memory store, fake verifier/Temporal starter. No network,
no database. Covers the independent CHANGES_REQUESTED reproductions:

- approved inner binding + altered outer candidate/cycle must refuse
  before any durable write or Temporal start (R2-selection);
- authority is proven at ingress AND immediately before the Temporal
  effect (R2-selection);
- the recorded result transitions the activation to its terminal
  outcome so supervision observes it (R5-terminal).
"""
from typing import Any

import pytest

from backend.session_factory.activation import (
    SELECTION_BINDING_FIELDS,
    ActivationResultEvent,
    MuseActivationCoordinator,
    SelectionEvent,
)


def _core(record):
    return {
        key: record.get(key) for key in (
            "tenant_id", "activation_id", "result_id", "worker_id", "status",
            "evidence_ref",
        )
    }


def _round(record):
    value = record.get("repair_round")
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


class Store:
    def __init__(self) -> None:
        self.activations: dict[str, dict[str, Any]] = {}
        self.attempt_owners: dict[tuple[str, str, str], str] = {}
        self.results: dict[str, dict[str, Any]] = {}

    async def claim_activation(self, key, record):
        if key in self.activations:
            return False, dict(self.activations[key])
        self.activations[key] = dict(record)
        return True, dict(record)

    async def get_activation(self, key):
        row = self.activations.get(key)
        return dict(row) if row else None

    async def get_repair_count(self, key):
        return 0

    async def find_attempt_activation(self, tenant_id, project_id, attempt_id):
        return sorted(
            key
            for key, row in self.activations.items()
            if isinstance(row.get("workhub_binding"), dict)
            and row["workhub_binding"].get("tenant_id") == tenant_id
            and row["workhub_binding"].get("project_id") == project_id
            and row["workhub_binding"].get("workhub_attempt_id") == attempt_id
        )

    async def claim_attempt_owner(self, tenant_id, project_id, attempt_id,
                                  activation_id):
        key = (tenant_id, project_id, attempt_id)
        owner = self.attempt_owners.setdefault(key, activation_id)
        return (owner == activation_id, owner)

    async def mark_activation_started(self, key, result):
        self.activations[key].update(result, status="STARTED")

    async def mark_activation_result(self, key, status, repair_round=None):
        current = self.activations[key].get("status")
        if current == "STARTED":
            self.activations[key]["status"] = status
        elif current != status:
            raise ValueError("ACTIVATION_RESULT_REPLAY_CONFLICT")

    async def claim_result(self, key, record):
        if key in self.results:
            stored = dict(self.results[key])
            if _core(stored) != _core(record):
                raise ValueError("ACTIVATION_RESULT_REPLAY_CONFLICT")
            return False, stored
        for row in self.results.values():
            if (row.get("activation_id") == record.get("activation_id")
                    and _core(row) != _core(record)):
                raise ValueError("ACTIVATION_RESULT_REPLAY_CONFLICT")
        self.results[key] = dict(record)
        return True, dict(record)

    async def get_result(self, key):
        row = self.results.get(key)
        return dict(row) if row else None

    async def get_result_by_activation(self, activation_id):
        matches = sorted(
            (dict(row) for row in self.results.values()
             if row.get("activation_id") == activation_id),
            key=lambda row: str(row.get("result_id") or ""),
        )
        if not matches:
            return None
        if any(_core(row) != _core(matches[0]) for row in matches[1:]):
            raise ValueError("ACTIVATION_RESULT_AMBIGUOUS")
        return matches[0]

    async def claim_verdict(self, key, record):
        raise AssertionError("not used")

    async def claim_next_repair(self, activation_id, verdict_id):
        raise AssertionError("not used")

    async def claim_release(self, key, record):
        raise AssertionError("not used")

    async def abandon_release(self, key, token):
        raise AssertionError("not used")

    async def mark_release_completed(self, key, token, result):
        raise AssertionError("not used")


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


def binding(**overrides):
    values = dict(
        schema_version=1,
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


def selection(**overrides):
    values = dict(
        tenant_id="t",
        objective_id="o",
        cycle_id="c",
        selection_id="s",
        mandate_id="m",
        session_id="session",
        worker_id="worker",
        accepted=True,
        workflow_version=1,
        candidate_sha="0123456789abcdef",
        plan_sha256="a" * 64,
        correlation_id="correlation-1",
        workhub_binding=binding(),
    )
    values.update(overrides)
    return SelectionEvent(**values)


async def canonical_verifier(approved_binding):
    """Fake verifier returning the canonical proven selection."""
    return {"verified": True, "selection": dict(approved_binding["selection"])}


async def allowed(_payload):
    return True


async def noop(_payload):
    return {}


def coordinator(store, starts, verifier=None):
    async def start(payload):
        starts.append(payload)
        return {"run_id": "r"}

    async def verify(approved):
        if verifier is not None:
            return await verifier(approved)
        return await canonical_verifier(approved)

    return MuseActivationCoordinator(
        store=store, temporal_start=start, release=noop,
        reviewer_authority=allowed, verifier=verify,
    )


@pytest.mark.asyncio
async def test_review_repro_changed_outer_candidate_and_cycle_refuse():
    """Exact R2 repro: approved inner, substituted outer candidate/cycle."""
    store, starts = Store(), []
    subject = coordinator(store, starts)
    tampered = selection(
        candidate_sha="ffffffffffffffffffffffffffffffffffffffff",
        cycle_id="unapproved-cycle",
    )
    with pytest.raises(ValueError, match="SELECTION_BINDING_MISMATCH"):
        await subject.accept_selection(tampered)
    assert starts == []
    assert store.activations == {}
    assert store.attempt_owners == {}
    # The legitimate selection still activates exactly once afterwards.
    first = await subject.accept_selection(selection())
    assert first["status"] == "STARTED"
    assert len(starts) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,drift",
    [
        ("objective_id", "other-objective"),
        ("cycle_id", "other-cycle"),
        ("selection_id", "other-selection"),
        ("mandate_id", "other-mandate"),
        ("session_id", "other-session"),
        ("worker_id", "other-worker"),
        ("candidate_sha", "deadbeef00"),
        ("plan_sha256", "b" * 64),
        ("correlation_id", "other-correlation"),
    ],
)
async def test_every_bound_outer_field_drift_refuses(field, drift):
    assert field in SELECTION_BINDING_FIELDS
    store, starts = Store(), []
    subject = coordinator(store, starts)
    with pytest.raises(ValueError, match="SELECTION_BINDING_MISMATCH"):
        await subject.accept_selection(selection(**{field: drift}))
    assert starts == []
    assert store.activations == {}
    assert store.attempt_owners == {}


@pytest.mark.asyncio
async def test_tenant_drift_refuses_on_binding_shape():
    store, starts = Store(), []
    subject = coordinator(store, starts)
    with pytest.raises(ValueError, match="WORKHUB_BINDING_TENANT_MISMATCH"):
        await subject.accept_selection(selection(tenant_id="foreign"))
    assert starts == []


@pytest.mark.asyncio
async def test_binding_inconsistent_with_proof_refuses():
    """A binding whose inner selection differs from the proof refuses."""

    async def lying_verifier(approved):
        proven = dict(approved["selection"])
        proven["candidate_sha"] = "bbbbbbbb"
        return {"verified": True, "selection": proven}

    store, starts = Store(), []
    subject = coordinator(store, starts, verifier=lying_verifier)
    with pytest.raises(ValueError, match="SELECTION_BINDING_MISMATCH"):
        await subject.accept_selection(selection())
    assert starts == []
    assert store.activations == {}


@pytest.mark.asyncio
async def test_prestart_reverification_refusal_starts_nothing():
    """Lease/contract moved between ingress and start: no effect fires."""
    calls = []

    async def flaky_verifier(approved):
        calls.append(dict(approved))
        if len(calls) == 1:
            return await canonical_verifier(approved)
        raise ValueError("BINDING_LEASE_MOVED")

    store, starts = Store(), []
    subject = coordinator(store, starts, verifier=flaky_verifier)
    with pytest.raises(ValueError, match="BINDING_LEASE_MOVED"):
        await subject.accept_selection(selection())
    assert starts == []
    assert len(calls) == 2
    # The STARTING record stays durable for a retry under a fresh proof.
    assert len(store.activations) == 1
    row = next(iter(store.activations.values()))
    assert row["status"] == "STARTING"


@pytest.mark.asyncio
async def test_result_marks_activation_terminal_and_replay_deduplicates():
    store, starts = Store(), []
    subject = coordinator(store, starts)
    first = await subject.accept_selection(selection())
    activation_id = first["activation_id"]
    out = await subject.accept_result(
        ActivationResultEvent("t", activation_id, "r", "worker",
                              "SUCCEEDED", "ev")
    )
    assert out["status"] == "RECORDED"
    assert (await store.get_activation(activation_id))["status"] == "SUCCEEDED"
    replay = await subject.accept_result(
        ActivationResultEvent("t", activation_id, "r", "worker",
                              "SUCCEEDED", "ev")
    )
    assert replay["status"] == "RECORDED"
    assert replay["deduplicated"] is True
    # Exact selection replay after the terminal result reads back.
    reread = await subject.accept_selection(selection())
    assert reread["deduplicated"] is True
    assert reread["status"] == "SUCCEEDED"
    assert len(starts) == 1


@pytest.mark.asyncio
async def test_conflicting_terminal_result_refuses():
    store, starts = Store(), []
    subject = coordinator(store, starts)
    activation_id = (await subject.accept_selection(selection()))["activation_id"]
    await subject.accept_result(
        ActivationResultEvent("t", activation_id, "r1", "worker",
                              "SUCCEEDED", "ev")
    )
    with pytest.raises(
        ValueError, match="ACTIVATION_RESULT_ACTIVATION_UNKNOWN"
    ):
        await subject.accept_result(
            ActivationResultEvent("t", activation_id, "r2", "worker",
                                  "FAILED", "ev")
        )
    with pytest.raises(
        ValueError, match="ACTIVATION_RESULT_ACTIVATION_UNKNOWN"
    ):
        await subject.accept_result(
            ActivationResultEvent("t", "missing", "r3", "worker",
                                  "SUCCEEDED", "ev")
        )


@pytest.mark.asyncio
async def test_terminal_result_surfaces_by_activation_for_supervision():
    """Lane-ops #23: the claimed result row is the supervision proof."""
    store, starts = Store(), []
    subject = coordinator(store, starts)
    activation_id = (await subject.accept_selection(selection()))["activation_id"]
    assert await store.get_result_by_activation(activation_id) is None
    await subject.accept_result(
        ActivationResultEvent("t", activation_id, "r", "worker",
                              "SUCCEEDED", "ev")
    )
    proof = await store.get_result_by_activation(activation_id)
    assert proof["result_id"] == "r"
    assert proof["activation_id"] == activation_id
    assert proof["worker_id"] == "worker"
    assert proof["status"] == "SUCCEEDED"
    assert proof["evidence_ref"] == "ev"


@pytest.mark.asyncio
async def test_distinct_results_for_one_activation_refuse_ambiguous():
    """Distinct result rows leave no single authoritative proof: hold.

    Such rows can only predate the pre-insert ownership refusal
    (maintained writers refuse the second contender before inserting),
    so the legacy pair is seeded directly; the readback still refuses
    to choose an arbitrary row.
    """
    store, starts = Store(), []
    subject = coordinator(store, starts)
    activation_id = (await subject.accept_selection(selection()))["activation_id"]
    await subject.accept_result(
        ActivationResultEvent("t", activation_id, "r1", "worker",
                              "SUCCEEDED", "ev")
    )
    store.results["r2"] = {
        "tenant_id": "t", "activation_id": activation_id, "result_id": "r2",
        "worker_id": "worker", "status": "SUCCEEDED", "evidence_ref": "ev-2",
    }
    with pytest.raises(ValueError, match="ACTIVATION_RESULT_AMBIGUOUS"):
        await store.get_result_by_activation(activation_id)


@pytest.mark.asyncio
async def test_rejected_conflicting_result_leaves_original_readable():
    """Lane-ops #23: a conflicting contender refuses before inserting,
    so the accepted original stays readable — sequentially and under
    concurrent opposite writers."""
    import asyncio

    store, starts = Store(), []
    subject = coordinator(store, starts)
    activation_id = (await subject.accept_selection(selection()))["activation_id"]
    await subject.accept_result(
        ActivationResultEvent("t", activation_id, "r1", "worker",
                              "SUCCEEDED", "ev")
    )
    with pytest.raises(
        ValueError, match="ACTIVATION_RESULT_REPLAY_CONFLICT"
    ):
        await subject.accept_result(
            ActivationResultEvent("t", activation_id, "r2", "worker",
                                  "SUCCEEDED", "ev-2")
        )
    proof = await store.get_result_by_activation(activation_id)
    assert proof["result_id"] == "r1"
    assert proof["evidence_ref"] == "ev"

    raced, raced_starts = Store(), []
    raced_subject = coordinator(raced, raced_starts)
    raced_id = (await raced_subject.accept_selection(selection()))[
        "activation_id"]
    outcomes = await asyncio.gather(
        raced_subject.accept_result(
            ActivationResultEvent("t", raced_id, "r1", "worker",
                                  "SUCCEEDED", "ev")
        ),
        raced_subject.accept_result(
            ActivationResultEvent("t", raced_id, "r2", "worker",
                                  "FAILED", "ev-2")
        ),
        return_exceptions=True,
    )
    recorded = [out for out in outcomes if not isinstance(out, Exception)]
    refused = [out for out in outcomes if isinstance(out, Exception)]
    assert len(recorded) == 1 and len(refused) == 1
    assert isinstance(refused[0], ValueError)
    # The loser refuses before inserting — at the ownership check
    # when it arrives mid-race, or at the terminal-status check when
    # it arrives after terminality. Either way it inserts nothing.
    assert str(refused[0]) in {
        "ACTIVATION_RESULT_REPLAY_CONFLICT",
        "ACTIVATION_RESULT_ACTIVATION_UNKNOWN",
    }
    assert len(raced.results) == 1
    winner = await raced.get_result_by_activation(raced_id)
    assert winner["result_id"] == recorded[0]["result_id"]
    assert (await raced.get_activation(raced_id))["status"] == (
        winner["status"])
    # Store-transaction race: a distinct contender that passed the
    # read check still refuses at the claim before inserting.
    with pytest.raises(
        ValueError, match="ACTIVATION_RESULT_REPLAY_CONFLICT"
    ):
        await raced.claim_result("r-late", {
            "tenant_id": "t", "activation_id": raced_id,
            "result_id": "r-late", "worker_id": "worker",
            "status": winner["status"], "evidence_ref": "ev-late",
        })
    assert len(raced.results) == 1
    assert (await raced.get_result_by_activation(raced_id)) == winner
