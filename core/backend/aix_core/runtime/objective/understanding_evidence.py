"""AIX Closed-Loop Execution Plan, Sprint W4: build a `contracts.UnderstandingEvidenceV1`
snapshot from an `execution_context.ExecutionContext` -- the live, mutable, per-objective
working memory `exploration_planner.py`/`_run_exploration_gate` already populate.

This module never re-derives evidence itself (no new file reads, no new searches) --
it only reshapes facts `ExecutionContext` already collected into the frozen, versioned
contract, the same "reshape existing truth, don't recompute it" discipline
`verification_engine.py` established for verification facts.
"""

from __future__ import annotations

from backend.aix_core.runtime.objective.contracts import UnderstandingEvidenceV1
from backend.aix_core.runtime.planner.execution_context import ExecutionContext


def build_understanding_evidence(
    *,
    objective_id: str,
    cycle_id: str | None,
    context: ExecutionContext,
    observed_problem: str,
    assumptions: tuple[str, ...] = (),
    uncertainties: tuple[str, ...] = (),
) -> UnderstandingEvidenceV1:
    """Snapshot `context` into an immutable `UnderstandingEvidenceV1`. `observed_problem`
    is required (a snapshot with no stated problem understanding is not evidence of
    understanding) -- callers with nothing yet to say must not call this until they do,
    rather than passing a placeholder string.
    """
    files_read = tuple(record.path for record in context.files_read)
    symbols_found = tuple(record.name for record in context.symbols_resolved if record.name)
    relevant_tests = tuple(
        str(item.get("path") or item.get("name") or "")
        for item in context.tests_executed
        if isinstance(item, dict) and (item.get("path") or item.get("name"))
    )
    dependencies = tuple(context.repositories_explored)
    evidence_refs = tuple(
        f"toolcall://{record.tool_call_id}" for record in context.files_read if record.tool_call_id
    )
    return UnderstandingEvidenceV1(
        objective_id=objective_id,
        cycle_id=cycle_id,
        files_read=files_read,
        symbols_found=symbols_found,
        relevant_tests=relevant_tests,
        dependencies=dependencies,
        observed_problem=observed_problem,
        assumptions=assumptions,
        uncertainties=uncertainties,
        evidence_refs=evidence_refs,
        understanding_complete=context.understanding_complete,
    )


__all__ = ["build_understanding_evidence"]
