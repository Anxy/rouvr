"""AIX-CLOSED-Loop-004 (AIX-OHC-002) Packet P3: Objective Fidelity / Obligation
Coverage.

    Objective Obligations
            v
        StepContracts
            v
      Coverage Validation

The planner (`step_contract_planner.propose_step_contract`, Packet P2's obligations
threaded in as of this packet) may decide HOW. It may never silently redefine WHAT by
proposing a plan that drops a required obligation. This module is the deterministic,
no-model gate that catches that before `run_real_execute` is ever called -- reused
verbatim by `durable_api_backend._maybe_real_execute`, never a second copy of the
check.

Deliberately narrow for this packet, matching this codebase's "single-step-per-cycle"
architecture today (see `step_attempt.py`'s own docstring): "explicitly verified by a
step" (the sprint doc's second, alternative form of coverage) has no separate
verification-only step type to check against yet -- only `obligation_refs` (a step
that implements/addresses the obligation) counts as coverage here. `StepContractV1[]`
becoming a real, multi-step plan is Packet P4's job, not this one's.
"""
from __future__ import annotations

from dataclasses import dataclass

from backend.aix_core.runtime.objective.contracts import ObjectiveObligationV1, StepContractV1


@dataclass(frozen=True)
class ObligationCoverageResult:
    ok: bool
    uncovered: tuple[ObjectiveObligationV1, ...]


_META_DOD_MARKERS = (
    "mutation summary",
    "repository state exists",
    "requested repository state",
    "evidence-accurate",
    "scoped change complete",
    "validation evidence attached",
)


def is_meta_definition_of_done(description: str) -> bool:
    text = str(description or "").casefold()
    if not text:
        return False
    from backend.aix_core.runtime.objective.objective_scope import is_generic_meta_dod

    return is_generic_meta_dod(text) or any(marker in text for marker in _META_DOD_MARKERS)


def attach_meta_dod_obligation_refs(
    *,
    obligations: tuple[ObjectiveObligationV1, ...],
    step_contracts: tuple[StepContractV1, ...],
) -> tuple[StepContractV1, ...]:
    """Attach live-helper meta DoD items (mutation summary / repo state) onto steps.

    Planners often cover the product ask but omit these always-present DoD strings,
    which then fail PLAN_INCOMPLETE even when the edit step is otherwise sound (FE#6).
    """
    if not step_contracts or not obligations:
        return step_contracts
    covered: set[str] = set()
    for step in step_contracts:
        covered.update(step.obligation_refs)
    missing = tuple(
        o.id
        for o in obligations
        if o.required and is_meta_definition_of_done(o.description) and o.id not in covered
    )
    if not missing:
        return step_contracts
    first = step_contracts[0]
    new_refs = tuple(dict.fromkeys(tuple(first.obligation_refs) + missing))
    return (first.model_copy(update={"obligation_refs": new_refs}),) + tuple(step_contracts[1:])


def validate_obligation_coverage(
    *,
    obligations: tuple[ObjectiveObligationV1, ...],
    step_contracts: tuple[StepContractV1, ...],
) -> ObligationCoverageResult:
    """Every `required` obligation must appear in at least one step's `obligation_refs`.

    Non-required obligations are exempt (the sprint doc's own "Required obligations
    must be..." framing). `uncovered` preserves `obligations`' own order (the
    admission-time order Packet P2 derived them in), not insertion order into a set.
    """
    steps = attach_meta_dod_obligation_refs(
        obligations=obligations, step_contracts=step_contracts
    )
    covered_ids: set[str] = set()
    for step in steps:
        covered_ids.update(step.obligation_refs)
    uncovered = tuple(o for o in obligations if o.required and o.id not in covered_ids)
    return ObligationCoverageResult(ok=not uncovered, uncovered=uncovered)


__all__ = [
    "ObligationCoverageResult",
    "attach_meta_dod_obligation_refs",
    "is_meta_definition_of_done",
    "validate_obligation_coverage",
]
