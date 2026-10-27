"""AIX Closed-Loop Execution Plan, Sprint W8: StepAttemptV1 built from RealExecuteResult."""

from __future__ import annotations

from backend.aix_core.runtime.objective.contracts import StepAttemptV1
from backend.aix_core.runtime.executor.real_execute import RealExecuteResult
from backend.aix_core.runtime.objective.step_attempt import build_step_attempt, placeholder_move_token


def _result(**overrides: object) -> RealExecuteResult:
    base = dict(
        ok=True, code="REAL_EXECUTE_OK", workspace="D:/tmp/ws",
        evidence_refs=["file://ws/evidence/execution_report.json"],
        artifacts={"execution_report": "file://ws/evidence/execution_report.json"},
        objective_status="COMPLETED", real_operations=3, target_files_modified=1,
        completion_gates={"diff_exists": True},
    )
    base.update(overrides)
    return RealExecuteResult(**base)  # type: ignore[arg-type]


def test_successful_result_becomes_succeeded_attempt() -> None:
    attempt = build_step_attempt(
        result=_result(), step_id="step-1", tenant_id="tenant-1", attempt_number=1,
        executor_ref="aix.real_execute.v1", command_id="cmd-1", completed_at="2026-08-12T00:00:00Z",
    )
    assert isinstance(attempt, StepAttemptV1)
    assert attempt.outcome == "SUCCEEDED"
    assert attempt.failure_class is None
    assert attempt.retry_eligible is False
    assert attempt.step_id == "step-1"
    assert attempt.artifact_refs[0] == "file://ws/evidence/execution_report.json"
    assert attempt.verification_result_ref == "completion_gates"
    assert attempt.completed_at == "2026-08-12T00:00:00Z"


def test_failed_result_becomes_failed_attempt_with_failure_class() -> None:
    attempt = build_step_attempt(
        result=_result(ok=False, objective_status="BLOCKED", reason_code="WORKSPACE_NOT_BOUND"),
        step_id="step-1", tenant_id="tenant-1", attempt_number=2,
        executor_ref="aix.real_execute.v1", command_id="cmd-2", completed_at="2026-08-12T00:00:00Z",
    )
    assert attempt.outcome == "FAILED"
    assert attempt.failure_class == "WORKSPACE_NOT_BOUND"
    assert attempt.retry_eligible is True


def test_move_token_placeholder_is_deterministic_and_non_empty() -> None:
    a = placeholder_move_token("cmd-1")
    b = placeholder_move_token("cmd-1")
    c = placeholder_move_token("cmd-2")
    assert a == b
    assert a != c
    assert len(a) > 0


def test_read_verification_used_when_no_completion_gates() -> None:
    attempt = build_step_attempt(
        result=_result(completion_gates=None, read_verification={"ok": True}),
        step_id="step-1", tenant_id="tenant-1", attempt_number=1,
        executor_ref="aix.real_execute.v1", command_id="cmd-1", completed_at="2026-08-12T00:00:00Z",
    )
    assert attempt.verification_result_ref == "read_verification"


def test_frozen_attempt_requires_completed_at() -> None:
    """StepAttemptV1's own model_validator requires completed_at for any non-IN_PROGRESS
    outcome -- confirms build_step_attempt always supplies one, never leaves it unset."""
    attempt = build_step_attempt(
        result=_result(), step_id="step-1", tenant_id="tenant-1", attempt_number=1,
        executor_ref="aix.real_execute.v1", command_id="cmd-1", completed_at="2026-08-12T00:00:00Z",
    )
    assert attempt.completed_at is not None
