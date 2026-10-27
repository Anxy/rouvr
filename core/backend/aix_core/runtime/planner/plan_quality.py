"""AIX-MPE-003 Packet MPE-03 + PD-001: plan quality / coverage gate.

Runs before mutation. Reuses `obligation_coverage.validate_obligation_coverage`
against the full `PlanRevisionV1.steps` tuple.

OutcomeContract coverage (DoD architecture): when `require_outcome_contract` is
True, a sealed OutcomeContractV1 must exist and every mandatory facet
operation/entity/mapping must be bound before Plan ACCEPTED.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from backend.aix_core.runtime.objective.contracts import ObjectiveObligationV1, PlanRevisionV1
from backend.aix_core.runtime.objective.outcome_contract import OutcomeContractV1
from backend.aix_core.runtime.verification.obligation_coverage import validate_obligation_coverage
from backend.aix_core.runtime.objective.objective_scope import (
    validate_objective_scope_coverage,
)
from backend.aix_core.runtime.verification.contract_coverage import evaluate_contract_coverage
from backend.aix_core.runtime.verification.objective_verification_contract import (
    ObjectiveVerificationContractV1,
    evaluate_ovc_disposition_coverage,
)

PlanQualityCode = str

_GENERIC_ACCEPTANCE = frozenset(
    {
        "done",
        "complete",
        "change complete",
        "updated",
        "implemented",
        "finished",
        "ok",
        "success",
        "works",
        "change is complete",
    }
)
_TEST_RE = re.compile(r"\b(tests?|pytest|coverage|proving)\b", re.IGNORECASE)
_TEST_STEP_RE = re.compile(
    r"\b(test_|tests?|pytest|spec|validation|verify)\b", re.IGNORECASE
)
_TEST_PATH_RE = re.compile(r"(^|/)tests?/|test_|\.spec\.", re.IGNORECASE)
_API_PATH_RE = re.compile(r"router|route|api|endpoint|controller", re.IGNORECASE)
_SERVICE_PATH_RE = re.compile(r"service|domain|core", re.IGNORECASE)
_CONFIG_PATH_RE = re.compile(r"config|settings|env", re.IGNORECASE)


def _scope_roles(target_scope: tuple[str, ...]) -> set[str]:
    roles: set[str] = set()
    for path in target_scope:
        text = str(path or "")
        if _TEST_PATH_RE.search(text):
            roles.add("tests")
        if _API_PATH_RE.search(text):
            roles.add("api")
        if _SERVICE_PATH_RE.search(text):
            roles.add("service")
        if _CONFIG_PATH_RE.search(text):
            roles.add("config")
    return roles


@dataclass(frozen=True)
class PlanQualityResult:
    ok: bool
    code: PlanQualityCode
    detail: str
    uncovered_obligation_ids: tuple[str, ...] = ()


def _generic_acceptance(step_purpose: str, criteria: tuple[str, ...], objective: str) -> bool:
    if not criteria:
        return True
    objective_fold = (objective or "").casefold().strip()
    purpose_fold = (step_purpose or "").casefold().strip()
    if objective_fold and purpose_fold and purpose_fold == objective_fold:
        return True
    cleaned = tuple(str(item).casefold().strip(" .") for item in criteria if str(item).strip())
    if not cleaned:
        return True
    return all(item in _GENERIC_ACCEPTANCE for item in cleaned)


def evaluate_plan_quality(
    *,
    plan: PlanRevisionV1,
    obligations: tuple[ObjectiveObligationV1, ...] = (),
    allowed_files: tuple[str, ...] = (),
    objective: str = "",
    used_legacy_one_step: bool = False,
    outcome_contract: OutcomeContractV1 | None = None,
    require_outcome_contract: bool = False,
    ovc: ObjectiveVerificationContractV1 | None = None,
) -> PlanQualityResult:
    del allowed_files
    if not plan.steps:
        return PlanQualityResult(ok=False, code="PLAN_INVALID", detail="plan has no steps")

    # OVC disposition coverage gate (Correction 5): Every OV item must have a valid disposition
    if ovc is not None:
        ovc_cov = evaluate_ovc_disposition_coverage(ovc, plan.steps)
        if not ovc_cov.ok:
            return PlanQualityResult(
                ok=False,
                code="OVC_DISPOSITION_INCOMPLETE",
                detail=f"plan lacks disposition for OVC items: missing={ovc_cov.missing_ov_ids}, invalid={ovc_cov.invalid_dispositions}",
            )

    # OutcomeContract before authoritative decomposition / ACCEPTED plan.
    coverage = evaluate_contract_coverage(
        outcome_contract=outcome_contract,
        steps=plan.steps,
        require_outcome_contract=require_outcome_contract,
    )
    if not coverage.ok:
        return PlanQualityResult(
            ok=False,
            code=coverage.code,
            detail=coverage.detail,
        )

    known_ids = {item.id for item in plan.steps}
    for step in plan.steps:
        if not step.acceptance_criteria or not step.validation_commands:
            return PlanQualityResult(
                ok=False,
                code="PLAN_INVALID",
                detail=f"step {step.id!r} missing acceptance_criteria or validation_commands",
            )
        unknown_deps = [dep for dep in step.depends_on if dep not in known_ids]
        if unknown_deps:
            return PlanQualityResult(
                ok=False,
                code="INVALID_STEP_DEPENDENCY",
                detail=f"step {step.id!r} depends on unknown steps {unknown_deps!r}",
            )
        earlier_ids = {item.id for item in plan.steps if item.step_index < step.step_index}
        forward_deps = [dep for dep in step.depends_on if dep not in earlier_ids]
        if forward_deps:
            return PlanQualityResult(
                ok=False,
                code="INVALID_STEP_DEPENDENCY",
                detail=f"step {step.id!r} has forward or self dependencies {forward_deps!r}",
            )
        roles = _scope_roles(step.target_scope)
        if len(roles) >= 3 and len(plan.steps) == 1:
            return PlanQualityResult(
                ok=False,
                code="COMPLEX_OBJECTIVE_COLLAPSED",
                detail=f"step {step.id!r} bundles independent target scopes {sorted(roles)} without decomposition",
            )
        if not step.target_scope:
            return PlanQualityResult(
                ok=False,
                code="UNGROUNDED_TARGET_SCOPE",
                detail=f"step {step.id!r} has empty target_scope",
            )
        if objective.strip() and _generic_acceptance(step.purpose, step.acceptance_criteria, objective):
            return PlanQualityResult(
                ok=False,
                code="GENERIC_ACCEPTANCE_CRITERIA",
                detail=f"step {step.id!r} acceptance criteria are generic or repeat the objective",
            )

    obl_coverage = validate_obligation_coverage(obligations=obligations, step_contracts=plan.steps)
    if not obl_coverage.ok:
        descriptions = ", ".join(o.description for o in obl_coverage.uncovered)
        return PlanQualityResult(
            ok=False,
            code="OBLIGATION_COVERAGE_INCOMPLETE",
            detail=f"required obligations are not covered by any step: {descriptions}",
            uncovered_obligation_ids=tuple(o.id for o in obl_coverage.uncovered),
        )

    scope = validate_objective_scope_coverage(
        original_objective=objective,
        plan_steps=plan.steps,
    )
    if not scope.ok:
        return PlanQualityResult(
            ok=False,
            code=scope.code,
            detail=scope.detail,
            uncovered_obligation_ids=tuple(
                o.id
                for o in obligations
                if o.required
                and any(
                    m.kind in (o.description or "").casefold()
                    or m.label.casefold() == (o.description or "").casefold()
                    for m in scope.missing
                )
            ),
        )

    required = tuple(o for o in obligations if o.required)
    asks_tests = _TEST_RE.search(objective or "") or any(
        _TEST_RE.search(o.description) for o in required
    )
    has_test_step = any(
        _TEST_STEP_RE.search(step.purpose)
        or any(_TEST_STEP_RE.search(path) for path in step.target_scope)
        or any(_TEST_STEP_RE.search(cmd) for cmd in step.validation_commands)
        for step in plan.steps
    )
    if asks_tests and not has_test_step:
        return PlanQualityResult(
            ok=False,
            code="MISSING_REQUESTED_TEST_COVERAGE",
            detail="objective requests tests but no step represents tests or validation",
        )

    collapsed = len(plan.steps) == 1 and (
        len(required) >= 3
        or (used_legacy_one_step and len(required) >= 2)
        or (asks_tests and len(required) >= 2)
    )
    if collapsed:
        return PlanQualityResult(
            ok=False,
            code="COMPLEX_OBJECTIVE_COLLAPSED",
            detail=(
                "single catch-all step cannot cover independently verifiable obligations; "
                "decompose into coherent bounded steps"
            ),
        )

    return PlanQualityResult(ok=True, code="PLAN_ACCEPTED", detail="plan accepted")


__all__ = ["PlanQualityResult", "evaluate_plan_quality"]
