import asyncio
from typing import Any

import pytest

from backend.session_factory.activation import (
    ActivationResultEvent,
    MuseActivationCoordinator,
    SelectionEvent,
    VerdictEvent,
)


_CORE_KEYS = (
    "tenant_id", "activation_id", "result_id", "worker_id", "status",
    "evidence_ref",
)


def _core(record):
    return {key: record.get(key) for key in _CORE_KEYS}


def _round(record):
    value = record.get("repair_round")
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _binding(record):
    number = record.get("repair_number")
    if isinstance(number, bool) or not isinstance(number, int) or number < 1:
        number = None
    verdict = record.get("repair_verdict_id")
    if not isinstance(verdict, str):
        verdict = ""
    return (number, verdict)


def _repair_release_id(activation_id, repair_number):
    return f"{activation_id}:repair:{int(repair_number)}"


class Store:
    def __init__(self) -> None:
        self.activations: dict[str, dict[str, Any]] = {}
        self.attempt_owners: dict[tuple[str, str, str], str] = {}
        self.verdicts: dict[str, dict[str, Any]] = {}
        self.results: dict[str, dict[str, Any]] = {}
        self.repair_by_verdict: dict[tuple[str, str], int] = {}
        self.repairs: dict[str, int] = {}
        self.releases: dict[str, dict[str, Any]] = {}

    async def claim_activation(self, key, record):
        if key in self.activations:
            return False, dict(self.activations[key])
        self.activations[key] = dict(record)
        return True, dict(record)

    async def get_activation(self, key):
        row = self.activations.get(key)
        return dict(row) if row else None

    async def get_repair_count(self, key):
        return int(self.repairs.get(key, 0))

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
        if repair_round is None:
            current = self.activations[key].get("status")
            if current == "STARTED":
                self.activations[key]["status"] = status
            elif current != status:
                raise ValueError("ACTIVATION_RESULT_REPLAY_CONFLICT")
            return
        if int(repair_round) != int(self.repairs.get(key, 0)):
            raise ValueError("ACTIVATION_RESULT_REPLAY_CONFLICT")
        current = self.activations[key].get("status")
        if current == "STARTED" or current != status:
            self.activations[key]["status"] = status

    async def claim_verdict(self, key, record):
        if key in self.verdicts:
            return False, dict(self.verdicts[key])
        self.verdicts[key] = dict(record)
        return True, dict(record)

    async def claim_result(self, key, record):
        if key in self.results:
            stored = dict(self.results[key])
            if _core(stored) != _core(record):
                raise ValueError("ACTIVATION_RESULT_REPLAY_CONFLICT")
            return False, stored
        current_round = int(self.repairs.get(record.get("activation_id"), 0))
        incoming_round = _round(record)
        if incoming_round is None:
            incoming_round = current_round
            record = {**record, "repair_round": incoming_round}
        if int(incoming_round) != int(current_round):
            raise ValueError("ACTIVATION_RESULT_REPLAY_CONFLICT")
        rows = [
            row for row in self.results.values()
            if row.get("activation_id") == record.get("activation_id")
        ]
        legacy = [row for row in rows if _round(row) is None]
        if legacy:
            if (len(legacy) > 1 or any(
                    _core(row) != _core(legacy[0]) for row in legacy)):
                raise ValueError("ACTIVATION_RESULT_REPLAY_CONFLICT")
            if int(incoming_round) == 0:
                raise ValueError("ACTIVATION_RESULT_REPLAY_CONFLICT")
        for row in rows:
            if _round(row) is None:
                continue
            if int(_round(row)) != int(incoming_round):
                continue
            if _core(row) != _core(record):
                raise ValueError("ACTIVATION_RESULT_REPLAY_CONFLICT")
        self.results[key] = dict(record)
        return True, dict(record)

    async def get_result(self, key):
        row = self.results.get(key)
        return dict(row) if row else None

    def _check_binding(self, activation_id, record, incoming_round):
        """Mirror of the maintained store's atomic binding verification."""
        number, verdict_id = _binding(record)
        if int(incoming_round) == 0:
            if number is not None or verdict_id:
                raise ValueError(
                    "ACTIVATION_RESULT_REPLAY_CONFLICT:REPAIR_BINDING_INVALID"
                )
            return
        if number is None or int(number) != int(incoming_round):
            raise ValueError(
                "ACTIVATION_RESULT_REPLAY_CONFLICT:REPAIR_BINDING_INVALID"
            )
        if not verdict_id:
            raise ValueError(
                "ACTIVATION_RESULT_REPLAY_CONFLICT:REPAIR_BINDING_MISSING"
            )
        allocated = self.repair_by_verdict.get((activation_id, verdict_id))
        verdict = self.verdicts.get(verdict_id)
        if verdict is None:
            raise ValueError(
                "ACTIVATION_RESULT_REPLAY_CONFLICT:REPAIR_BINDING_MISSING"
            )
        if (
            allocated is None
            or int(allocated) != int(incoming_round)
            or verdict.get("activation_id") != activation_id
            or verdict.get("outcome") != "fail"
        ):
            raise ValueError(
                "ACTIVATION_RESULT_REPLAY_CONFLICT:REPAIR_BINDING_STALE"
            )
        release = self.releases.get(
            _repair_release_id(activation_id, int(incoming_round)))
        if release is None:
            raise ValueError(
                "ACTIVATION_RESULT_REPLAY_CONFLICT:REPAIR_BINDING_MISSING"
            )
        if release.get("state") != "completed":
            raise ValueError(
                "ACTIVATION_RESULT_REPLAY_CONFLICT:"
                "REPAIR_BINDING_RELEASE_PENDING"
            )

    async def claim_result_with_mark(self, key, record):
        """Atomic in-memory mirror: binding check, contender fence,
        insert, and fenced terminal mark under one logical lock."""
        if record.get("status") not in ("SUCCEEDED", "FAILED"):
            raise ValueError("ACTIVATION_RESULT_STATUS_INVALID")
        activation_id = record.get("activation_id")
        current_round = int(self.repairs.get(activation_id, 0))
        incoming_round = _round(record)
        if incoming_round is None:
            incoming_round = current_round
            record = {**record, "repair_round": incoming_round}
        if int(incoming_round) != int(current_round):
            raise ValueError(
                "ACTIVATION_RESULT_REPLAY_CONFLICT:REPAIR_BINDING_STALE"
            )
        self._check_binding(activation_id, record, incoming_round)
        rows = [
            row for row in self.results.values()
            if row.get("activation_id") == activation_id
        ]
        legacy = [row for row in rows if _round(row) is None]
        if legacy:
            if (len(legacy) > 1 or any(
                    _core(row) != _core(legacy[0]) for row in legacy)):
                raise ValueError("ACTIVATION_RESULT_REPLAY_CONFLICT")
            if int(incoming_round) == 0 and not any(
                    _round(row) == 0 for row in rows):
                raise ValueError("ACTIVATION_RESULT_REPLAY_CONFLICT")
        for row in rows:
            if _round(row) is None:
                continue
            if int(_round(row)) != int(incoming_round):
                continue
            if _core(row) != _core(record):
                raise ValueError("ACTIVATION_RESULT_REPLAY_CONFLICT")
        if key in self.results:
            stored = dict(self.results[key])
            if _core(stored) != _core(record):
                raise ValueError("ACTIVATION_RESULT_REPLAY_CONFLICT")
            return False, stored
        self.results[key] = dict(record)
        activation = self.activations.get(activation_id)
        if activation is not None and (
                activation.get("status") == "STARTED"
                or record.get("status") != activation.get("status")):
            activation["status"] = record["status"]
        return True, dict(record)

    async def get_result_by_activation(self, activation_id):
        current_round = int(self.repairs.get(activation_id, 0))
        rows = sorted(
            (dict(row) for row in self.results.values()
             if row.get("activation_id") == activation_id),
            key=lambda row: str(row.get("result_id") or ""),
        )
        if not rows:
            return None
        legacy = [row for row in rows if _round(row) is None]
        tagged = [row for row in rows if _round(row) is not None]
        if legacy:
            if (tagged or len(legacy) > 1 or any(
                    _core(row) != _core(legacy[0]) for row in legacy)):
                raise ValueError("ACTIVATION_RESULT_AMBIGUOUS")
            if int(current_round) != 0:
                return None
            return legacy[0]
        by_round: dict[int, list[dict[str, Any]]] = {}
        for row in tagged:
            by_round.setdefault(int(_round(row)), []).append(row)
        for members in by_round.values():
            if any(_core(row) != _core(members[0]) for row in members[1:]):
                raise ValueError("ACTIVATION_RESULT_AMBIGUOUS")
        current = by_round.get(int(current_round))
        if not current:
            return None
        return current[0]

    async def claim_next_repair(self, activation_id, verdict_id):
        binding = (activation_id, verdict_id)
        if binding in self.repair_by_verdict:
            return "allocated", self.repair_by_verdict[binding]
        current = self.repairs.get(activation_id, 0)
        maximum = self.activations[activation_id]["max_repairs"]
        if current >= maximum:
            return "exhausted", None
        number = current + 1
        self.repairs[activation_id] = number
        self.repair_by_verdict[binding] = number
        return "allocated", number

    async def claim_release(self, key, record):
        row = self.releases.get(key)
        if row and row["state"] == "completed":
            return "completed", dict(row), None
        if row and row["state"] == "claimed":
            return "busy", dict(row), None
        row = {**record, "state": "claimed"}
        self.releases[key] = row
        return "acquired", dict(row), key + ":token"

    async def abandon_release(self, key, token):
        self.releases[key]["state"] = "retryable"

    async def mark_release_completed(self, key, token, result):
        self.releases[key].update(result, state="completed")


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


