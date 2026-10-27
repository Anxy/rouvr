from __future__ import annotations

import hashlib
import json

import pytest

from backend.aix_core.runtime.planner.experimental.decomposer import (
    AcceptanceCriterion,
    CandidateStep,
    DecompositionDisposition,
    DecompositionResult,
    ObjectiveBrief,
)
from backend.aix_core.runtime.planner.experimental.plan_compiler import DagPlanCompiler, PlanCompileError

HASH = "a" * 64


def _brief() -> ObjectiveBrief:
    return ObjectiveBrief(
        objective_id="objective-1",
        tenant_id="tenant-1",
        task_class="docs_only_change",
        statement="Update docs and verify.",
        repository_id="aix",
        allowed_files=("docs/a.md",),
        acceptance_criteria=(
            AcceptanceCriterion(criterion_id="ac-1", description="Change docs."),
            AcceptanceCriterion(criterion_id="ac-2", description="Verify docs."),
        ),
        context_capsule_sha256=HASH,
    )


def _step(
    step_id: str,
    criterion_id: str,
    *,
    dependencies: tuple[str, ...] = (),
) -> CandidateStep:
    return CandidateStep(
        step_id=step_id,
        purpose=step_id,
        repository_id="aix",
        target_files=("docs/a.md",),
        acceptance_criterion_ids=(criterion_id,),
        dependency_ids=dependencies,
    )


def _decomposition(steps: tuple[CandidateStep, ...]) -> DecompositionResult:
    payload = [
        step.model_dump(mode="json")
        for step in sorted(steps, key=lambda candidate: candidate.step_id)
    ]
    decomposition_sha256 = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()
    return DecompositionResult(
        disposition=DecompositionDisposition.CANDIDATE,
        reason_codes=("BOUNDED_CANDIDATE_STEPS",),
        objective_id="objective-1",
        policy_version="policy.v1",
        steps=steps,
        decomposition_sha256=decomposition_sha256,
    )


def test_compiler_emits_deterministic_topological_sequential_plan() -> None:
    decomposition = _decomposition(
        (
            _step("verify", "ac-2", dependencies=("change",)),
            _step("change", "ac-1"),
        )
    )

    plan = DagPlanCompiler().compile(brief=_brief(), decomposition=decomposition)

    assert [step.step_id for step in plan.steps] == ["change", "verify"]
    assert [step.sequence for step in plan.steps] == [1, 2]
    assert plan.execution_mode == "SEQUENTIAL_V1"
    assert plan.acceptance_map == {"ac-1": ("change",), "ac-2": ("verify",)}


def test_compiler_plan_hash_is_stable_across_candidate_order() -> None:
    left = _decomposition((_step("change", "ac-1"), _step("verify", "ac-2", dependencies=("change",))))
    right = _decomposition(tuple(reversed(left.steps)))
    compiler = DagPlanCompiler()

    assert compiler.compile(brief=_brief(), decomposition=left).plan_sha256 == compiler.compile(
        brief=_brief(), decomposition=right
    ).plan_sha256


def test_compiler_rejects_cycle() -> None:
    decomposition = _decomposition(
        (
            _step("a", "ac-1", dependencies=("b",)),
            _step("b", "ac-2", dependencies=("a",)),
        )
    )

    with pytest.raises(PlanCompileError, match="CYCLIC_DEPENDENCY_GRAPH"):
        DagPlanCompiler().compile(brief=_brief(), decomposition=decomposition)


def test_compiler_rejects_self_dependency() -> None:
    with pytest.raises(PlanCompileError, match="SELF_DEPENDENCY"):
        DagPlanCompiler().compile(
            brief=_brief(),
            decomposition=_decomposition(
                (_step("a", "ac-1", dependencies=("a",)), _step("b", "ac-2"))
            ),
        )


def test_compiler_rejects_missing_dependency() -> None:
    with pytest.raises(PlanCompileError, match="MISSING_DEPENDENCY"):
        DagPlanCompiler().compile(
            brief=_brief(),
            decomposition=_decomposition(
                (_step("a", "ac-1", dependencies=("missing",)), _step("b", "ac-2"))
            ),
        )


def test_compiler_rejects_incomplete_acceptance_mapping() -> None:
    with pytest.raises(PlanCompileError, match="ACCEPTANCE_MAPPING_INCOMPLETE"):
        DagPlanCompiler().compile(
            brief=_brief(),
            decomposition=_decomposition((_step("a", "ac-1"),)),
        )


def test_compiler_rejects_unknown_acceptance_criterion() -> None:
    with pytest.raises(PlanCompileError, match="UNKNOWN_ACCEPTANCE_CRITERION"):
        DagPlanCompiler().compile(
            brief=_brief(),
            decomposition=_decomposition(
                (
                    _step("a", "ac-1"),
                    _step("b", "ac-2"),
                    _step("c", "unknown"),
                )
            ),
        )


def test_compiler_enforces_maximum_steps() -> None:
    with pytest.raises(PlanCompileError, match="MAXIMUM_STEP_COUNT_EXCEEDED"):
        DagPlanCompiler(max_steps=1).compile(
            brief=_brief(),
            decomposition=_decomposition((_step("a", "ac-1"), _step("b", "ac-2"))),
        )


def test_compiler_rejects_non_candidate_decomposition() -> None:
    decomposition = DecompositionResult(
        disposition=DecompositionDisposition.CLARIFICATION_REQUIRED,
        reason_codes=("TARGET_FILES_REQUIRED",),
        objective_id="objective-1",
        policy_version="policy.v1",
    )

    with pytest.raises(PlanCompileError, match="DECOMPOSITION_NOT_COMPILABLE"):
        DagPlanCompiler().compile(brief=_brief(), decomposition=decomposition)


def test_compiler_rejects_objective_identity_mismatch() -> None:
    decomposition = _decomposition((_step("a", "ac-1"), _step("b", "ac-2")))
    mismatched = decomposition.model_copy(update={"objective_id": "objective-2"})

    with pytest.raises(PlanCompileError, match="OBJECTIVE_ID_MISMATCH"):
        DagPlanCompiler().compile(brief=_brief(), decomposition=mismatched)


def test_compiler_rejects_steps_that_do_not_match_attested_decomposition_hash() -> None:
    decomposition = _decomposition((_step("a", "ac-1"), _step("b", "ac-2")))
    forged = decomposition.model_copy(update={"decomposition_sha256": "b" * 64})

    with pytest.raises(PlanCompileError, match="DECOMPOSITION_HASH_MISMATCH"):
        DagPlanCompiler().compile(brief=_brief(), decomposition=forged)
