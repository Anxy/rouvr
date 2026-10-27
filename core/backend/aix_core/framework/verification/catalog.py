"""AIX Framework Gate Catalog.

Sprint: F5 Verification Framework (F5-P2)
Grounded in: completion_gates.py, product_structure_coverage.py (Law C immutable),
structural_verify.py, semantic_verifier.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Callable

from backend.aix_core.framework.verification.contract import (
    GateContext,
    GateResult,
    VerificationContract,
)


class ImmutableGateError(RuntimeError):
    """Raised when an illegal attempt is made to unregister, replace, or bypass an immutable gate."""


def _utc() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class GateDefinition:
    gate_id: str
    ordinal: int
    evaluator: Callable[[GateContext], GateResult]
    mandatory: bool = True
    immutable: bool = False
    applies: Callable[[GateContext], bool] = field(default_factory=lambda: (lambda ctx: True))
    source: str = "framework.verification.catalog"
    version: str = "1.0.0"


class GateCatalog:
    """Deterministic, immutable-capable registry of verification gates.

    Enforces that canonical gates (such as Continuity Law C) cannot be unregistered,
    reordered, or soft-passed.
    """

    def __init__(self) -> None:
        self._gates: dict[str, GateDefinition] = {}
        self._ordinals: dict[int, str] = {}
        self._frozen: bool = False

    def is_frozen(self) -> bool:
        return self._frozen

    def freeze(self) -> None:
        """Lock catalog against further registration or modification."""
        self._frozen = True

    def register(self, definition: GateDefinition) -> None:
        if self._frozen:
            raise ImmutableGateError("Cannot register gate on a frozen GateCatalog.")
        if definition.gate_id in self._gates:
            existing = self._gates[definition.gate_id]
            if existing.immutable:
                raise ImmutableGateError(f"Gate {definition.gate_id!r} is immutable and cannot be replaced.")
        if definition.ordinal in self._ordinals and self._ordinals[definition.ordinal] != definition.gate_id:
            raise ValueError(
                f"Ordinal {definition.ordinal} is already occupied by gate {self._ordinals[definition.ordinal]!r}."
            )
        self._gates[definition.gate_id] = definition
        self._ordinals[definition.ordinal] = definition.gate_id

    def unregister(self, gate_id: str) -> None:
        if self._frozen:
            raise ImmutableGateError("Cannot unregister gate on a frozen GateCatalog.")
        if gate_id not in self._gates:
            raise KeyError(f"Gate {gate_id!r} not found in catalog.")
        definition = self._gates[gate_id]
        if definition.immutable:
            raise ImmutableGateError(f"Gate {gate_id!r} is immutable and cannot be unregistered.")
        del self._gates[gate_id]
        del self._ordinals[definition.ordinal]

    def get(self, gate_id: str) -> GateDefinition:
        if gate_id not in self._gates:
            raise KeyError(f"Gate {gate_id!r} not found in catalog.")
        return self._gates[gate_id]

    def ordered_for(self, context: GateContext) -> tuple[GateDefinition, ...]:
        """Return applicable gates sorted deterministically by ordinal."""
        applicable = [
            defn for defn in self._gates.values()
            if defn.applies(context)
        ]
        applicable.sort(key=lambda d: d.ordinal)
        return tuple(applicable)

    def list_all(self) -> tuple[GateDefinition, ...]:
        items = list(self._gates.values())
        items.sort(key=lambda d: d.ordinal)
        return tuple(items)


# ==============================================================================
# Canonical Evaluators (Wrapping Verified Runtime Assets)
# ==============================================================================

def _eval_plan_contract(ctx: GateContext) -> GateResult:
    started = _utc()
    if ctx.contract is None:
        return GateResult(
            gate_id="plan_contract",
            passed=False,
            reasons=("missing verification contract",),
            mandatory=True,
            immutable=True,
            started_at=started,
            completed_at=_utc(),
        )
    return GateResult(
        gate_id="plan_contract",
        passed=True,
        mandatory=True,
        immutable=True,
        started_at=started,
        completed_at=_utc(),
    )


def _eval_structural_scope(ctx: GateContext) -> GateResult:
    """Structural authority via live `evaluate_completion_gates` structural_effects key."""
    started = _utc()
    from backend.aix_core.runtime.verification.completion_gates import evaluate_completion_gates

    gate = evaluate_completion_gates(
        discovery=ctx.discovery,
        diff_stats=ctx.diff_stats,
        diff_text=ctx.diff_text,
        test_result=ctx.test_result,
        already_satisfied=ctx.already_satisfied,
        already_satisfied_reason=ctx.already_satisfied_reason,
        already_satisfied_evidence_refs=ctx.already_satisfied_evidence_refs,
        secret_values=ctx.secret_values,
        diff_only_verification_allowed=ctx.diff_only_verification_allowed,
        state_assertions_passed=ctx.state_assertions_passed,
        state_assertion_detail=ctx.state_assertion_detail,
        task_family=ctx.task_family,
        objective=ctx.objective,
        required_effects=ctx.required_effects,
        before_files=ctx.before_files,
        after_files=ctx.after_files,
        step_contract=ctx.step_contract,
    )
    gates = dict(gate.gates or {})
    # Absent key = N/A for this run (same as runtime when no structural targets).
    if "structural_effects" not in gates:
        return GateResult(
            gate_id="structural_scope",
            passed=True,
            reasons=(),
            details={"authority": "evaluate_completion_gates", "checked": False},
            mandatory=True,
            immutable=True,
            started_at=started,
            completed_at=_utc(),
        )
    passed = bool(gates["structural_effects"])
    reasons = tuple(r for r in (gate.reasons or ()) if "structural" in str(r).lower()) if not passed else ()
    return GateResult(
        gate_id="structural_scope",
        passed=passed,
        reasons=reasons or (() if passed else ("structural_effects failed",)),
        details={"authority": "evaluate_completion_gates", "checked": True},
        mandatory=True,
        immutable=True,
        started_at=started,
        completed_at=_utc(),
    )


def _eval_objective_coverage(ctx: GateContext) -> GateResult:
    """Objective coverage via real `check_objective_coverage` authority."""
    started = _utc()
    from backend.aix_core.objective_cycle.objective_coverage import check_objective_coverage

    cov = check_objective_coverage(
        objective=ctx.objective or "",
        diff_text=ctx.diff_text or "",
        task_family=ctx.task_family or "",
        workspace_files=ctx.after_files,
    )
    return GateResult(
        gate_id="objective_coverage",
        passed=bool(cov.satisfied),
        reasons=() if cov.satisfied else (cov.detail or "objective_coverage_failed",),
        details={
            "authority": "check_objective_coverage",
            "triggered_signals": list(cov.triggered_signals),
            "missing_signals": list(cov.missing_signals),
        },
        mandatory=True,
        immutable=True,
        started_at=started,
        completed_at=_utc(),
    )


def _eval_continuity_law_c(ctx: GateContext) -> GateResult:
    """Continuity Law C Evaluator: Fail-closed product structure coverage verification."""
    started = _utc()
    from backend.aix_core.runtime.verification.product_structure_coverage import (
        evaluate_product_structure_coverage,
        step_has_continuity_refs,
    )

    continuity_req = ctx.contract.continuity_required if ctx.contract else step_has_continuity_refs(ctx.step_contract)
    if not continuity_req:
        return GateResult(
            gate_id="continuity_law_c",
            passed=True,
            mandatory=True,
            immutable=True,
            details={"continuity_required": False, "checked": False},
            started_at=started,
            completed_at=_utc(),
        )

    psc = evaluate_product_structure_coverage(
        step_contract=ctx.step_contract,
        after_files=ctx.after_files or {},
        before_files=ctx.before_files or {},
        diff_text=ctx.diff_text or "",
    )
    reasons: tuple[str, ...] = ()
    if not psc.ok:
        reasons = (f"{psc.code}: {psc.detail}",)
    return GateResult(
        gate_id="continuity_law_c",
        passed=bool(psc.ok),
        reasons=reasons,
        details={
            "continuity_required": True,
            "checked": bool(psc.checked),
            "code": psc.code,
            "missing_operation_refs": list(psc.missing_operation_refs),
            "missing_entity_refs": list(psc.missing_entity_refs),
        },
        mandatory=True,
        immutable=True,
        started_at=started,
        completed_at=_utc(),
    )


def _eval_independent_completion(ctx: GateContext) -> GateResult:
    started = _utc()
    from backend.aix_core.runtime.verification.verification import IndependentVerifier, VerificationInputV1
    from backend.aix_core.runtime.verification.completion_gates import split_changed_paths, _is_meaningful_change

    source_changed, _ = split_changed_paths(ctx.diff_stats)
    meaningful = any(_is_meaningful_change(ctx.diff_stats[p]) for p in source_changed)

    inp = VerificationInputV1(
        source_files_changed=meaningful or (ctx.already_satisfied and bool(source_changed)),
        test_suite_passed=bool(ctx.test_result and ctx.test_result.get("passed", False)),
        diff_only_allowed=ctx.diff_only_verification_allowed,
        state_assertions_passed=ctx.state_assertions_passed,
        state_assertion_detail=ctx.state_assertion_detail,
    )
    verdict, reasons = IndependentVerifier().verify(inp)
    passed = verdict == "PASSED"

    return GateResult(
        gate_id="independent_completion",
        passed=passed,
        reasons=tuple(reasons),
        details={"verification_verdict": verdict},
        mandatory=True,
        immutable=True,
        started_at=started,
        completed_at=_utc(),
    )


def _eval_contract_coverage(ctx: GateContext) -> GateResult:
    """PD-003E: real `evaluate_contract_coverage` authority — never stub PASS."""
    started = _utc()
    from backend.aix_core.runtime.verification.contract_coverage import evaluate_contract_coverage

    step = ctx.step_contract
    steps = (step,) if step is not None else ()
    outcome = getattr(ctx, "outcome_contract", None)
    # Legacy paths without a sealed OutcomeContract skip (same as runtime gate).
    require = outcome is not None and bool(getattr(outcome, "contract_hash", "") or "")
    coverage = evaluate_contract_coverage(
        outcome_contract=outcome,
        steps=steps,
        require_outcome_contract=require,
    )
    reasons: tuple[str, ...] = ()
    if not coverage.ok:
        reasons = (f"{coverage.code}: {coverage.detail}",)
    return GateResult(
        gate_id="contract_coverage",
        passed=bool(coverage.ok),
        reasons=reasons,
        details={
            "code": coverage.code,
            "unbound_operations": list(coverage.unbound_operations),
            "unbound_entities": list(coverage.unbound_entities),
            "unbound_mappings": list(coverage.unbound_mappings),
            "uncovered_facets": list(coverage.uncovered_facets),
        },
        mandatory=True,
        immutable=True,
        started_at=started,
        completed_at=_utc(),
    )


def _eval_semantic_step(ctx: GateContext) -> GateResult:
    """Semantic / mechanical authority via completion gates + mechanical layer.

    Does not invent PASS. Uses live runtime authorities. Full LLM semantic verify
    remains owned by `semantic_verifier.verify_step` on the OC path — catalog is
    advisory until parity is proven.
    """
    started = _utc()
    from backend.aix_core.runtime.verification.completion_gates import evaluate_completion_gates

    exec_result = getattr(ctx, "execution_result", None)
    if exec_result is not None and hasattr(exec_result, "completion_gates"):
        from backend.aix_core.runtime.verification.semantic_verifier import _mechanical_layer

        passed, failed = _mechanical_layer(exec_result)
        reasons = tuple(f"mechanical:{item}" for item in failed) if failed else ()
        return GateResult(
            gate_id="semantic_step",
            passed=bool(passed),
            reasons=reasons,
            details={"authority": "semantic_verifier._mechanical_layer"},
            mandatory=True,
            immutable=True,
            started_at=started,
            completed_at=_utc(),
        )

    gate = evaluate_completion_gates(
        discovery=ctx.discovery,
        diff_stats=ctx.diff_stats,
        diff_text=ctx.diff_text,
        test_result=ctx.test_result,
        already_satisfied=ctx.already_satisfied,
        already_satisfied_reason=ctx.already_satisfied_reason,
        already_satisfied_evidence_refs=ctx.already_satisfied_evidence_refs,
        secret_values=ctx.secret_values,
        diff_only_verification_allowed=ctx.diff_only_verification_allowed,
        state_assertions_passed=ctx.state_assertions_passed,
        state_assertion_detail=ctx.state_assertion_detail,
        task_family=ctx.task_family,
        objective=ctx.objective,
        required_effects=ctx.required_effects,
        before_files=ctx.before_files,
        after_files=ctx.after_files,
        step_contract=ctx.step_contract,
    )
    # Fail closed on objective_coverage / verification_passed — the semantic stand-ins
    # available without an LLM call.
    gates = dict(gate.gates or {})
    coverage_ok = gates.get("objective_coverage", True)
    verification_ok = gates.get("verification_passed", True)
    passed = bool(gate.passed and coverage_ok and verification_ok)
    reasons = tuple(gate.reasons) if not passed else ()
    return GateResult(
        gate_id="semantic_step",
        passed=passed,
        reasons=reasons,
        details={
            "authority": "evaluate_completion_gates",
            "objective_coverage": coverage_ok,
            "verification_passed": verification_ok,
        },
        mandatory=True,
        immutable=True,
        started_at=started,
        completed_at=_utc(),
    )


def _eval_integration_proof(ctx: GateContext) -> GateResult:
    """Static integration proof via real `evaluate_integration_proof` authority."""
    started = _utc()
    from backend.aix_core.runtime.verification.integration_proof import evaluate_integration_proof

    step = ctx.step_contract
    steps = (step,) if step is not None else ()
    outcome = getattr(ctx, "outcome_contract", None)
    require = outcome is not None
    proof = evaluate_integration_proof(
        outcome_contract=outcome,
        steps=steps,
        require_outcome_contract=require,
    )
    # SKIPPED_NO_CONTRACT is an explicit non-failure for legacy paths.
    passed = bool(proof.ok) or proof.result == "INTEGRATION_SKIPPED_NO_CONTRACT"
    reasons: tuple[str, ...] = ()
    if not passed:
        reasons = tuple(
            f"{m.code}:{m.detail}" for m in (proof.mismatches or ())
        ) or (f"integration_proof:{proof.result}",)
    return GateResult(
        gate_id="integration_proof",
        passed=passed,
        reasons=reasons,
        details={
            "result": proof.result,
            "uncovered_facets": list(proof.uncovered_facets),
            "verifier_version": proof.verifier_version,
        },
        mandatory=True,
        immutable=True,
        started_at=started,
        completed_at=_utc(),
    )


def build_default_catalog() -> GateCatalog:
    """Build and freeze the canonical 8-gate catalog."""
    catalog = GateCatalog()

    catalog.register(
        GateDefinition(
            gate_id="plan_contract",
            ordinal=10,
            evaluator=_eval_plan_contract,
            mandatory=True,
            immutable=True,
        )
    )
    catalog.register(
        GateDefinition(
            gate_id="structural_scope",
            ordinal=20,
            evaluator=_eval_structural_scope,
            mandatory=True,
            immutable=True,
        )
    )
    catalog.register(
        GateDefinition(
            gate_id="objective_coverage",
            ordinal=30,
            evaluator=_eval_objective_coverage,
            mandatory=True,
            immutable=True,
        )
    )
    catalog.register(
        GateDefinition(
            gate_id="continuity_law_c",
            ordinal=40,
            evaluator=_eval_continuity_law_c,
            mandatory=True,
            immutable=True,  # Permanently immutable Law C
            source="backend.aix_core.runtime.verification.product_structure_coverage",
        )
    )
    catalog.register(
        GateDefinition(
            gate_id="independent_completion",
            ordinal=50,
            evaluator=_eval_independent_completion,
            mandatory=True,
            immutable=True,
        )
    )
    catalog.register(
        GateDefinition(
            gate_id="semantic_step",
            ordinal=60,
            evaluator=_eval_semantic_step,
            mandatory=True,
            immutable=True,
        )
    )
    catalog.register(
        GateDefinition(
            gate_id="contract_coverage",
            ordinal=70,
            evaluator=_eval_contract_coverage,
            mandatory=True,
            immutable=True,
        )
    )
    catalog.register(
        GateDefinition(
            gate_id="integration_proof",
            ordinal=80,
            evaluator=_eval_integration_proof,
            mandatory=True,
            immutable=True,
        )
    )

    catalog.freeze()
    return catalog