async def _accept_authority(binding):
    return {"verified": True}


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


def verdict(activation_id, **overrides):
    values = dict(
        tenant_id="t",
        activation_id=activation_id,
        verdict_id="v",
        outcome="pass",
        worker_id="worker",
        reviewer_id="reviewer",
        evidence_ref="ev",
        result_id="r",
    )
    values.update(overrides)
    return VerdictEvent(**values)


async def allowed(_payload):
    return True


async def denied(_payload):
    return False


async def noop(_payload):
    return {}


async def activated(coordinator):
    result = await coordinator.accept_selection(selection())
    await coordinator.accept_result(
        ActivationResultEvent(
            tenant_id="t",
            activation_id=result["activation_id"],
            result_id="r",
            worker_id="worker",
            status="SUCCEEDED",
            evidence_ref="ev",
        )
    )
    return result


@pytest.mark.asyncio
async def test_selection_starts_temporal_once_and_exact_duplicate_is_readback():
    store, starts = Store(), []

    async def start(payload):
        starts.append(payload)
        return {"run_id": "r"}

    coordinator = MuseActivationCoordinator(
        store=store, temporal_start=start, release=noop, reviewer_authority=allowed,
        verifier=_accept_authority
    )
    first = await activated(coordinator)
    second = await activated(coordinator)
    assert first["activation_id"] == second["activation_id"]
    assert second["deduplicated"] is True
    assert len(starts) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changed",
    [
        {"mandate_id": "other"},
        {"session_id": "other"},
        {"worker_id": "other"},
        {"candidate_sha": "deadbeef00"},
        {"cycle_id": "other-cycle"},
        {"correlation_id": "other-correlation"},
    ],
)
async def test_outer_selection_drift_refuses_before_any_write(changed):
    # Lane-ops #23 R2: every effect-driving outer field is bound to the
    # canonical verified inner selection at ingress, before ownership or
    # insertion. The refusal precedes the legacy replay comparison and
    # inserts nothing, so the canonical selection still activates exactly
    # once afterwards.
    store, starts = Store(), []

    async def start(payload):
        starts.append(payload)
        return {"run_id": "r"}

    coordinator = MuseActivationCoordinator(
        store=store, temporal_start=start, release=noop, reviewer_authority=allowed,
        verifier=_accept_authority
    )
    first = await activated(coordinator)
    with pytest.raises(ValueError, match="SELECTION_BINDING_MISMATCH"):
        await coordinator.accept_selection(selection(**changed))
    assert len(store.activations) == 1
    assert len(store.attempt_owners) == 1
    canonical = selection()
    expected = await coordinator.accept_selection(canonical)
    assert first["activation_id"] == expected["activation_id"]
    assert first["workflow_id"] == expected["workflow_id"]
    assert len(starts) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changed",
    [
        {"model": "other"},
        {"executor": "other"},
        {"max_repairs": 3},
    ],
)
async def test_authoritative_binding_changes_conflict_without_second_start(changed):
    # Outer fields with no selection counterpart stay covered by the exact
    # replay comparison on the stored record.
    store, starts = Store(), []

    async def start(payload):
        starts.append(payload)
        return {"run_id": "r"}

    coordinator = MuseActivationCoordinator(
        store=store, temporal_start=start, release=noop, reviewer_authority=allowed,
        verifier=_accept_authority
    )
    first = await activated(coordinator)
    with pytest.raises(ValueError, match="ACTIVATION_REPLAY_CONFLICT"):
        await coordinator.accept_selection(selection(**changed))
    canonical = selection()
    expected = await coordinator.accept_selection(canonical)
    assert first["activation_id"] == expected["activation_id"]
    assert first["workflow_id"] == expected["workflow_id"]
    assert len(starts) == 1


