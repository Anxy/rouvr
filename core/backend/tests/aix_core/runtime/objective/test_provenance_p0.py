"""P0: provenance contracts + pure validators. No live wiring, no Explore."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.aix_core.runtime.objective.contracts import BudgetV1
from backend.aix_core.runtime.objective.provenance.contracts import (
    ApprovalBinding,
    AttemptEvidence,
    ClaimCite,
    EvidenceProvenance,
    ExplorationEvidence,
    ExplorationNeed,
    GovernanceRevision,
    PlanRevision,
    PlanTarget,
    StepTargetProjection,
    UnderstandingClaim,
    UnderstandingRevision,
    UserIntent,
    UserTurn,
)
from backend.aix_core.runtime.objective.provenance.validators import (
    assert_effect_is_required_effect_kind,
    create_root_contains,
    validate_approval_for_execute,
    validate_execute_diff,
    validate_explore_gate_sources,
    validate_need,
    validate_need_evidence,
    validate_plan_revision,
    validate_plan_target,
    validate_replan_revision,
    validate_reunderstand,
    validate_target_kind,
    validate_understanding_revision,
)

_PROVENANCE_DIR = (
    Path(__file__).resolve().parents[4]
    / "aix_core"
    / "runtime"
    / "objective"
    / "provenance"
)
_BACKEND_ROOT = Path(__file__).resolve().parents[4]


def _budget() -> BudgetV1:
    return BudgetV1(time_seconds=60, tokens=1000, cost_usd=1.0, retries=1, steps=4, concurrency=1)


def _cite(kind: str, ref: str) -> ClaimCite:
    return ClaimCite(kind=kind, ref=ref)  # type: ignore[arg-type]


def _prov() -> EvidenceProvenance:
    return EvidenceProvenance(tool="search_files", query="timeout", fingerprint="fp1")


def _intent(*, text: str = "Fix login timeout in src/auth/jwt.py") -> UserIntent:
    return UserIntent(objective_id="obj_1", text=text)


def _governance(
    *,
    create_root: tuple[str, ...] = ("proposed/",),
    create_capability: tuple[str, ...] = ("CREATE_FILE", "CREATE_DIRECTORY"),
    forbidden_effects: tuple[str, ...] = ("production_mutation",),
    approval_policy: str = "INITIAL_PLAN_ONLY",
) -> GovernanceRevision:
    return GovernanceRevision(
        objective_id="obj_1",
        forbidden_effects=forbidden_effects,
        create_capability=create_capability,  # type: ignore[arg-type]
        create_root=create_root,
        task_class="code_change",
        budgets=_budget(),
        approval_policy=approval_policy,  # type: ignore[arg-type]
    )


def _claim(
    *,
    kind: str,
    text: str,
    cites: tuple[ClaimCite, ...],
    standing: str = "ASSERTED",
    status: str = "ACTIVE",
    claim_id: str | None = None,
) -> UnderstandingClaim:
    kwargs: dict[str, object] = {
        "kind": kind,
        "text": text,
        "cites": cites,
        "standing": standing,
        "status": status,
    }
    if claim_id is not None:
        kwargs["id"] = claim_id
    return UnderstandingClaim(**kwargs)  # type: ignore[arg-type]


def _chain(*, intent_text: str = "Fix login timeout in src/auth/jwt.py", extra_evidence: tuple[ExplorationEvidence, ...] = ()):
    intent = _intent(text=intent_text)
    outcome = _claim(
        kind="OUTCOME",
        text="Login timeout is fixed",
        cites=(_cite("INTENT_SPAN", intent.id),),
        claim_id="cl_outcome",
    )
    missing = _claim(
        kind="MISSING_INFO",
        text="Where is the timeout implemented?",
        cites=(_cite("CLAIM", outcome.id),),
        claim_id="cl_missing",
    )
    understanding = UnderstandingRevision(
        objective_id="obj_1",
        intent_id=intent.id,
        scope_mode="OPEN_IMPLEMENTATION",
        claims=(outcome, missing),
    )
    need = ExplorationNeed(
        objective_id="obj_1",
        understanding_revision_id=understanding.id,
        claim_ids=(missing.id,),
        purpose="locate timeout",
        question="Where is login timeout configured?",
        source="repository",
        result_type="file_content",
        budget_files=8,
    )
    evidence = ExplorationEvidence(
        need_id=need.id,
        objective_id="obj_1",
        source="repository",
        location="src/auth/jwt.py",
        observation="timeout constant is 30s",
        exists=True,
        provenance=_prov(),
        id="ev_jwt",
    )
    return intent, understanding, need, (evidence, *extra_evidence)


def _existing_target(*, evidence_id: str, path: str = "src/auth/jwt.py", outcome_id: str = "cl_outcome") -> PlanTarget:
    return PlanTarget(
        kind="EXISTING",
        path=path,
        effect="LITERAL_REPLACE",
        rationale="change the timeout constant",
        evidence_id=evidence_id,
        outcome_claim_ids=(outcome_id,),
    )


def _plan(
    *,
    intent: UserIntent,
    understanding: UnderstandingRevision,
    governance: GovernanceRevision,
    targets: tuple[PlanTarget, ...],
    steps: tuple[StepTargetProjection, ...] | None = None,
    revision: int = 1,
    approval_ref: str | None = None,
    plan_id: str | None = None,
) -> PlanRevision:
    kwargs: dict[str, object] = {
        "objective_id": "obj_1",
        "cycle_id": "cyc_1",
        "revision": revision,
        "intent_id": intent.id,
        "understanding_revision_id": understanding.id,
        "evidence_ids": tuple(t.evidence_id for t in targets if t.evidence_id),
        "governance_revision_id": governance.id,
        "targets": targets,
        "steps": steps if steps is not None else tuple(StepTargetProjection(target_scope=(t.path,)) for t in targets),
        "approval_ref": approval_ref,
    }
    if plan_id is not None:
        kwargs["plan_id"] = plan_id
    return PlanRevision(**kwargs)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Contracts compile
# ---------------------------------------------------------------------------


def test_contracts_compile_and_forbid_extra() -> None:
    intent = _intent()
    turn = UserTurn(objective_id="obj_1", intent_id=intent.id, text="Also the PHP API.")
    claim = _claim(kind="OUTCOME", text="done", cites=(_cite("INTENT_SPAN", intent.id),))
    rev = UnderstandingRevision(
        objective_id="obj_1", intent_id=intent.id, scope_mode="BOUNDED", claims=(claim,)
    )
    need = ExplorationNeed(
        objective_id="obj_1",
        understanding_revision_id=rev.id,
        claim_ids=(claim.id,),
        purpose="p",
        question="q",
        source="repository",
        result_type="existence",
    )
    ev = ExplorationEvidence(
        need_id=need.id,
        objective_id="obj_1",
        source="repository",
        observation="absent",
        provenance=_prov(),
    )
    gov = _governance()
    target = PlanTarget(
        kind="CREATE",
        path="proposed/index.html",
        effect="LITERAL_REPLACE",
        rationale="new page",
        create_capability="CREATE_FILE",
        create_root="proposed/",
        outcome_claim_ids=(claim.id,),
    )
    plan = PlanRevision(
        objective_id="obj_1",
        cycle_id="cyc_1",
        revision=1,
        intent_id=intent.id,
        understanding_revision_id=rev.id,
        governance_revision_id=gov.id,
        targets=(target,),
        steps=(StepTargetProjection(target_scope=("proposed/index.html",)),),
    )
    attempt = AttemptEvidence(
        plan_id=plan.plan_id,
        step_id="st_1",
        attempt=1,
        changed_files=("proposed/index.html",),
        verifier_verdict="REPLAN",
        reasons=("fail",),
    )
    binding = ApprovalBinding(plan_id=plan.plan_id, ref="appr_1")
    assert intent.version == "v1"
    assert turn.intent_id == intent.id
    assert ev.need_id == need.id
    assert attempt.plan_id == plan.plan_id
    assert binding.plan_id == plan.plan_id
    with pytest.raises(ValidationError):
        UserIntent(objective_id="obj_1", text="x", extra_field=True)  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        intent.text = "mutated"


# ---------------------------------------------------------------------------
# Understanding U1–U8
# ---------------------------------------------------------------------------


def test_u1_scope_must_cite_user_text_only() -> None:
    intent = _intent()
    bad = _claim(
        kind="SCOPE_IN",
        text="src/auth/jwt.py",
        cites=(_cite("EVIDENCE", "ev_1"),),
    )
    rev = UnderstandingRevision(
        objective_id="obj_1", intent_id=intent.id, scope_mode="BOUNDED", claims=(bad,)
    )
    result = validate_understanding_revision(intent=intent, revision=rev)
    assert result.ok is False
    assert result.rule_id == "U1"


def test_u1_empty_cites_rejected() -> None:
    intent = _intent()
    bad = _claim(kind="OUTCOME", text="done", cites=())
    rev = UnderstandingRevision(
        objective_id="obj_1", intent_id=intent.id, scope_mode="OPEN_IMPLEMENTATION", claims=(bad,)
    )
    result = validate_understanding_revision(intent=intent, revision=rev)
    assert result.ok is False
    assert result.rule_id == "U1"


def test_u2_scope_in_path_not_in_user_text() -> None:
    intent = _intent(text="Fix login timeout")
    claim = _claim(
        kind="SCOPE_IN",
        text="src/auth/jwt.py",
        cites=(_cite("INTENT_SPAN", intent.id),),
    )
    rev = UnderstandingRevision(
        objective_id="obj_1", intent_id=intent.id, scope_mode="BOUNDED", claims=(claim,)
    )
    result = validate_understanding_revision(intent=intent, revision=rev)
    assert result.ok is False
    assert result.rule_id == "U2"


def test_u3_governance_create_root_is_not_scope_in() -> None:
    intent = _intent(text="Build a landing page in proposed/")
    gov = _governance(create_root=("proposed/",))
    claim = _claim(
        kind="SCOPE_IN",
        text="proposed/",
        cites=(_cite("INTENT_SPAN", intent.id),),
    )
    rev = UnderstandingRevision(
        objective_id="obj_1", intent_id=intent.id, scope_mode="BOUNDED", claims=(claim,)
    )
    result = validate_understanding_revision(intent=intent, revision=rev, governance=gov)
    assert result.ok is False
    assert result.rule_id == "U3"


def test_u4_situation_must_cite_evidence_and_be_hypothesized() -> None:
    intent = _intent()
    asserted = _claim(
        kind="SITUATION",
        text="jwt.py sets timeout to 30s",
        cites=(_cite("EVIDENCE", "ev_1"),),
        standing="ASSERTED",
    )
    rev = UnderstandingRevision(
        objective_id="obj_1",
        intent_id=intent.id,
        scope_mode="OPEN_IMPLEMENTATION",
        claims=(asserted,),
    )
    result = validate_understanding_revision(intent=intent, revision=rev)
    assert result.ok is False
    assert result.rule_id == "U4"

    wrong_cite = _claim(
        kind="CAUSE",
        text="timeout is too low",
        cites=(_cite("INTENT_SPAN", intent.id),),
        standing="HYPOTHESIZED",
    )
    rev2 = UnderstandingRevision(
        objective_id="obj_1",
        intent_id=intent.id,
        scope_mode="OPEN_IMPLEMENTATION",
        claims=(wrong_cite,),
    )
    result2 = validate_understanding_revision(intent=intent, revision=rev2)
    assert result2.ok is False
    assert result2.rule_id == "U4"


def test_u5_how_language_rejected() -> None:
    intent = _intent()
    claim = _claim(
        kind="OUTCOME",
        text="We should modify src/auth/jwt.py",
        cites=(_cite("INTENT_SPAN", intent.id),),
    )
    rev = UnderstandingRevision(
        objective_id="obj_1", intent_id=intent.id, scope_mode="OPEN_IMPLEMENTATION", claims=(claim,)
    )
    result = validate_understanding_revision(intent=intent, revision=rev)
    assert result.ok is False
    assert result.rule_id == "U5"


def test_u6_active_ambiguity_need_clarification() -> None:
    intent = _intent()
    amb = _claim(
        kind="AMBIGUITY",
        text="HTML page or schema only?",
        cites=(_cite("INTENT_SPAN", intent.id),),
    )
    rev = UnderstandingRevision(
        objective_id="obj_1", intent_id=intent.id, scope_mode="BOUNDED", claims=(amb,)
    )
    result = validate_understanding_revision(intent=intent, revision=rev)
    assert result.ok is False
    assert result.rule_id == "U6"
    assert result.decision == "NEED_CLARIFICATION"


def test_u7_omitted_active_user_claim_rejected() -> None:
    intent = _intent(text="Only the MySQL schema")
    parent_claim = _claim(
        kind="SCOPE_IN",
        text="MySQL schema",
        cites=(_cite("INTENT_SPAN", intent.id),),
        claim_id="cl_schema",
    )
    parent = UnderstandingRevision(
        id="und_v1",
        objective_id="obj_1",
        intent_id=intent.id,
        scope_mode="BOUNDED",
        claims=(parent_claim,),
    )
    replacement = _claim(
        kind="SCOPE_IN",
        text="PHP API",
        cites=(_cite("USER_TURN", "turn_2"),),
        claim_id="cl_php",
    )
    child = UnderstandingRevision(
        id="und_v2",
        objective_id="obj_1",
        intent_id=intent.id,
        parent_revision_id=parent.id,
        scope_mode="BOUNDED",
        claims=(replacement,),
    )
    result = validate_understanding_revision(intent=intent, revision=child, parent=parent)
    assert result.ok is False
    assert result.rule_id == "U7"


def test_u7_extend_keeps_parent_active() -> None:
    intent = _intent(text="Only the MySQL schema")
    parent_claim = _claim(
        kind="SCOPE_IN",
        text="MySQL schema",
        cites=(_cite("INTENT_SPAN", intent.id),),
        claim_id="cl_schema",
    )
    parent = UnderstandingRevision(
        id="und_v1",
        objective_id="obj_1",
        intent_id=intent.id,
        scope_mode="BOUNDED",
        claims=(parent_claim,),
    )
    added = _claim(
        kind="SCOPE_IN",
        text="PHP API",
        cites=(_cite("USER_TURN", "turn_2"),),
        claim_id="cl_php",
    )
    child = UnderstandingRevision(
        id="und_v2",
        objective_id="obj_1",
        intent_id=intent.id,
        parent_revision_id=parent.id,
        scope_mode="BOUNDED",
        claims=(parent_claim, added),
    )
    turn = UserTurn(id="turn_2", objective_id="obj_1", intent_id=intent.id, text="Also build PHP API.")
    result = validate_understanding_revision(
        intent=intent, revision=child, parent=parent, turns=(turn,)
    )
    assert result.ok is True


def test_u7_replace_requires_superseded_copy() -> None:
    intent = _intent(text="Only the MySQL schema")
    parent_claim = _claim(
        kind="SCOPE_IN",
        text="MySQL schema",
        cites=(_cite("INTENT_SPAN", intent.id),),
        claim_id="cl_schema",
    )
    parent = UnderstandingRevision(
        id="und_v1",
        objective_id="obj_1",
        intent_id=intent.id,
        scope_mode="BOUNDED",
        claims=(parent_claim,),
    )
    superseded = _claim(
        kind="SCOPE_IN",
        text="MySQL schema",
        cites=(_cite("INTENT_SPAN", intent.id),),
        claim_id="cl_schema",
        status="SUPERSEDED",
    )
    replacement = _claim(
        kind="SCOPE_IN",
        text="PHP API",
        cites=(_cite("USER_TURN", "turn_2"),),
        claim_id="cl_php",
    )
    child = UnderstandingRevision(
        id="und_v2",
        objective_id="obj_1",
        intent_id=intent.id,
        parent_revision_id=parent.id,
        scope_mode="BOUNDED",
        claims=(superseded, replacement),
    )
    turn = UserTurn(id="turn_2", objective_id="obj_1", intent_id=intent.id, text="Also build PHP API.")
    result = validate_understanding_revision(
        intent=intent, revision=child, parent=parent, turns=(turn,)
    )
    assert result.ok is True


def test_u8_missing_info_must_cite_claim() -> None:
    intent = _intent()
    outcome = _claim(
        kind="OUTCOME",
        text="Login timeout is fixed",
        cites=(_cite("INTENT_SPAN", intent.id),),
        claim_id="cl_outcome",
    )
    bad = _claim(
        kind="MISSING_INFO",
        text="Where is timeout?",
        cites=(_cite("EVIDENCE", "ev_1"),),
    )
    rev = UnderstandingRevision(
        objective_id="obj_1",
        intent_id=intent.id,
        scope_mode="OPEN_IMPLEMENTATION",
        claims=(outcome, bad),
    )
    result = validate_understanding_revision(intent=intent, revision=rev)
    assert result.ok is False
    assert result.rule_id == "U8"

    good = _claim(
        kind="MISSING_INFO",
        text="Where is timeout?",
        cites=(_cite("CLAIM", outcome.id),),
    )
    rev2 = UnderstandingRevision(
        objective_id="obj_1",
        intent_id=intent.id,
        scope_mode="OPEN_IMPLEMENTATION",
        claims=(outcome, good),
    )
    result2 = validate_understanding_revision(intent=intent, revision=rev2)
    assert result2.ok is True
    assert result2.rule_id == "U1"


# ---------------------------------------------------------------------------
# Needs N1–N5
# ---------------------------------------------------------------------------


def test_n1_need_requires_active_missing_info_with_claim_cite() -> None:
    intent, understanding, need, _evidence = _chain()
    assert validate_need(need=need, understanding=understanding).ok is True
    empty = need.model_copy(update={"claim_ids": (understanding.claims[0].id,)})
    result = validate_need(need=empty, understanding=understanding)
    assert result.ok is False
    assert result.rule_id == "N1"


def test_n2_source_and_result_type_enums() -> None:
    with pytest.raises(ValidationError):
        ExplorationNeed(
            objective_id="obj_1",
            understanding_revision_id="und_1",
            claim_ids=("cl_1",),
            purpose="p",
            question="q",
            source="census",  # type: ignore[arg-type]
            result_type="file_content",
        )
    with pytest.raises(ValidationError):
        ExplorationNeed(
            objective_id="obj_1",
            understanding_revision_id="und_1",
            claim_ids=("cl_1",),
            purpose="p",
            question="q",
            source="repository",
            result_type="glob_dump",  # type: ignore[arg-type]
        )


def test_n3_evidence_count_must_not_exceed_budget() -> None:
    intent, understanding, need, (jwt,) = _chain()
    del intent, understanding
    extras = tuple(
        jwt.model_copy(update={"id": f"ev_{i}", "location": f"file_{i}.py"})
        for i in range(need.budget_files)
    )
    result = validate_need_evidence(need=need, evidence=(jwt, *extras))
    assert result.ok is False
    assert result.rule_id == "N3"


def test_n4_evidence_need_id_must_exist() -> None:
    _intent, _und, need, (jwt,) = _chain()
    orphan = jwt.model_copy(update={"need_id": "need_missing"})
    result = validate_need_evidence(need=need, evidence=(orphan,))
    assert result.ok is False
    assert result.rule_id == "N4"


def test_n5_discovery_and_glob_are_forbidden_explore_sources() -> None:
    assert validate_explore_gate_sources(("search_files",)).ok is True
    bad = validate_explore_gate_sources(("run_repository_discovery",))
    assert bad.ok is False
    assert bad.rule_id == "N5"
    globbed = validate_explore_gate_sources(("glob_fallback",))
    assert globbed.ok is False
    assert globbed.rule_id == "N5"
    disc = validate_explore_gate_sources(("backend/aix_core/runtime/objective/discovery.py",))
    assert disc.ok is False
    assert disc.rule_id == "N5"


# ---------------------------------------------------------------------------
# Plan P1–P12
# ---------------------------------------------------------------------------


def test_p1_existing_requires_full_provenance_chain() -> None:
    intent, understanding, need, evidence = _chain()
    gov = _governance()
    target = PlanTarget(
        kind="EXISTING",
        path="src/auth/jwt.py",
        effect="LITERAL_REPLACE",
        rationale="change timeout",
        evidence_id=None,
        outcome_claim_ids=("cl_outcome",),
    )
    result = validate_plan_target(
        target=target,
        intent=intent,
        understanding=understanding,
        needs=(need,),
        evidence=evidence,
        governance=gov,
    )
    assert result.ok is False
    assert result.rule_id == "P1"


def test_p2_existing_path_must_equal_evidence_location_exactly() -> None:
    intent, understanding, need, (jwt,) = _chain()
    census = jwt.model_copy(
        update={"id": "ev_census", "location": "backend/.tmp/cycle/schema.sql"}
    )
    target = _existing_target(evidence_id=census.id, path="schema.sql")
    result = validate_plan_target(
        target=target,
        intent=intent,
        understanding=understanding,
        needs=(need,),
        evidence=(census,),
        governance=_governance(),
    )
    assert result.ok is False
    assert result.rule_id == "P2"


def test_p3_uncited_evidence_is_unused_not_a_target() -> None:
    intent, understanding, need, evidence = _chain()
    extra = ExplorationEvidence(
        id="ev_readme",
        need_id=need.id,
        objective_id="obj_1",
        source="repository",
        location="README.md",
        observation="mentions login",
        exists=True,
        provenance=_prov(),
    )
    gov = _governance()
    target = _existing_target(evidence_id="ev_jwt")
    plan = _plan(
        intent=intent,
        understanding=understanding,
        governance=gov,
        targets=(target,),
    )
    result = validate_plan_revision(
        plan=plan,
        intent=intent,
        understanding=understanding,
        needs=(need,),
        evidence=(evidence[0], extra),
        governance=gov,
    )
    assert result.ok is True
    assert {t.path for t in plan.targets} == {"src/auth/jwt.py"}
    assert extra.location == "README.md"


def test_p4_create_root_component_containment() -> None:
    assert create_root_contains(target="proposed/index.html", root="proposed/") is True
    assert create_root_contains(target="proposed/", root="proposed/") is True
    assert create_root_contains(target="proposed-malicious/file", root="proposed/") is False
    assert create_root_contains(target="proposed/../outside/file", root="proposed/") is False
    assert create_root_contains(target="proposed/./index.html", root="proposed/") is True

    intent = _intent(text="Build a landing page")
    outcome = _claim(
        kind="OUTCOME",
        text="Landing page exists",
        cites=(_cite("INTENT_SPAN", intent.id),),
        claim_id="cl_outcome",
    )
    understanding = UnderstandingRevision(
        objective_id="obj_1",
        intent_id=intent.id,
        scope_mode="OPEN_IMPLEMENTATION",
        claims=(outcome,),
    )
    gov = _governance(create_root=("proposed/",), create_capability=("CREATE_FILE",))

    def _create(path: str) -> PlanTarget:
        return PlanTarget(
            kind="CREATE",
            path=path,
            effect="LITERAL_REPLACE",
            rationale="new file",
            create_capability="CREATE_FILE",
            create_root="proposed/",
            outcome_claim_ids=("cl_outcome",),
        )

    kwargs = dict(intent=intent, understanding=understanding, needs=(), evidence=(), governance=gov)
    ok = validate_plan_target(target=_create("proposed/index.html"), **kwargs)
    assert ok.ok is True
    mal = validate_plan_target(target=_create("proposed-malicious/x.html"), **kwargs)
    assert mal.ok is False and mal.rule_id == "P4"
    escape = validate_plan_target(target=_create("proposed/../outside/x.html"), **kwargs)
    assert escape.ok is False and escape.rule_id == "P4"


def test_p5_create_must_not_mint_scope_in() -> None:
    intent = _intent(text="Build a landing page")
    outcome = _claim(
        kind="OUTCOME",
        text="Landing page exists",
        cites=(_cite("INTENT_SPAN", intent.id),),
        claim_id="cl_outcome",
    )
    minted = _claim(
        kind="SCOPE_IN",
        text="proposed/index.html",
        cites=(_cite("EVIDENCE", "ev_1"),),
    )
    understanding = UnderstandingRevision(
        objective_id="obj_1",
        intent_id=intent.id,
        scope_mode="OPEN_IMPLEMENTATION",
        claims=(outcome, minted),
    )
    target = PlanTarget(
        kind="CREATE",
        path="proposed/index.html",
        effect="LITERAL_REPLACE",
        rationale="new file",
        create_capability="CREATE_FILE",
        create_root="proposed/",
        outcome_claim_ids=("cl_outcome",),
    )
    result = validate_plan_target(
        target=target,
        intent=intent,
        understanding=understanding,
        needs=(),
        evidence=(),
        governance=_governance(create_capability=("CREATE_FILE",)),
    )
    assert result.ok is False
    assert result.rule_id == "P5"


def test_p6_target_requires_active_outcome() -> None:
    intent, understanding, need, evidence = _chain()
    target = PlanTarget(
        kind="EXISTING",
        path="src/auth/jwt.py",
        effect="LITERAL_REPLACE",
        rationale="change timeout",
        evidence_id="ev_jwt",
        outcome_claim_ids=(),
    )
    result = validate_plan_target(
        target=target,
        intent=intent,
        understanding=understanding,
        needs=(need,),
        evidence=evidence,
        governance=_governance(),
    )
    assert result.ok is False
    assert result.rule_id == "P6"


def test_p7_bounded_must_not_exceed_user_named_scope() -> None:
    intent = _intent(text="Change only src/auth/jwt.py")
    outcome = _claim(
        kind="OUTCOME",
        text="timeout fixed",
        cites=(_cite("INTENT_SPAN", intent.id),),
        claim_id="cl_outcome",
    )
    scope = _claim(
        kind="SCOPE_IN",
        text="src/auth/jwt.py",
        cites=(_cite("INTENT_SPAN", intent.id),),
    )
    out = _claim(
        kind="SCOPE_OUT",
        text="tests/",
        cites=(_cite("INTENT_SPAN", intent.id),),
    )
    missing = _claim(
        kind="MISSING_INFO",
        text="where?",
        cites=(_cite("CLAIM", outcome.id),),
        claim_id="cl_missing",
    )
    understanding = UnderstandingRevision(
        objective_id="obj_1",
        intent_id=intent.id,
        scope_mode="BOUNDED",
        claims=(outcome, scope, out, missing),
    )
    need = ExplorationNeed(
        objective_id="obj_1",
        understanding_revision_id=understanding.id,
        claim_ids=(missing.id,),
        purpose="locate",
        question="where",
        source="repository",
        result_type="file_content",
    )
    other = ExplorationEvidence(
        id="ev_other",
        need_id=need.id,
        objective_id="obj_1",
        source="repository",
        location="src/other.py",
        observation="unrelated",
        exists=True,
        provenance=_prov(),
    )
    target = _existing_target(evidence_id="ev_other", path="src/other.py")
    result = validate_plan_target(
        target=target,
        intent=intent,
        understanding=understanding,
        needs=(need,),
        evidence=(other,),
        governance=_governance(),
    )
    assert result.ok is False
    assert result.rule_id == "P7"


def test_p8_provenance_valid_is_not_plan_correct() -> None:
    """Citing docker-compose from a timeout Need is substantively questionable and still accepted."""
    compose = ExplorationEvidence(
        id="ev_compose",
        need_id="need_placeholder",
        objective_id="obj_1",
        source="repository",
        location="docker-compose.yml",
        observation="compose mentions a timeout env var",
        exists=True,
        provenance=_prov(),
    )
    intent, understanding, need, _jwt = _chain()
    compose = compose.model_copy(update={"need_id": need.id})
    target = _existing_target(evidence_id=compose.id, path="docker-compose.yml")
    gov = _governance()
    plan = _plan(
        intent=intent,
        understanding=understanding,
        governance=gov,
        targets=(target,),
        steps=(StepTargetProjection(target_scope=("docker-compose.yml",)),),
    )
    result = validate_plan_revision(
        plan=plan,
        intent=intent,
        understanding=understanding,
        needs=(need,),
        evidence=(compose,),
        governance=gov,
    )
    assert result.ok is True
    assert result.rule_id == "P8"
    assert result.decision == "ACCEPT"


def test_p9_effect_must_be_required_effect_kind_only() -> None:
    rejected = assert_effect_is_required_effect_kind("refactor authentication architecture")
    assert rejected.ok is False
    assert rejected.rule_id == "P9"
    with pytest.raises(ValidationError):
        PlanTarget(
            kind="EXISTING",
            path="src/auth/jwt.py",
            effect="MODIFY_FILE",  # type: ignore[arg-type]
            rationale="free text belongs in rationale",
            evidence_id="ev_jwt",
            outcome_claim_ids=("cl_outcome",),
        )
    assert assert_effect_is_required_effect_kind("LITERAL_REPLACE").ok is True

    intent, understanding, need, evidence = _chain()
    target = _existing_target(evidence_id="ev_jwt")
    gov = _governance(forbidden_effects=("LITERAL_REPLACE",))
    result = validate_plan_target(
        target=target,
        intent=intent,
        understanding=understanding,
        needs=(need,),
        evidence=evidence,
        governance=gov,
    )
    assert result.ok is False
    assert result.rule_id == "P9"


def test_p10_system_enriched_transformations_rejected() -> None:
    intent, understanding, need, evidence = _chain()
    gov = _governance()
    plan = _plan(
        intent=intent,
        understanding=understanding,
        governance=gov,
        targets=(_existing_target(evidence_id="ev_jwt"),),
    )
    result = validate_plan_revision(
        plan=plan,
        intent=intent,
        understanding=understanding,
        needs=(need,),
        evidence=evidence,
        governance=gov,
        transformations=({"kind": "SYSTEM_ENRICHED", "op": "infer_required_effects"},),
    )
    assert result.ok is False
    assert result.rule_id == "P10"


def test_p11_step_target_scope_must_be_subset_of_plan_targets() -> None:
    intent, understanding, need, evidence = _chain()
    gov = _governance()
    plan = _plan(
        intent=intent,
        understanding=understanding,
        governance=gov,
        targets=(_existing_target(evidence_id="ev_jwt"),),
        steps=(StepTargetProjection(target_scope=("src/auth/jwt.py", "src/extra.py")),),
    )
    result = validate_plan_revision(
        plan=plan,
        intent=intent,
        understanding=understanding,
        needs=(need,),
        evidence=evidence,
        governance=gov,
    )
    assert result.ok is False
    assert result.rule_id == "P11"


def test_p12_target_derived_forbidden() -> None:
    assert validate_target_kind("EXISTING").ok is True
    assert validate_target_kind("CREATE").ok is True
    derived = validate_target_kind("TARGET_DERIVED")
    assert derived.ok is False
    assert derived.rule_id == "P12"
    with pytest.raises(ValidationError):
        PlanTarget(
            kind="DERIVED",  # type: ignore[arg-type]
            path="tests/test_auth.py",
            effect="LITERAL_REPLACE",
            rationale="derived from basename",
            outcome_claim_ids=("cl_outcome",),
        )


# ---------------------------------------------------------------------------
# Approval A1–A6
# ---------------------------------------------------------------------------


def test_a1_approval_bound_to_exact_plan_id() -> None:
    intent, understanding, _need, _evidence = _chain()
    gov = _governance()
    plan = _plan(
        intent=intent,
        understanding=understanding,
        governance=gov,
        targets=(_existing_target(evidence_id="ev_jwt"),),
        approval_ref="appr_this",
        plan_id="plan_v1",
    )
    matching = validate_approval_for_execute(
        plan=plan,
        governance=gov,
        approval=ApprovalBinding(plan_id="plan_v1", ref="appr_this"),
    )
    assert matching.ok is True
    wrong_plan = validate_approval_for_execute(
        plan=plan,
        governance=gov,
        approval=ApprovalBinding(plan_id="plan_other", ref="appr_this"),
    )
    assert wrong_plan.ok is False
    assert wrong_plan.rule_id == "A1"
    assert wrong_plan.decision == "NEED_APPROVAL"
    wrong_ref = validate_approval_for_execute(
        plan=plan,
        governance=gov,
        approval=ApprovalBinding(plan_id="plan_v1", ref="appr_other"),
    )
    assert wrong_ref.ok is False
    assert wrong_ref.rule_id == "A1"
    assert wrong_ref.decision == "NEED_APPROVAL"


def test_a2_plan_v1_approval_does_not_authorize_v2() -> None:
    intent, understanding, _need, _evidence = _chain()
    gov = _governance()
    plan_v2 = _plan(
        intent=intent,
        understanding=understanding,
        governance=gov,
        targets=(_existing_target(evidence_id="ev_jwt"),),
        revision=2,
        plan_id="plan_v2",
    )
    result = validate_approval_for_execute(
        plan=plan_v2,
        governance=gov,
        approval=ApprovalBinding(plan_id="plan_v1", ref="appr_v1"),
    )
    assert result.ok is False
    assert result.rule_id == "A2"
    assert result.decision == "NEED_APPROVAL"


def test_a3_every_replan_and_always_need_new_approval() -> None:
    intent, understanding, _need, _evidence = _chain()
    plan = _plan(
        intent=intent,
        understanding=understanding,
        governance=_governance(),
        targets=(_existing_target(evidence_id="ev_jwt"),),
        revision=1,
        plan_id="plan_v1",
    )
    always = _governance(approval_policy="ALWAYS")
    missing = validate_approval_for_execute(plan=plan, governance=always, approval=None)
    assert missing.ok is False
    assert missing.rule_id == "A3"
    present = validate_approval_for_execute(
        plan=plan,
        governance=always,
        approval=ApprovalBinding(plan_id="plan_v1", ref="appr_v1"),
    )
    assert present.ok is True


def test_a4_initial_plan_only_v2_still_need_approval() -> None:
    intent, understanding, _need, _evidence = _chain()
    gov = _governance(approval_policy="INITIAL_PLAN_ONLY")
    v1 = _plan(
        intent=intent,
        understanding=understanding,
        governance=gov,
        targets=(_existing_target(evidence_id="ev_jwt"),),
        revision=1,
        plan_id="plan_v1",
    )
    no_v1 = validate_approval_for_execute(plan=v1, governance=gov, approval=None)
    assert no_v1.ok is False
    assert no_v1.rule_id == "A4"
    yes_v1 = validate_approval_for_execute(
        plan=v1,
        governance=gov,
        approval=ApprovalBinding(plan_id="plan_v1", ref="appr_v1"),
    )
    assert yes_v1.ok is True

    v2 = _plan(
        intent=intent,
        understanding=understanding,
        governance=gov,
        targets=(_existing_target(evidence_id="ev_jwt"),),
        revision=2,
        plan_id="plan_v2",
    )
    no_v2 = validate_approval_for_execute(plan=v2, governance=gov, approval=None)
    assert no_v2.ok is False
    assert no_v2.rule_id == "A4"
    own_v2 = validate_approval_for_execute(
        plan=v2,
        governance=gov,
        approval=ApprovalBinding(plan_id="plan_v2", ref="appr_v2"),
    )
    assert own_v2.ok is True


def test_a5_never_skips_human_approval() -> None:
    intent, understanding, _need, _evidence = _chain()
    gov = _governance(approval_policy="NEVER")
    plan = _plan(
        intent=intent,
        understanding=understanding,
        governance=gov,
        targets=(_existing_target(evidence_id="ev_jwt"),),
        revision=2,
        plan_id="plan_v2",
    )
    result = validate_approval_for_execute(plan=plan, governance=gov, approval=None)
    assert result.ok is True
    assert result.rule_id == "A5"


def test_a6_auto_replan_still_runs_approval_policy() -> None:
    intent, understanding, _need, _evidence = _chain()
    gov = _governance()
    v2 = _plan(
        intent=intent,
        understanding=understanding,
        governance=gov,
        targets=(_existing_target(evidence_id="ev_jwt"),),
        revision=2,
        plan_id="plan_v2",
    )
    missing = validate_approval_for_execute(
        plan=v2, governance=gov, approval=None, auto_replan=True
    )
    assert missing.ok is False
    assert missing.rule_id == "A6"
    stolen = validate_approval_for_execute(
        plan=v2,
        governance=gov,
        approval=ApprovalBinding(plan_id="plan_v1", ref="appr_v1"),
        auto_replan=True,
    )
    assert stolen.ok is False
    assert stolen.rule_id == "A6"


# ---------------------------------------------------------------------------
# Execute E1–E3
# ---------------------------------------------------------------------------


def test_e1_diff_must_be_subset_of_plan_targets() -> None:
    intent, understanding, _need, _evidence = _chain()
    plan = _plan(
        intent=intent,
        understanding=understanding,
        governance=_governance(),
        targets=(_existing_target(evidence_id="ev_jwt"),),
    )
    assert validate_execute_diff(changed_files=("src/auth/jwt.py",), plan=plan).ok is True
    extra = validate_execute_diff(
        changed_files=("src/auth/jwt.py", "src/leaked.py"), plan=plan
    )
    assert extra.ok is False
    assert extra.rule_id == "E1"


def test_e2_reunderstand_requires_new_revision_without_facet_scope() -> None:
    intent = _intent()
    user_scope = _claim(
        kind="SCOPE_IN",
        text="src/auth/jwt.py",
        cites=(_cite("INTENT_SPAN", intent.id),),
    )
    new = UnderstandingRevision(
        objective_id="obj_1",
        intent_id=intent.id,
        scope_mode="BOUNDED",
        claims=(user_scope,),
    )
    assert validate_reunderstand(
        previous_understanding_id="und_old", new_understanding=new
    ).ok is True
    same = validate_reunderstand(previous_understanding_id=new.id, new_understanding=new)
    assert same.ok is False
    assert same.rule_id == "E2"
    facet = _claim(
        kind="SCOPE_IN",
        text="Frontend",
        cites=(_cite("EVIDENCE", "facet_dod"),),
    )
    bad = UnderstandingRevision(
        objective_id="obj_1",
        intent_id=intent.id,
        scope_mode="BOUNDED",
        claims=(facet,),
    )
    facet_result = validate_reunderstand(
        previous_understanding_id="und_old", new_understanding=bad
    )
    assert facet_result.ok is False
    assert facet_result.rule_id == "E2"


def test_e3_replan_requires_new_revision_with_full_refs() -> None:
    intent, understanding, _need, _evidence = _chain()
    v1 = _plan(
        intent=intent,
        understanding=understanding,
        governance=_governance(),
        targets=(_existing_target(evidence_id="ev_jwt"),),
        revision=1,
    )
    assert validate_replan_revision(plan=v1).ok is False
    v2 = _plan(
        intent=intent,
        understanding=understanding,
        governance=_governance(),
        targets=(_existing_target(evidence_id="ev_jwt"),),
        revision=2,
    )
    assert validate_replan_revision(plan=v2).ok is True
    assert validate_replan_revision(plan=v2).rule_id == "E3"


# ---------------------------------------------------------------------------
# Isolation / purity / no live wiring
# ---------------------------------------------------------------------------


def test_p0_validators_are_pure() -> None:
    source = (_PROVENANCE_DIR / "validators.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    forbidden_calls = {"open", "write_text", "write_bytes", "Popen", "run", "urlopen"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith(("httpx", "requests", "subprocess", "socket"))
        if isinstance(node, ast.ImportFrom) and node.module:
            assert not node.module.startswith(("httpx", "requests", "subprocess"))
            assert node.module != "backend.aix_core.runtime.objective.discovery"
            assert node.module != "backend.aix_core.runtime.objective.provenance.explore"
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in forbidden_calls


def test_p0_contracts_and_validators_do_not_import_explore() -> None:
    """P0 modules stay Explore-free. P3 owns provenance/explore.py."""
    for name in ("contracts.py", "validators.py", "understanding.py", "admit.py"):
        source = (_PROVENANCE_DIR / name).read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert "explore" not in node.module
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "explore" not in alias.name


def test_p0_no_production_wiring_or_flag() -> None:
    # Flag stays absent. P4 wires PlanRequest into the planner; executor/capsule/replan stay unwired.
    dab = (_BACKEND_ROOT / "aix_core" / "runtime" / "objective" / "durable_api_backend.py").read_text(
        encoding="utf-8"
    )
    assert "OBJECTIVE_PROVENANCE_ENABLED" not in dab
    still_unwired = [
        _BACKEND_ROOT / "aix_core" / "runtime" / "objective" / "discovery.py",
        _BACKEND_ROOT / "aix_core" / "runtime" / "executor" / "real_execute.py",
        _BACKEND_ROOT / "aix_core" / "runtime" / "planner" / "incremental_replan.py",
        _BACKEND_ROOT / "aix_core" / "runtime" / "recovery" / "replan.py",
    ]
    needle = "aix_core.runtime.objective.provenance"
    for path in still_unwired:
        text = path.read_text(encoding="utf-8")
        assert needle not in text
        assert "OBJECTIVE_PROVENANCE_ENABLED" not in text
    production_py = [
        p
        for p in _BACKEND_ROOT.rglob("*.py")
        if "tests" not in p.parts
    ]
    enabled = [
        str(p)
        for p in production_py
        if "OBJECTIVE_PROVENANCE_ENABLED" in p.read_text(encoding="utf-8")
    ]
    assert enabled == []
