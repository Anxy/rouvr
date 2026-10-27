"""FailureEvidenceV1 must stay constructible when RealExecuteError.detail is empty."""

from __future__ import annotations

from backend.aix_core.runtime.objective.contracts import (
    FailureEvidenceV1,
    coerce_executor_reason,
)


def test_coerce_executor_reason_prefers_detail() -> None:
    assert coerce_executor_reason(code="X", detail="  concrete  ") == "concrete"


def test_coerce_executor_reason_falls_back_to_code_when_detail_empty() -> None:
    assert coerce_executor_reason(code="SCOPE_DENIED", detail="") == "SCOPE_DENIED"
    assert coerce_executor_reason(code="SCOPE_DENIED", detail=None) == "SCOPE_DENIED"


def test_failure_evidence_constructs_when_detail_empty() -> None:
    """Regression: empty detail previously raised ValidationError → UNCAUGHT_CONTROL_ERROR."""
    evidence = FailureEvidenceV1(
        step_id="step-1",
        attempt=1,
        failure_signature="sig",
        failure_domain="EXECUTION",
        failure_type="SOME_TYPED_CODE",
        executor_reason=coerce_executor_reason(code="SOME_TYPED_CODE", detail=""),
    )
    assert evidence.executor_reason == "SOME_TYPED_CODE"
