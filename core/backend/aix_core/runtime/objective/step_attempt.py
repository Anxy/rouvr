"""AIX Closed-Loop Execution Plan, Sprint W8: build a `contracts.StepAttemptV1` from an
already-computed `real_execute.RealExecuteResult`.

Same "reshape existing truth, never recompute it" discipline `verification_engine.py`
established and `understanding_evidence.py`/Sprint W2's envelope builder already follow
in this program -- this module makes zero execution or success decisions of its own; it
only gives `RealExecuteResult`'s already-independently-computed facts a stable, typed,
auditable shape.

One field has no real backing subsystem yet and is explicitly a placeholder, not a
silent invention: `move_token` is specified by the canonical architecture
(`AIX-E2E-CYCLE-001-GOVERNED-OBJECTIVE-CYCLE.md` section 5, packet `001F`, "one Move
Token authorizes one attempt") as a real, separately-issued scoped authorization --
that issuance system does not exist in this codebase yet. A deterministic value derived
from `command_id` is used here so the field is never empty (`StepAttemptV1.move_token`
requires `min_length=1`), but it carries none of the real scoping/single-use semantics
`001F` will eventually require. Do not read a populated `move_token` as proof that
attempt-scoped authorization is enforced -- it is not, yet.
"""

from __future__ import annotations

import hashlib

from backend.aix_core.runtime.objective.contracts import AttemptOutcome, StepAttemptV1, UsageV1
from backend.aix_core.runtime.executor.real_execute import RealExecuteResult

_SUCCESS_OBJECTIVE_STATUSES = frozenset({"COMPLETED"})


def placeholder_move_token(command_id: str) -> str:
    """See module docstring -- not a real 001F Move Token, just a non-empty, stable,
    deterministic stand-in until that issuance system exists."""
    return f"placeholder-move-token:{hashlib.sha256(command_id.encode('utf-8')).hexdigest()[:16]}"


def _classify_outcome(result: RealExecuteResult) -> AttemptOutcome:
    if result.objective_status in _SUCCESS_OBJECTIVE_STATUSES and result.ok:
        return "SUCCEEDED"
    return "FAILED"


def build_step_attempt(
    *,
    result: RealExecuteResult,
    step_id: str,
    tenant_id: str,
    attempt_number: int,
    executor_ref: str,
    command_id: str,
    completed_at: str,
    model_ref: str | None = None,
) -> StepAttemptV1:
    outcome = _classify_outcome(result)
    usage_data = result.token_usage or {}
    usage = UsageV1(
        tokens_in=int(usage_data.get("prompt_tokens") or usage_data.get("tokens_in") or 0),
        tokens_out=int(usage_data.get("completion_tokens") or usage_data.get("tokens_out") or 0),
        cost_usd=float(usage_data.get("cost_usd") or 0.0),
        tool_calls=int(result.real_operations or 0),
        time_seconds=0.0,
    )
    artifact_refs = tuple(result.evidence_refs) + tuple(
        f"artifact://{key}={value}" for key, value in (result.artifacts or {}).items()
    )
    verification_result_ref = None
    if result.completion_gates is not None:
        verification_result_ref = "completion_gates"
    elif result.read_verification is not None:
        verification_result_ref = "read_verification"

    return StepAttemptV1(
        step_id=step_id,
        tenant_id=tenant_id,
        attempt_number=attempt_number,
        move_token=placeholder_move_token(command_id),
        executor_ref=executor_ref,
        model_ref=model_ref,
        outcome=outcome,
        usage=usage,
        raw_result_ref=f"workspace://{result.workspace}" if result.workspace else None,
        artifact_refs=artifact_refs,
        verification_result_ref=verification_result_ref,
        failure_class=None if outcome == "SUCCEEDED" else (result.reason_code or result.code),
        retry_eligible=outcome == "FAILED",
        completed_at=completed_at,
    )


__all__ = ["build_step_attempt", "placeholder_move_token"]