@pytest.mark.asyncio
async def test_concurrent_binding_mismatch_cannot_start_second_workflow():
    store, starts, entered, finish = Store(), [], asyncio.Event(), asyncio.Event()

    async def start(payload):
        starts.append(payload)
        entered.set()
        await finish.wait()
        return {"run_id": "r"}

    coordinator = MuseActivationCoordinator(
        store=store, temporal_start=start, release=noop, reviewer_authority=allowed,
        verifier=_accept_authority
    )
    original = asyncio.create_task(activated(coordinator))
    await entered.wait()
    # R2 ingress binding refuses the drifted outer mandate before any
    # ownership or insertion, even mid-start of the canonical selection.
    with pytest.raises(ValueError, match="SELECTION_BINDING_MISMATCH"):
        await coordinator.accept_selection(selection(mandate_id="other"))
    finish.set()
    await original
    assert len(starts) == 1


@pytest.mark.asyncio
async def test_conflicting_stored_duplicate_fails_closed():
    store = Store()
    coordinator = MuseActivationCoordinator(
        store=store, temporal_start=noop, release=noop, reviewer_authority=allowed,
        verifier=_accept_authority
    )
    first = await activated(coordinator)
    store.activations[first["activation_id"]]["mandate_id"] = "substituted"
    with pytest.raises(ValueError, match="ACTIVATION_REPLAY_CONFLICT"):
        await activated(coordinator)


@pytest.mark.asyncio
async def test_release_failure_retries_same_key_then_completes_once():
    store, calls = Store(), []

    async def release(payload):
        calls.append(payload["idempotency_key"])
        if len(calls) == 1:
            raise RuntimeError("network lost")
        return {"unit_id": "next"}

    coordinator = MuseActivationCoordinator(
        store=store, temporal_start=noop, release=release, reviewer_authority=allowed,
        verifier=_accept_authority
    )
    activation_id = (await activated(coordinator))["activation_id"]
    event = verdict(activation_id)
    with pytest.raises(RuntimeError, match="network lost"):
        await coordinator.accept_verdict(event)
    result = await coordinator.accept_verdict(event)
    replay = await coordinator.accept_verdict(event)
    assert result["released"] is True and replay["deduplicated"] is True
    assert calls == [f"{activation_id}:successor"] * 2


@pytest.mark.asyncio
async def test_concurrent_pass_releases_at_most_once():
    store, calls, entered = Store(), [], asyncio.Event()

    async def release(payload):
        calls.append(payload["release_id"])
        entered.set()
        await asyncio.sleep(0.02)
        return {}

    coordinator = MuseActivationCoordinator(
        store=store, temporal_start=noop, release=release, reviewer_authority=allowed,
        verifier=_accept_authority
    )
    activation_id = (await activated(coordinator))["activation_id"]
    a = asyncio.create_task(
        coordinator.accept_verdict(verdict(activation_id, verdict_id="v1"))
    )
    await entered.wait()
    b = await coordinator.accept_verdict(verdict(activation_id, verdict_id="v2"))
    await a
    assert b["release_pending"] is True
    assert calls == [f"{activation_id}:successor"]


@pytest.mark.asyncio
async def test_verdict_requires_activation_tenant_worker_and_authority():
    store = Store()
    coordinator = MuseActivationCoordinator(
        store=store, temporal_start=noop, release=noop, reviewer_authority=allowed,
        verifier=_accept_authority
    )
    activation_id = (await activated(coordinator))["activation_id"]
    with pytest.raises(ValueError, match="UNKNOWN"):
        await coordinator.accept_verdict(verdict("act-unknown"))
    with pytest.raises(ValueError, match="BINDING_MISMATCH"):
        await coordinator.accept_verdict(verdict(activation_id, tenant_id="other"))
    with pytest.raises(ValueError, match="BINDING_MISMATCH"):
        await coordinator.accept_verdict(verdict(activation_id, worker_id="other"))
    unauthorised = MuseActivationCoordinator(
        store=store, temporal_start=noop, release=noop, reviewer_authority=denied,
        verifier=_accept_authority
    )
    with pytest.raises(ValueError, match="UNAUTHORISED"):
        await unauthorised.accept_verdict(verdict(activation_id))
    with pytest.raises(ValueError, match="SELF_REVIEW"):
        await coordinator.accept_verdict(verdict(activation_id, reviewer_id="worker"))


