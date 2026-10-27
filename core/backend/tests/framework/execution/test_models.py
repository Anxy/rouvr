from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.aix_core.framework.execution import (
    Attempt,
    ExecutionLane,
    ExecutionPlan,
    Step,
    from_attempt_journal,
    from_objective_envelope,
    from_plan_revision,
    from_step_contract,
)
from backend.aix_core.runtime.objective.contracts import (
    BudgetV1,
    ObjectiveEnvelopeV1,
    PlanRevisionV1,
    StepAttemptV1,
    StepContractV1,
)


def _budget() -> BudgetV1:
    return BudgetV1(
        time_seconds=60,
        tokens=1_000,
        cost_usd=1.0,
        retries=2,
        steps=4,
        concurrency=1,
    )


def _step(
    *,
    step_id: str,
    index: int,
    depends_on: tuple[str, ...] = (),
) -> StepContractV1:
    return StepContractV1(
        id=step_id,
        cycle_id="cyc-1",
        tenant_id="tenant-1",
        step_index=index,
        purpose=f"purpose {step_id}",
        depends_on=depends_on,
        target_scope=(f"src/{step_id}.py",),
        required_context_refs=("ctx-1",),
        expected_output_schema_ref="schema://step-result/v1",
        acceptance_criteria=(f"{step_id} is complete",),
        validation_commands=(f"pytest tests/{step_id}.py",),
        assigned_role="executor",
        risk_tier="LOW",
        approval_required=False,
        step_budget=_budget(),
        expected_checks=("test_suite_passed",),
        required_evidence=("diff", "tests"),
        status="PENDING",
    )


def _plan() -> PlanRevisionV1:
    first = _step(step_id="step-a", index=0)
    second = _step(step_id="step-b", index=1)
    third = _step(step_id="step-c", index=2, depends_on=(first.id, second.id))
    return PlanRevisionV1(
        plan_id="plan-1",
        objective_id="objective-1",
        cycle_id="cyc-1",
        revision=2,
        steps=(first, second, third),
        status="ACCEPTED",
        approval_ref="approval-1",
    )


def test_step_contract_adapter_is_lossless_and_stable() -> None:
    contract = _step(step_id="step-stable", index=3)
    model = from_step_contract(contract)

    assert model.step_id == contract.id
    assert model.index == contract.step_index
    assert model.target_scope == contract.target_scope
    assert model.acceptance_criteria == contract.acceptance_criteria
    assert model.required_evidence == contract.required_evidence
    assert model.source_contract is contract
    assert Step.from_dict(json.loads(json.dumps(model.to_dict()))) == model
    assert from_step_contract(contract).step_id == model.step_id


def test_plan_revision_adapter_builds_deterministic_serial_lanes() -> None:
    plan = _plan()
    model = from_plan_revision(plan)

    assert model.plan_id == plan.plan_id
    assert model.step_ids == ("step-a", "step-b", "step-c")
    assert tuple(lane.step_ids for lane in model.lanes) == (
        ("step-a", "step-b"),
        ("step-c",),
    )
    assert all(lane.concurrency_limit == 1 for lane in model.lanes)
    assert from_plan_revision(plan) == model
    assert ExecutionPlan.from_dict(json.loads(json.dumps(model.to_dict()))) == model


def test_objective_envelope_compatibility_preserves_authority_references() -> None:
    envelope = ObjectiveEnvelopeV1(
        id="objective-1",
        tenant_id="tenant-1",
        actor_ref="user-1",
        project_id="project-1",
        workhub_task_ref="workhub-1",
        task_class="source_edit",
        definition_of_done="change is implemented",
        acceptance_criteria=("tests pass",),
        repository_scope=("repo-1",),
        budget_ceiling=_budget(),
        idempotency_key="command-1",
        correlation_id="correlation-1",
    )

    reference = from_objective_envelope(envelope)

    assert reference.objective_id == envelope.id
    assert reference.workhub_task_ref == envelope.workhub_task_ref
    assert reference.repository_scope == envelope.repository_scope
    assert type(reference).from_dict(reference.to_dict()) == reference


def test_attempt_adapter_preserves_journal_lifecycle_and_evidence() -> None:
    entry = SimpleNamespace(
        tenant_id="tenant-1",
        run_id="cyc-1",
        objective_id="objective-1",
        step_id="step-a",
        attempt_number=1,
        command_id="command-attempt-1",
        attempt_id="attempt-1",
        payload={"evidence_refs": ["evidence://journal"]},
        status="COMMITTED",
        outcome="SUCCEEDED",
        effects_applied=True,
        opened_at="2026-01-01T00:00:00Z",
        committed_at="2026-01-01T00:00:01Z",
    )
    runtime_attempt = StepAttemptV1(
        id="attempt-1",
        step_id="step-a",
        tenant_id="tenant-1",
        attempt_number=1,
        move_token="move-token-1",
        executor_ref="run_real_execute",
        outcome="SUCCEEDED",
        raw_result_ref="result://1",
        artifact_refs=("evidence://artifact",),
        verification_result_ref="evidence://verification",
        completed_at=entry.committed_at,
    )

    model = from_attempt_journal(entry, runtime_attempt)

    assert model.status == "COMMITTED"
    assert model.outcome == "SUCCEEDED"
    assert model.effects_applied is True
    assert model.evidence_refs == (
        "evidence://journal",
        "evidence://artifact",
        "evidence://verification",
    )
    assert Attempt.from_dict(json.loads(json.dumps(model.to_dict()))) == model


@pytest.mark.parametrize(
    "factory",
    [
        lambda: ExecutionLane("lane-1", 0, ("step-1",), concurrency_limit=2),
        lambda: ExecutionPlan(
            "plan-1", "objective-1", "cyc-1", 1, "ACCEPTED",
            (ExecutionLane("lane-1", 0, ("step-1",)),),
            ("step-1", "step-2"), "runtime://plan", None,
        ),
        lambda: Attempt(
            "attempt-1", "step-1", 1, "command-1", "OPEN", "SUCCEEDED",
            True, "2026-01-01T00:00:00Z", None, (), None,
        ),
        lambda: Attempt(
            "attempt-1", "step-1", 1, "command-1", "COMMITTED", "SUCCEEDED",
            False, "2026-01-01T00:00:00Z", "2026-01-01T00:00:01Z", (), None,
        ),
    ],
)
def test_invalid_execution_state_is_rejected(factory: object) -> None:
    with pytest.raises(ValueError):
        factory()  # type: ignore[operator]


def test_models_are_immutable() -> None:
    plan = from_plan_revision(_plan())
    with pytest.raises(FrozenInstanceError):
        plan.revision = 99  # type: ignore[misc]


def test_framework_import_does_not_load_executor_or_backend_or_workhub() -> None:
    service_root = Path(__file__).resolve().parents[4]
    script = f"""
import sys
sys.path.insert(0, {str(service_root)!r})
import backend.aix_core.framework.execution
forbidden = {{
    'backend.aix_core.runtime.executor.real_execute',
    'backend.aix_core.runtime.executor.multi_step_execute',
    'backend.aix_core.runtime.objective.durable_api_backend',
    'backend.aix_core.runtime.objective.mark_workhub_done',
}}
loaded = sorted(forbidden.intersection(sys.modules))
raise SystemExit('unexpected runtime imports: ' + repr(loaded) if loaded else 0)
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=service_root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
