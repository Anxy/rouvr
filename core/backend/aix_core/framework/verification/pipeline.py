"""AIX Framework Verification Pipeline.

Sprint: F5 Verification Framework (F5-P2)
Orchestrates GateCatalog evaluation and adapts results to CompletionGateResult shape.
"""

from __future__ import annotations

import traceback
from typing import Any

from backend.aix_core.framework.verification.catalog import (
    GateCatalog,
    build_default_catalog,
)
from backend.aix_core.framework.verification.contract import (
    GateContext,
    GateResult,
    VerificationCheck,
    VerificationOutcome,
)
from backend.aix_core.runtime.verification.completion_gates import (
    CompletionGateResult,
    split_changed_paths,
)


class VerificationPipeline:
    """Deterministic verification pipeline orchestrating GateCatalog evaluators."""

    def __init__(self, catalog: GateCatalog | None = None) -> None:
        self._catalog = catalog or build_default_catalog()

    def catalog(self) -> GateCatalog:
        return self._catalog

    def evaluate_gates(self, context: GateContext) -> tuple[GateResult, ...]:
        """Evaluate all applicable gates in strict ordinal order.

        Exceptions in evaluators are captured as failing GateResults (fail-closed).
        """
        applicable = self._catalog.ordered_for(context)
        results: list[GateResult] = []

        for defn in applicable:
            try:
                res = defn.evaluator(context)
            except Exception as ex:
                res = GateResult(
                    gate_id=defn.gate_id,
                    passed=False,
                    reasons=(f"gate {defn.gate_id!r} evaluator raised exception: {ex}",),
                    details={"exception": str(ex), "traceback": traceback.format_exc()},
                    mandatory=defn.mandatory,
                    immutable=defn.immutable,
                )
            results.append(res)

        return tuple(results)

    def to_completion_gate_result(
        self, gate_results: tuple[GateResult, ...], context: GateContext
    ) -> CompletionGateResult:
        """Convert typed GateResults to identical legacy CompletionGateResult."""
        from backend.aix_core.runtime.verification.completion_gates import evaluate_completion_gates

        # Preserve exact legacy behavior and reasons
        return evaluate_completion_gates(
            discovery=context.discovery,
            diff_stats=context.diff_stats,
            diff_text=context.diff_text,
            test_result=context.test_result,
            already_satisfied=context.already_satisfied,
            already_satisfied_reason=context.already_satisfied_reason,
            already_satisfied_evidence_refs=context.already_satisfied_evidence_refs,
            secret_values=context.secret_values,
            diff_only_verification_allowed=context.diff_only_verification_allowed,
            state_assertions_passed=context.state_assertions_passed,
            state_assertion_detail=context.state_assertion_detail,
            task_family=context.task_family,
            objective=context.objective,
            required_effects=context.required_effects,
            before_files=context.before_files,
            after_files=context.after_files,
            step_contract=context.step_contract,
        )

    async def verify(self, context: GateContext) -> VerificationOutcome:
        """Run full verification pipeline and emit VerificationOutcome."""
        gate_results = self.evaluate_gates(context)
        checks: list[VerificationCheck] = []

        for gr in gate_results:
            status = "PASSED" if gr.passed else "FAILED"
            checks.append(
                VerificationCheck(
                    check_type=gr.gate_id,
                    mandatory=gr.mandatory,
                    status=status,
                    evidence_ref=gr.evidence_ref,
                    failure_reason=gr.reasons[0] if gr.reasons else None,
                    started_at=gr.started_at,
                    completed_at=gr.completed_at,
                )
            )

        failed_mandatory = tuple(c.check_type for c in checks if c.mandatory and c.status != "PASSED")
        return VerificationOutcome(
            checks=tuple(checks),
            all_mandatory_passed=not failed_mandatory,
            failed_mandatory=failed_mandatory,
        )