@pytest.mark.asyncio
async def test_conflicting_verdict_replay_and_malformed_ids_fail_closed():
    store = Store()
    coordinator = MuseActivationCoordinator(
        store=store, temporal_start=noop, release=noop, reviewer_authority=allowed,
        verifier=_accept_authority
    )
    activation_id = (await activated(coordinator))["activation_id"]
    await coordinator.accept_verdict(verdict(activation_id, outcome="blocked"))
    with pytest.raises(ValueError, match="REPLAY_CONFLICT"):
        await coordinator.accept_verdict(verdict(activation_id, outcome="pass"))
    with pytest.raises(ValueError, match="EVENT_ID_INVALID"):
        await coordinator.accept_verdict(verdict(activation_id, verdict_id=""))


@pytest.mark.asyncio
async def test_repair_limit_is_immutable_atomic_and_replay_stable():
    store, releases = Store(), []

    async def release(payload):
        releases.append(payload)
        return {}

    coordinator = MuseActivationCoordinator(
        store=store, temporal_start=noop, release=release, reviewer_authority=allowed,
        verifier=_accept_authority
    )
    activation_id = (await coordinator.accept_selection(selection(max_repairs=1)))[
        "activation_id"
    ]
    await coordinator.accept_result(
        ActivationResultEvent("t", activation_id, "r", "worker", "FAILED", "ev")
    )
    one = verdict(activation_id, verdict_id="v1", outcome="fail")
    first, replay = await asyncio.gather(
        coordinator.accept_verdict(one), coordinator.accept_verdict(one)
    )
    second = await coordinator.accept_verdict(
        verdict(activation_id, verdict_id="v2", outcome="fail")
    )
    assert {first["status"], replay["status"]} <= {"RELEASED", "RELEASE_PENDING"}
    assert second["status"] == "BLOCKED"
    assert store.repairs[activation_id] == 1
    assert len(releases) == 1


@pytest.mark.asyncio
async def test_unaccepted_and_malformed_selection_fail_closed():
    coordinator = MuseActivationCoordinator(
        store=Store(), temporal_start=noop, release=noop, reviewer_authority=allowed,
        verifier=_accept_authority
    )
    with pytest.raises(ValueError, match="NOT_ACCEPTED"):
        await coordinator.accept_selection(selection(accepted=False))
    with pytest.raises(ValueError, match="EVENT_ID_INVALID"):
        await coordinator.accept_selection(selection(mandate_id="bad value"))


@pytest.mark.asyncio
async def test_temporal_result_cannot_overwrite_immutable_activation_fields():
    store = Store()

    async def hostile_result(_payload):
        return {
            "activation_id": "attacker",
            "status": "BLOCKED",
            "workflow_id": "other",
        }

    coordinator = MuseActivationCoordinator(
        store=store,
        temporal_start=hostile_result,
        release=noop,
        reviewer_authority=allowed,
        verifier=_accept_authority
    )
    result = await activated(coordinator)
    assert result["activation_id"] != "attacker"
    assert result["status"] == "STARTED"
    assert result["workflow_id"] != "other"
    assert result["temporal_result"] == {
        "activation_id": "attacker",
        "status": "BLOCKED",
        "workflow_id": "other",
    }


def local_coordinator(store, starts):
    async def start(payload):
        starts.append(payload)
        return {"run_id": "r"}

    return MuseActivationCoordinator(
        store=store, temporal_start=start, release=noop,
        reviewer_authority=allowed,
        verifier=_accept_authority
    )


@pytest.mark.asyncio
async def test_selection_without_binding_refuses():
    coordinator = local_coordinator(Store(), [])
    with pytest.raises(ValueError, match="WORKHUB_BINDING_MISSING"):
        await coordinator.accept_selection(selection(workhub_binding={}))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tamper",
    [
        {"authority_ref": ""},
        {"plan_digest": ""},
        {"lease_epoch": 0},
        {"attempt_version": 0},
        {"tenant_id": "foreign"},
        {"workhub_attempt_id": ""},
    ],
)
async def test_selection_with_broken_binding_refuses(tamper):
    bad = binding()
    bad.update(tamper)
    coordinator = local_coordinator(Store(), [])
    with pytest.raises(ValueError, match="WORKHUB_BINDING"):
        await coordinator.accept_selection(selection(workhub_binding=bad))


def reselected_binding(**overrides):
    """A second fully self-consistent event binding (outer == inner)."""
    inner = selection_block(**overrides)
    return binding(selection=inner)


@pytest.mark.asyncio
async def test_same_attempt_second_selection_conflicts():
    store, starts = Store(), []
    coordinator = local_coordinator(store, starts)
    first = await coordinator.accept_selection(selection())
    assert first["status"] == "STARTED"
    # Fully self-consistent rival passes ingress binding, then loses the
    # per-attempt ownership race.
    rival = selection(
        selection_id="s2", workhub_binding=reselected_binding(selection_id="s2")
    )
    with pytest.raises(
        ValueError, match="ACTIVATION_ATTEMPT_BINDING_CONFLICT"
    ):
        await coordinator.accept_selection(rival)


@pytest.mark.asyncio
async def test_same_attempt_exact_replay_deduplicates():
    store, starts = Store(), []
    coordinator = local_coordinator(store, starts)
    first = await coordinator.accept_selection(selection())
    second = await coordinator.accept_selection(selection())
    assert second["deduplicated"] is True
    assert len(starts) == 1


