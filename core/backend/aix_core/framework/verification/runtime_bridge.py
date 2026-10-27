"""F5 strangler: route completion-gate evaluation through VerificationPipeline."""

from __future__ import annotations

from typing import Any

from backend.aix_core.framework.verification.contract import GateContext, VerificationContract
from backend.aix_core.framework.verification.pipeline import VerificationPipeline
from backend.aix_core.runtime.verification.completion_gates import (
    CompletionGateResult,
    evaluate_completion_gates,
)


def verification_catalog_enabled(settings: object | None = None) -> bool:
    if settings is not None and hasattr(settings, "AIX_FRAMEWORK_VERIFICATION_CATALOG"):
        return bool(getattr(settings, "AIX_FRAMEWORK_VERIFICATION_CATALOG"))
    try:
        from backend.hyeve_aix_service.config import settings as _settings

        return bool(getattr(_settings, "AIX_FRAMEWORK_VERIFICATION_CATALOG", False))
    except Exception:
        return False


def _gate_context_from_kwargs(**kwargs: Any) -> GateContext:
    step = kwargs.get("step_contract")
    if step is not None:
        try:
            contract = VerificationContract.for_step(step)
        except Exception:
            contract = VerificationContract(
                contract_id="gate-ctx-fallback",
                objective_id="",
                cycle_id="",
                step_id="",
                criteria=(),
                expected_checks=(),
                required_evidence=(),
                continuity_required=False,
            )
    else:
        contract = VerificationContract(
            contract_id="gate-ctx-nostep",
            objective_id="",
            cycle_id="",
            step_id="",
            criteria=(),
            expected_checks=(),
            required_evidence=(),
            continuity_required=False,
        )
    refs = kwargs.get("already_satisfied_evidence_refs") or ()
    if not isinstance(refs, tuple):
        refs = tuple(refs)
    return GateContext(
        contract=contract,
        discovery=kwargs.get("discovery"),
        diff_stats=kwargs.get("diff_stats") or {},
        diff_text=kwargs.get("diff_text") or "",
        test_result=kwargs.get("test_result"),
        already_satisfied=bool(kwargs.get("already_satisfied") or False),
        already_satisfied_reason=kwargs.get("already_satisfied_reason") or "",
        already_satisfied_evidence_refs=refs,
        secret_values=kwargs.get("secret_values"),
        diff_only_verification_allowed=bool(kwargs.get("diff_only_verification_allowed") or False),
        state_assertions_passed=kwargs.get("state_assertions_passed", True),
        state_assertion_detail=kwargs.get("state_assertion_detail") or "",
        task_family=kwargs.get("task_family") or "",
        objective=kwargs.get("objective") or "",
        required_effects=kwargs.get("required_effects") or (),
        before_files=kwargs.get("before_files"),
        after_files=kwargs.get("after_files"),
        step_contract=step,
        outcome_contract=kwargs.get("outcome_contract"),
    )


def evaluate_completion_gates_routed(
    *,
    settings: object | None = None,
    use_catalog: bool | None = None,
    **kwargs: Any,
) -> CompletionGateResult:
    """Flag OFF → legacy. Flag ON → pipeline then legacy-shaped result (fail-closed)."""
    enabled = verification_catalog_enabled(settings) if use_catalog is None else bool(use_catalog)
    if not enabled:
        return evaluate_completion_gates(**kwargs)

    ctx = _gate_context_from_kwargs(**kwargs)
    pipeline = VerificationPipeline()
    gate_results = pipeline.evaluate_gates(ctx)
    # Canonical CompletionGateResult from live authorities (adapter re-calls evaluate_completion_gates).
    legacy = pipeline.to_completion_gate_result(gate_results, ctx)
    # Fail-closed only on real mandatory catalog failures — not incomplete-context evaluator crashes.
    mandatory_fail = [
        r
        for r in gate_results
        if r.mandatory and not r.passed and not (r.details or {}).get("exception")
    ]
    if not mandatory_fail:
        return legacy
    extra_reasons = tuple(
        r for gr in mandatory_fail for r in (gr.reasons or (f"{gr.gate_id} failed",))
    )
    merged_reasons = tuple(dict.fromkeys([*legacy.reasons, *extra_reasons]))
    gates = dict(legacy.gates or {})
    for gr in mandatory_fail:
        gates[gr.gate_id] = False
    return CompletionGateResult(
        passed=False,
        reasons=merged_reasons,
        gates=gates,
        changed_source_files=list(legacy.changed_source_files or []),
        run_artifacts=list(legacy.run_artifacts or []),
        verification_verdict=legacy.verification_verdict or "FAILED",
    )


__all__ = [
    "evaluate_completion_gates_routed",
    "verification_catalog_enabled",
]
