from __future__ import annotations

import ast
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.workflows.temporal.objective_cycle_contracts import (
    OBJECTIVE_CYCLE_ACTIVITY_CONTRACTS,
    ActivityOutcome,
    ObjectiveCycleActivityInput,
    ObjectiveCycleActivityResult,
    ObjectiveCycleWorkflowInput,
    build_activity_input,
    objective_cycle_activity_id,
    objective_cycle_workflow_id,
    validate_activity_result_authority,
)

HASH = "a" * 64


def _workflow_input() -> ObjectiveCycleWorkflowInput:
    return ObjectiveCycleWorkflowInput(
        workflow_version=1,
        tenant_id="tenant-1",
        objective_id="objective-1",
        cycle_id="cycle-1",
        candidate_sha="b" * 40,
        plan_sha256=HASH,
        start_request_id="request-1",
        correlation_id="correlation-1",
    )


def test_workflow_and_activity_ids_are_deterministic_and_scope_bound() -> None:
    left = objective_cycle_workflow_id(tenant_id="tenant-1", objective_id="objective-1", cycle_id="cycle-1")
    right = objective_cycle_workflow_id(tenant_id="tenant-1", objective_id="objective-1", cycle_id="cycle-1")

    assert left == right
    assert left != objective_cycle_workflow_id(
        tenant_id="tenant-2", objective_id="objective-1", cycle_id="cycle-1"
    )
    assert objective_cycle_activity_id(
        cycle_id="cycle-1", activity_name="dispatch_step", step_id="step-1", attempt_id="attempt-1"
    ) != objective_cycle_activity_id(
        cycle_id="cycle-1", activity_name="dispatch_step", step_id="step-1", attempt_id="attempt-2"
    )


def test_activity_input_uses_durable_refs_and_idempotent_identity() -> None:
    activity = build_activity_input(
        workflow_input=_workflow_input(),
        activity_name="dispatch_step",
        aggregate_version=7,
        causation_id="event-1",
        step_id="step-1",
        attempt_id="attempt-1",
        payload_refs={"step_contract": "postgres://cycle-1/steps/step-1"},
    )

    assert activity.idempotency_key == activity.activity_id
    assert activity.aggregate_version == 7
    assert activity.payload_refs["step_contract"].startswith("postgres://")


@pytest.mark.parametrize("key", ["secret", "api_key", "provider_credential", "workhub_status", "auto_accept", "caller_authorization", "authorization"])
def test_activity_input_rejects_secret_provider_and_lifecycle_payloads(key: str) -> None:
    with pytest.raises(ValidationError, match="TEMPORAL_FORBIDDEN_PAYLOAD_KEYS"):
        ObjectiveCycleActivityInput(
            activity_name="dispatch_step",
            activity_id="activity-1",
            tenant_id="tenant-1",
            objective_id="objective-1",
            cycle_id="cycle-1",
            aggregate_version=1,
            idempotency_key="key-1",
            correlation_id="correlation-1",
            causation_id="event-1",
            payload_refs={key: "forbidden"},
        )


def test_unregistered_activity_fails_closed() -> None:
    with pytest.raises(ValueError, match="TEMPORAL_ACTIVITY_NOT_REGISTERED"):
        build_activity_input(
            workflow_input=_workflow_input(),
            activity_name="call_provider_directly",
            aggregate_version=1,
            causation_id="event-1",
            payload_refs={},
        )


def test_all_activity_contracts_are_bounded_and_no_authority_expansion() -> None:
    assert len(OBJECTIVE_CYCLE_ACTIVITY_CONTRACTS) == 7
    for contract in OBJECTIVE_CYCLE_ACTIVITY_CONTRACTS:
        assert contract.maximum_attempts <= 3
        assert contract.idempotency_required is True
        assert contract.may_mutate_workhub_lifecycle is False
        assert contract.direct_provider_route_allowed is False


def test_activity_result_cannot_claim_workhub_lifecycle_state() -> None:
    result = ObjectiveCycleActivityResult(
        activity_id="activity-1",
        outcome=ActivityOutcome.SUCCEEDED,
        aggregate_version=8,
        reason_code="VERIFIED",
        evidence_refs=("evidence://1",),
        next_state="WORKHUB_ACCEPTED",
    )

    with pytest.raises(ValueError, match="TEMPORAL_WORKHUB_LIFECYCLE_STATE_FORBIDDEN"):
        validate_activity_result_authority(result)


def test_gap_terminal_verify_maps_to_succeeded_with_gap_reason() -> None:
    from backend.workflows.temporal.objective_cycle_activities import (
        ActivityOutcome,
        _map_backend_outcome,
    )

    outcome, reason = _map_backend_outcome(
        {"ok": False, "phase": "GAPS_FOUND", "error": {"code": "GAPS_REMAIN"}, "terminal_reason": None}
    )
    assert outcome == ActivityOutcome.SUCCEEDED
    assert "GAP" in reason


def test_nongap_failure_still_maps_to_failed() -> None:
    from backend.workflows.temporal.objective_cycle_activities import (
        ActivityOutcome,
        _map_backend_outcome,
    )

    outcome, _ = _map_backend_outcome(
        {"ok": False, "phase": "STEP_FAILED", "error": {"code": "EXECUTOR_BOOM"}}
    )
    assert outcome == ActivityOutcome.FAILED


def test_contract_module_has_no_random_uuid_clock_or_io_calls() -> None:
    module_path = Path(__file__).resolve().parents[1] / "workflows" / "temporal" / "objective_cycle_contracts.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    forbidden = {"random", "uuid", "open", "sleep", "now", "utcnow"}
    called = {
        node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, (ast.Attribute, ast.Name))
    }

    assert not called & forbidden