@pytest.mark.asyncio
async def test_conflicting_attempt_inserts_nothing_and_original_recovers():
    store, starts = Store(), []
    coordinator = local_coordinator(store, starts)
    first = await coordinator.accept_selection(selection())
    assert first["status"] == "STARTED"
    rival = selection(
        selection_id="s2", workhub_binding=reselected_binding(selection_id="s2")
    )
    with pytest.raises(
        ValueError, match="ACTIVATION_ATTEMPT_BINDING_CONFLICT"
    ):
        await coordinator.accept_selection(rival)
    assert len(store.activations) == 1
    assert len(starts) == 1
    retry = await coordinator.accept_selection(selection())
    assert retry["deduplicated"] is True
    assert retry["activation_id"] == first["activation_id"]
    assert len(store.activations) == 1
    assert len(starts) == 1


@pytest.mark.asyncio
async def test_authority_reverified_before_temporal_start():
    store, starts, verified = Store(), [], []

    async def start(payload):
        starts.append(payload)
        return {"run_id": "r"}

    async def recording_verifier(binding):
        verified.append(dict(binding))
        return {"verified": True}

    coordinator = MuseActivationCoordinator(
        store=store, temporal_start=start, release=noop,
        reviewer_authority=allowed,
        verifier=recording_verifier
    )
    await coordinator.accept_selection(selection())
    # Lane-ops #23 R2: authority is proven twice — once at ingress before
    # any durable write, and again immediately before the Temporal effect.
    assert len(verified) == 2
    assert verified[0]["workhub_attempt_id"] == "attempt-1"
    assert verified[1]["workhub_attempt_id"] == "attempt-1"
    assert len(starts) == 1


@pytest.mark.asyncio
async def test_failed_reverification_starts_nothing():
    store, starts = Store(), []

    async def start(payload):
        starts.append(payload)
        return {"run_id": "r"}

    async def refusing_verifier(binding):
        raise ValueError("BINDING_LEASE_LAPSED")

    coordinator = MuseActivationCoordinator(
        store=store, temporal_start=start, release=noop,
        reviewer_authority=allowed,
        verifier=refusing_verifier
    )
    with pytest.raises(ValueError, match="BINDING_LEASE_LAPSED"):
        await coordinator.accept_selection(selection())
    assert starts == []


def _repair_result(activation_id, result_id, status="SUCCEEDED", evidence="ev",
                   repair_number=None, repair_verdict_id=""):
    return ActivationResultEvent(
        "t", activation_id, result_id, "worker", status, evidence,
        repair_number=repair_number, repair_verdict_id=repair_verdict_id)


def _bound_result(activation_id, result_id, repair_number, verdict_id,
                  status="SUCCEEDED", evidence="ev"):
    """A next-round result carrying its actual release binding."""
    return _repair_result(activation_id, result_id, status, evidence,
                          repair_number=repair_number,
                          repair_verdict_id=verdict_id)


@pytest.mark.asyncio
async def test_same_round_concurrent_conflict_loser_inserts_nothing():
    """Same-round distinct results race: one recorded, loser refuses pre-insert."""
    import asyncio

    store, starts = Store(), []
    coordinator = local_coordinator(store, starts)
    activation_id = (await coordinator.accept_selection(selection()))[
        "activation_id"]
    outcomes = await asyncio.gather(
        coordinator.accept_result(_repair_result(activation_id, "r1")),
        coordinator.accept_result(
            _repair_result(activation_id, "r2", evidence="ev-2")),
        return_exceptions=True,
    )
    recorded = [out for out in outcomes if not isinstance(out, Exception)]
    refused = [out for out in outcomes if isinstance(out, Exception)]
    assert len(recorded) == 1 and len(refused) == 1
    assert isinstance(refused[0], ValueError)
    assert str(refused[0]) == "ACTIVATION_RESULT_REPLAY_CONFLICT"
    assert len(store.results) == 1
    proof = await store.get_result_by_activation(activation_id)
    assert proof["result_id"] == recorded[0]["result_id"]
    # Store-transaction fence: a late distinct contender for the same
    # round still refuses at claim before inserting.
    with pytest.raises(ValueError, match="ACTIVATION_RESULT_REPLAY_CONFLICT"):
        await store.claim_result("r-late", {
            "tenant_id": "t", "activation_id": activation_id,
            "result_id": "r-late", "worker_id": "worker",
            "status": proof["status"], "evidence_ref": "ev-late",
            "repair_round": 0,
        })
    assert len(store.results) == 1


@pytest.mark.asyncio
async def test_released_repair_publishes_next_round_readably():
    """A genuinely released bounded repair publishes its next result."""
    releases: list[dict] = []
    store, starts = Store(), []

    async def release(payload):
        releases.append(dict(payload))
        return {}

    coordinator = MuseActivationCoordinator(
        store=store, temporal_start=noop, release=release,
        reviewer_authority=allowed, verifier=_accept_authority,
    )
    activation_id = (await coordinator.accept_selection(selection(max_repairs=2)))[
        "activation_id"]
    first = await coordinator.accept_result(_repair_result(activation_id, "r1"))
    assert first["deduplicated"] is False
    assert (await store.get_result_by_activation(activation_id))["result_id"] == "r1"
    await coordinator.accept_verdict(
        verdict(activation_id, verdict_id="v1", outcome="fail", result_id="r1"))
    assert await store.get_repair_count(activation_id) == 1
    # Repair authorized but next result not yet filed: current round has
    # no proof (hold), the superseded prior round is never surfaced.
    assert await store.get_result_by_activation(activation_id) is None
    second = await coordinator.accept_result(
        _bound_result(activation_id, "r2", 1, "v1", evidence="ev-2"))
    assert second["deduplicated"] is False
    assert second["repair_round"] == 1
    proof = await store.get_result_by_activation(activation_id)
    assert proof["result_id"] == "r2"
    assert proof["repair_round"] == 1


@pytest.mark.asyncio
async def test_exact_old_replay_after_repair_deduplicates_without_new_round():
    """Exact old-result replay never becomes a new-round result."""
    store, starts = Store(), []
    coordinator = local_coordinator(store, starts)
    activation_id = (await coordinator.accept_selection(selection(max_repairs=2)))[
        "activation_id"]
    await coordinator.accept_result(_repair_result(activation_id, "r1"))
    await coordinator.accept_verdict(
        verdict(activation_id, verdict_id="v1", outcome="fail", result_id="r1"))
    await coordinator.accept_result(
        _bound_result(activation_id, "r2", 1, "v1", evidence="ev-2"))
    replay = await coordinator.accept_result(
        _repair_result(activation_id, "r1"))
    assert replay["deduplicated"] is True
    assert replay["repair_round"] == 0
    assert len(store.results) == 2
    proof = await store.get_result_by_activation(activation_id)
    assert proof["result_id"] == "r2"
    assert proof["repair_round"] == 1


@pytest.mark.asyncio
async def test_repair_exhaustion_refuses_further_distinct_results():
    """Exhausted budget: no new round, further distinct results lose."""
    store, starts = Store(), []
    coordinator = local_coordinator(store, starts)
    activation_id = (await coordinator.accept_selection(selection(max_repairs=1)))[
        "activation_id"]
    await coordinator.accept_result(_repair_result(activation_id, "r1"))
    await coordinator.accept_verdict(
        verdict(activation_id, verdict_id="v1", outcome="fail", result_id="r1"))
    await coordinator.accept_result(
        _bound_result(activation_id, "r2", 1, "v1", evidence="ev-2"))
    blocked = await coordinator.accept_verdict(
        verdict(activation_id, verdict_id="v2", outcome="fail", result_id="r2",
                evidence_ref="ev-2"))
    assert blocked["status"] == "BLOCKED"
    with pytest.raises(ValueError, match="ACTIVATION_RESULT_REPLAY_CONFLICT"):
        await coordinator.accept_result(
            _repair_result(activation_id, "r3", evidence="ev-3"))
    assert len(store.results) == 2
    proof = await store.get_result_by_activation(activation_id)
    assert proof["result_id"] == "r2"


@pytest.mark.asyncio
async def test_stale_mark_cannot_overwrite_newly_authorized_round():
    """A delayed old-round mark refuses instead of overwriting the new round."""
    store, starts = Store(), []
    coordinator = local_coordinator(store, starts)
    activation_id = (await coordinator.accept_selection(selection(max_repairs=2)))[
        "activation_id"]
    await coordinator.accept_result(
        _repair_result(activation_id, "r1", status="SUCCEEDED"))
    await coordinator.accept_verdict(
        verdict(activation_id, verdict_id="v1", outcome="fail", result_id="r1"))
    await coordinator.accept_result(
        _bound_result(activation_id, "r2", 1, "v1", status="FAILED",
                      evidence="ev-2"))
    assert (await store.get_activation(activation_id))["status"] == "FAILED"
    with pytest.raises(ValueError, match="ACTIVATION_RESULT_REPLAY_CONFLICT"):
        await store.mark_activation_result(activation_id, "SUCCEEDED", 0)
    assert (await store.get_activation(activation_id))["status"] == "FAILED"
    assert (await store.get_result_by_activation(activation_id))["result_id"] == "r2"


@pytest.mark.asyncio
async def test_legacy_distinct_rows_refuse_ambiguous_never_latest():
    """Legacy rows without a round owner never resolve to an arbitrary row."""
    store, starts = Store(), []
    coordinator = local_coordinator(store, starts)
    activation_id = (await coordinator.accept_selection(selection()))[
        "activation_id"]
    store.results["r-legacy-1"] = {
        "tenant_id": "t", "activation_id": activation_id,
        "result_id": "r-legacy-1", "worker_id": "worker",
        "status": "SUCCEEDED", "evidence_ref": "ev-1",
    }
    store.results["r-legacy-2"] = {
        "tenant_id": "t", "activation_id": activation_id,
        "result_id": "r-legacy-2", "worker_id": "worker",
        "status": "SUCCEEDED", "evidence_ref": "ev-2",
    }
    with pytest.raises(ValueError, match="ACTIVATION_RESULT_AMBIGUOUS"):
        await store.get_result_by_activation(activation_id)


@pytest.mark.asyncio
async def test_tagged_same_round_distinct_rows_refuse_ambiguous():
    """Distinct tagged rows for one round never resolve to a latest row."""
    store, starts = Store(), []
    coordinator = local_coordinator(store, starts)
    activation_id = (await coordinator.accept_selection(selection()))[
        "activation_id"]
    store.results["r-a"] = {
        "tenant_id": "t", "activation_id": activation_id,
        "result_id": "r-a", "worker_id": "worker",
        "status": "SUCCEEDED", "evidence_ref": "ev-a",
        "repair_round": 0,
    }
    store.results["r-b"] = {
        "tenant_id": "t", "activation_id": activation_id,
        "result_id": "r-b", "worker_id": "worker",
        "status": "SUCCEEDED", "evidence_ref": "ev-b",
        "repair_round": 0,
    }
    with pytest.raises(ValueError, match="ACTIVATION_RESULT_AMBIGUOUS"):
        await store.get_result_by_activation(activation_id)


@pytest.mark.asyncio
async def test_crash_between_result_claim_and_mark_recovers_on_replay():
    """Injected crash boundary: claimed result + STARTED recovers terminal."""
    store, starts = Store(), []
    coordinator = local_coordinator(store, starts)
    activation_id = (await coordinator.accept_selection(selection()))[
        "activation_id"]
    event = _repair_result(activation_id, "r")
    real = store.claim_result_with_mark

    async def crashing_claim(key, record):
        # Legacy claim commits the row without the terminal mark, then
        # the process dies before marking: the exact reproduced hole.
        await store.claim_result(key, record)
        raise RuntimeError("injected-crash-before-mark")

    store.claim_result_with_mark = crashing_claim
    try:
        with pytest.raises(RuntimeError, match="injected-crash-before-mark"):
            await coordinator.accept_result(event)
    finally:
        store.claim_result_with_mark = real
    assert (await store.get_activation(activation_id))["status"] == "STARTED"
    assert (await store.get_result("r"))["result_id"] == "r"
    replay = await coordinator.accept_result(event)
    assert replay["status"] == "RECORDED"
    assert replay["deduplicated"] is True
    assert (await store.get_activation(activation_id))["status"] == "SUCCEEDED"
    assert (await store.get_result_by_activation(activation_id))["result_id"] == "r"


@pytest.mark.asyncio
async def test_old_replay_after_new_round_leaves_new_round_terminal():
    """Exact old replay after a new round stays harmless and terminal."""
    store, starts = Store(), []
    coordinator = local_coordinator(store, starts)
    activation_id = (await coordinator.accept_selection(selection(max_repairs=2)))[
        "activation_id"]
    await coordinator.accept_result(_repair_result(activation_id, "r1"))
    await coordinator.accept_verdict(
        verdict(activation_id, verdict_id="v1", outcome="fail", result_id="r1"))
    await coordinator.accept_result(
        _bound_result(activation_id, "r2", 1, "v1", status="FAILED",
                      evidence="ev-2"))
    assert (await store.get_activation(activation_id))["status"] == "FAILED"
    replay = await coordinator.accept_result(
        _repair_result(activation_id, "r1"))
    assert replay["deduplicated"] is True
    assert (await store.get_activation(activation_id))["status"] == "FAILED"
    proof = await store.get_result_by_activation(activation_id)
    assert proof["result_id"] == "r2"
    assert proof["repair_round"] == 1


@pytest.mark.asyncio
async def test_delayed_unseen_old_result_refuses_after_repair():
    """A delayed distinct old result never captures the next round."""
    store, starts = Store(), []
    coordinator = local_coordinator(store, starts)
    activation_id = (await coordinator.accept_selection(selection(max_repairs=2)))[
        "activation_id"]
    await coordinator.accept_result(_repair_result(activation_id, "r1"))
    await coordinator.accept_verdict(
        verdict(activation_id, verdict_id="v1", outcome="fail", result_id="r1"))
    assert await store.get_repair_count(activation_id) == 1
    with pytest.raises(ValueError, match="ACTIVATION_RESULT_REPLAY_CONFLICT"):
        await coordinator.accept_result(
            _repair_result(activation_id, "r-late", evidence="ev-late"))
    assert len(store.results) == 1
    assert await store.get_result_by_activation(activation_id) is None
    assert (await store.get_activation(activation_id))["status"] == "SUCCEEDED"


@pytest.mark.asyncio
async def test_missing_stale_future_repair_bindings_refuse():
    """Unknown, stale, and future repair bindings refuse before insertion."""
    store, starts = Store(), []
    coordinator = local_coordinator(store, starts)
    activation_id = (await coordinator.accept_selection(selection(max_repairs=3)))[
        "activation_id"]
    await coordinator.accept_result(_repair_result(activation_id, "r1"))
    await coordinator.accept_verdict(
        verdict(activation_id, verdict_id="v1", outcome="fail", result_id="r1"))
    assert await store.get_repair_count(activation_id) == 1
    with pytest.raises(ValueError, match="REPAIR_BINDING_MISSING"):
        await coordinator.accept_result(
            _bound_result(activation_id, "r-x", 1, "v-missing", evidence="ev-x"))
    with pytest.raises(ValueError, match="REPAIR_BINDING"):
        await coordinator.accept_result(
            _bound_result(activation_id, "r-y", 2, "v1", evidence="ev-y"))
    await coordinator.accept_result(
        _bound_result(activation_id, "r2", 1, "v1", evidence="ev-2"))
    await coordinator.accept_verdict(
        verdict(activation_id, verdict_id="v2", outcome="fail", result_id="r2",
                evidence_ref="ev-2"))
    assert await store.get_repair_count(activation_id) == 2
    with pytest.raises(ValueError, match="REPAIR_BINDING_STALE"):
        await coordinator.accept_result(
            _bound_result(activation_id, "r-stale", 1, "v1", evidence="ev-s"))
    assert len(store.results) == 2
    # The authorized round 2 has no result yet: hold, never surface the
    # superseded round-1 proof as current.
    assert await store.get_result_by_activation(activation_id) is None


@pytest.mark.asyncio
async def test_failed_release_refuses_bound_result_until_completed():
    """A bound result waits for its repair release: failed refuses first."""
    store, starts = Store(), []
    calls = []

    async def flaky_release(payload):
        calls.append(payload["release_id"])
        if len(calls) == 1:
            raise RuntimeError("release lost")
        return {}

    coordinator = MuseActivationCoordinator(
        store=store, temporal_start=noop, release=flaky_release,
        reviewer_authority=allowed, verifier=_accept_authority,
    )
    activation_id = (await coordinator.accept_selection(selection(max_repairs=2)))[
        "activation_id"]
    await coordinator.accept_result(_repair_result(activation_id, "r1"))
    with pytest.raises(RuntimeError, match="release lost"):
        await coordinator.accept_verdict(
            verdict(activation_id, verdict_id="v1", outcome="fail",
                    result_id="r1"))
    assert await store.get_repair_count(activation_id) == 1
    with pytest.raises(ValueError, match="REPAIR_BINDING_RELEASE_PENDING"):
        await coordinator.accept_result(
            _bound_result(activation_id, "r2", 1, "v1", evidence="ev-2"))
    assert len(store.results) == 1
    retried = await coordinator.accept_verdict(
        verdict(activation_id, verdict_id="v1", outcome="fail", result_id="r1"))
    assert retried["status"] == "RELEASED"
    second = await coordinator.accept_result(
        _bound_result(activation_id, "r2", 1, "v1", evidence="ev-2"))
    assert second["deduplicated"] is False
    assert second["repair_round"] == 1


@pytest.mark.asyncio
async def test_pending_release_refuses_bound_result_until_completed():
    """A bound result refuses while its repair release is still pending."""
    import asyncio

    store, starts = Store(), []
    entered, finish = asyncio.Event(), asyncio.Event()

    async def blocking_release(payload):
        entered.set()
        await finish.wait()
        return {}

    coordinator = MuseActivationCoordinator(
        store=store, temporal_start=noop, release=blocking_release,
        reviewer_authority=allowed, verifier=_accept_authority,
    )
    activation_id = (await coordinator.accept_selection(selection(max_repairs=2)))[
        "activation_id"]
    await coordinator.accept_result(_repair_result(activation_id, "r1"))
    pending = asyncio.create_task(coordinator.accept_verdict(
        verdict(activation_id, verdict_id="v1", outcome="fail", result_id="r1")))
    await entered.wait()
    with pytest.raises(ValueError, match="REPAIR_BINDING_RELEASE_PENDING"):
        await coordinator.accept_result(
            _bound_result(activation_id, "r2", 1, "v1", evidence="ev-2"))
    finish.set()
    outcome = await pending
    assert outcome["status"] == "RELEASED"
    second = await coordinator.accept_result(
        _bound_result(activation_id, "r2", 1, "v1", evidence="ev-2"))
    assert second["repair_round"] == 1
    assert (await store.get_result_by_activation(activation_id))["result_id"] == "r2"


@pytest.mark.asyncio
async def test_concurrent_bound_and_delayed_old_results_fence_once():
    """A released repair's bound result wins over a delayed old arrival."""
    import asyncio

    store, starts = Store(), []
    coordinator = local_coordinator(store, starts)
    activation_id = (await coordinator.accept_selection(selection(max_repairs=2)))[
        "activation_id"]
    await coordinator.accept_result(_repair_result(activation_id, "r1"))
    await coordinator.accept_verdict(
        verdict(activation_id, verdict_id="v1", outcome="fail", result_id="r1"))
    outcomes = await asyncio.gather(
        coordinator.accept_result(
            _bound_result(activation_id, "r2", 1, "v1", evidence="ev-2")),
        coordinator.accept_result(
            _repair_result(activation_id, "r-late", evidence="ev-late")),
        return_exceptions=True,
    )
    recorded = [out for out in outcomes if not isinstance(out, Exception)]
    refused = [out for out in outcomes if isinstance(out, Exception)]
    assert len(recorded) == 1 and len(refused) == 1
    assert recorded[0]["result_id"] == "r2"
    assert isinstance(refused[0], ValueError)
    assert len(store.results) == 2
    proof = await store.get_result_by_activation(activation_id)
    assert proof["result_id"] == "r2"
    assert proof["repair_round"] == 1


@pytest.mark.asyncio
async def test_same_round_bound_conflict_loser_inserts_nothing():
    """Two distinct bound results for one repair round fence exactly once."""
    import asyncio

    store, starts = Store(), []
    coordinator = local_coordinator(store, starts)
    activation_id = (await coordinator.accept_selection(selection(max_repairs=2)))[
        "activation_id"]
    await coordinator.accept_result(_repair_result(activation_id, "r1"))
    await coordinator.accept_verdict(
        verdict(activation_id, verdict_id="v1", outcome="fail", result_id="r1"))
    outcomes = await asyncio.gather(
        coordinator.accept_result(
            _bound_result(activation_id, "r2a", 1, "v1", evidence="ev-2a")),
        coordinator.accept_result(
            _bound_result(activation_id, "r2b", 1, "v1", evidence="ev-2b")),
        return_exceptions=True,
    )
    recorded = [out for out in outcomes if not isinstance(out, Exception)]
    refused = [out for out in outcomes if isinstance(out, Exception)]
    assert len(recorded) == 1 and len(refused) == 1
    assert isinstance(refused[0], ValueError)
    assert len(store.results) == 2
    proof = await store.get_result_by_activation(activation_id)
    assert proof["result_id"] == recorded[0]["result_id"]
    assert proof["repair_round"] == 1


@pytest.mark.asyncio
async def test_opposite_status_next_round_moves_terminal():
    """A repair round may report the opposite outcome terminally."""
    store, starts = Store(), []
    coordinator = local_coordinator(store, starts)
    activation_id = (await coordinator.accept_selection(selection(max_repairs=2)))[
        "activation_id"]
    await coordinator.accept_result(
        _repair_result(activation_id, "r1", status="FAILED"))
    assert (await store.get_activation(activation_id))["status"] == "FAILED"
    await coordinator.accept_verdict(
        verdict(activation_id, verdict_id="v1", outcome="fail", result_id="r1"))
    second = await coordinator.accept_result(
        _bound_result(activation_id, "r2", 1, "v1", status="SUCCEEDED",
                      evidence="ev-2"))
    assert second["repair_round"] == 1
    assert (await store.get_activation(activation_id))["status"] == "SUCCEEDED"
    proof = await store.get_result_by_activation(activation_id)
    assert proof["result_id"] == "r2"
    assert proof["status"] == "SUCCEEDED"
