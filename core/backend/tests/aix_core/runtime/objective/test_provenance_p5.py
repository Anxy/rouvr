"""P5: accepted PlanTargets are the sole execution target authority.

Capsule = Intent + cited claims + cited Evidence + accepted targets.
allowed_files / obligations / observed_problem cannot authorize execute.
Approval binds the exact PlanRevision. REUNDERSTAND/REPLAN mint new revisions.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from types import SimpleNamespace

from backend.aix_core.intelligence.context.step_context_capsule import build_step_context_capsule
from backend.aix_core.runtime.executor.executor_package import build_executor_package
from backend.aix_core.runtime.executor.real_execute import resolve_empty_directory_marker_path
from backend.aix_core.runtime.objective.contracts import (
    BudgetV1,
    StepContractV1,
    UnderstandingEvidenceV1,
)
from backend.aix_core.runtime.objective.durable_api_backend import (
    DurableObjectiveCycleApiBackend,
    _CycleRecord,
    _ObjectiveRecord,
)
from backend.aix_core.runtime.objective.provenance.admit import freeze_user_intent
from backend.aix_core.runtime.objective.provenance.contracts import (
    ClaimCite,
    EvidenceProvenance,
    ExplorationEvidence,
    ExplorationNeed,
    PlanTarget,
    UnderstandingClaim,
    UnderstandingRevision,
)
from backend.aix_core.runtime.objective.provenance.explore import needs_from_understanding
from backend.aix_core.runtime.objective.provenance.plan_request import (
    build_plan_request,
    construct_plan_revision,
    default_governance,
)
from backend.aix_core.runtime.objective.provenance.understanding import build_understanding_v1
from backend.aix_core.runtime.objective.provenance.validators import (
    validate_approval_for_execute,
    validate_execute_diff,
    validate_plan_target,
    validate_reunderstand,
)
from backend.aix_core.runtime.objective.world_state import build_world_state
from backend.aix_core.runtime.verification.semantic_verifier import SemanticVerificationOutcome

_PROVENANCE_DIR = (
    Path(__file__).resolve().parents[4] / "aix_core" / "runtime" / "objective" / "provenance"
)
_BACKEND = Path(__file__).resolve().parents[4] / "aix_core" / "runtime" / "objective" / "durable_api_backend.py"
_VALIDATORS = _PROVENANCE_DIR / "validators.py"


def _budget() -> BudgetV1:
    return BudgetV1(time_seconds=60, tokens=1000, cost_usd=1.0, retries=1, steps=4, concurrency=1)


def _cite(kind: str, ref: str) -> ClaimCite:
    return ClaimCite(kind=kind, ref=ref)  # type: ignore[arg-type]


def _function_source(name: str) -> str:
    text = _BACKEND.read_text(encoding="utf-8")
    match = re.search(rf"    (?:async )?def {name}\(.*?(?=\n    (?:async )?def |\nclass |\Z)", text, re.S)
    assert match is not None, f"function {name} not found"
    return match.group(0)


def _obj(
    *,
    objective_id: str = "obj_p5",
    objective: str = "Fix login timeout in src/auth/jwt.py",
    allowed_files: tuple[str, ...] = (),
) -> _ObjectiveRecord:
    return _ObjectiveRecord(
        objective_id=objective_id,
        tenant_id="t1",
        workhub_task_id="wh-p5",
        objective=objective,
        task_class="code_change",
        definition_of_done=("timeout is fixed",),
        repository_id="repo-1",
        allowed_files=allowed_files,
        original_objective=objective,
    )


def _step(*, target_scope: tuple[str, ...]) -> StepContractV1:
    return StepContractV1(
        cycle_id="cyc_p5",
        tenant_id="t1",
        step_index=0,
        purpose="edit",
        target_scope=target_scope,
        expected_output_schema_ref="aix.step_contract.v1.generic_edit",
        acceptance_criteria=("done",),
        validation_commands=("echo ok",),
        assigned_role="executor",
        step_budget=_budget(),
        obligation_refs=(),
        status="PENDING",
    )


def _chain(*, objective_id: str = "obj_p5", intent_text: str = "Fix login timeout in src/auth/jwt.py"):
    intent = freeze_user_intent(objective_id=objective_id, text=intent_text)
    outcome = UnderstandingClaim(
        id="cl_outcome",
        kind="OUTCOME",
        text="Login timeout is fixed",
        standing="ASSERTED",
        status="ACTIVE",
        cites=(_cite("INTENT_SPAN", intent.id),),
    )
    missing = UnderstandingClaim(
        id="cl_missing",
        kind="MISSING_INFO",
        text="Where is the timeout implemented?",
        standing="ASSERTED",
        status="ACTIVE",
        cites=(_cite("CLAIM", outcome.id),),
    )
    understanding = UnderstandingRevision(
        objective_id=objective_id,
        intent_id=intent.id,
        scope_mode="OPEN_IMPLEMENTATION",
        claims=(outcome, missing),
    )
    need = ExplorationNeed(
        objective_id=objective_id,
        understanding_revision_id=understanding.id,
        claim_ids=(missing.id,),
        purpose="locate timeout",
        question="Where is login timeout configured?",
        source="repository",
        result_type="file_content",
        budget_files=8,
    )
    evidence = ExplorationEvidence(
        id="ev_jwt",
        need_id=need.id,
        objective_id=objective_id,
        source="repository",
        location="src/auth/jwt.py",
        observation="timeout constant is 30s",
        exists=True,
        provenance=EvidenceProvenance(tool="search_files", query="auth", fingerprint="fp_auth"),
    )
    gov = default_governance(objective_id=objective_id, task_class="code_change", budgets=_budget())
    return intent, understanding, need, evidence, gov


def _existing() -> PlanTarget:
    return PlanTarget(
        kind="EXISTING",
        path="src/auth/jwt.py",
        effect="LITERAL_REPLACE",
        rationale="change the timeout constant",
        evidence_id="ev_jwt",
        outcome_claim_ids=("cl_outcome",),
    )


def _accept_plan(backend: DurableObjectiveCycleApiBackend, obj: _ObjectiveRecord, *, revision: int = 1):
    intent, understanding, need, evidence, gov = _chain(
        objective_id=obj.objective_id, intent_text=backend._intent_text(obj)
    )
    backend._provenance.put_intent(intent)
    backend._provenance.put_understanding(understanding)
    backend._provenance.put_need(need)
    backend._provenance.put_evidence(evidence)
    backend._provenance.put_governance(gov)
    request = build_plan_request(
        cycle_id="cyc_p5",
        intent=intent,
        understanding=understanding,
        evidence=(evidence,),
        governance=gov,
    )
    construction = construct_plan_revision(
        request=request,
        intent=intent,
        understanding=understanding,
        needs=(need,),
        evidence=(evidence,),
        governance=gov,
        targets=(_existing(),),
        step_scopes=(("src/auth/jwt.py",),),
        revision=revision,
        cycle_id="cyc_p5",
    )
    assert construction.validation.ok, construction.validation.detail
    accepted = construction.plan.model_copy(update={"status": "ACCEPTED"})
    backend._provenance.put_plan(accepted)
    return intent, understanding, need, evidence, gov, accepted


def test_seed_marker_is_intent_bootstrap_not_allowed_files_authority() -> None:
    """allowed_files → CREATE_DIRECTORY → .gitkeep is bootstrap metadata, not a PlanTarget."""
    resolver_prefers_allowed = resolve_empty_directory_marker_path(
        objective="Create an empty directory named my-web-app",
        allowed_files=("other-app/.gitkeep",),
    )
    assert resolver_prefers_allowed == "other-app/.gitkeep"

    src = _function_source("_seed_create_directory_contract")
    assert "allowed_files=()" in src
    assert "allowed_files=obj.allowed_files" not in src

    backend = DurableObjectiveCycleApiBackend(require_workhub_load=False)
    obj = _obj(
        objective_id="obj_seed",
        objective="Create an empty directory named my-web-app",
        allowed_files=("other-app/.gitkeep",),
    )
    backend._ensure_frozen_intent(obj)
    cycle = _CycleRecord(
        cycle_id="cyc_seed",
        objective_id=obj.objective_id,
        tenant_id="t1",
        candidate_sha="abc",
        budgets={},
    )
    backend._seed_create_directory_contract(obj=obj, cycle=cycle, envelope=None)
    contract = backend._step_contracts[obj.objective_id]
    assert contract.target_scope == ("my-web-app/.gitkeep",)
    assert backend._accepted_plan_target_paths(obj) is None
    assert backend._execution_scope_paths(obj, contract) == ("my-web-app/.gitkeep",)


def test_seed_without_intent_name_does_not_use_allowed_files() -> None:
    backend = DurableObjectiveCycleApiBackend(require_workhub_load=False)
    obj = _obj(
        objective_id="obj_seed_noname",
        objective="Create an empty directory",
        allowed_files=("other-app/.gitkeep",),
    )
    backend._ensure_frozen_intent(obj)
    cycle = _CycleRecord(
        cycle_id="cyc_seed_noname",
        objective_id=obj.objective_id,
        tenant_id="t1",
        candidate_sha="abc",
        budgets={},
    )
    backend._seed_create_directory_contract(obj=obj, cycle=cycle, envelope=None)
    assert obj.objective_id not in backend._step_contracts


def test_adv12_heuristic_allowed_files_cannot_authorize_execute() -> None:
    backend = DurableObjectiveCycleApiBackend(require_workhub_load=False)
    obj = _obj(allowed_files=("src/Wrong.php", "CronJobController.php"))
    _intent, _understanding, _need, _evidence, _gov, accepted = _accept_plan(backend, obj)
    step = _step(target_scope=("src/auth/jwt.py", "src/Wrong.php"))
    assert backend._accepted_plan_target_paths(obj) == ("src/auth/jwt.py",)
    assert "src/Wrong.php" not in set(backend._execution_scope_paths(obj, step))
    assert backend._execution_scope_paths(obj, step) == ("src/auth/jwt.py",)
    scope_src = _function_source("_execution_scope_paths")
    assert "allowed_files=obj.allowed_files" not in scope_src
    assert "return obj.allowed_files" not in scope_src
    assert "tuple(obj.allowed_files" not in scope_src
    gate_src = _function_source("_execute_claimed_step")
    assert "plan_target_gate" in gate_src
    assert "allowed_files_gate" not in gate_src
    assert accepted.targets[0].path == "src/auth/jwt.py"


def test_adv11_replan_turn_keeps_or_supersedes_schema_php_needs_evidence() -> None:
    backend = DurableObjectiveCycleApiBackend(require_workhub_load=False)
    original = "Please provide only the schema"
    obj = _obj(objective_id="obj_adv11", objective=original)
    backend._ensure_frozen_intent(obj)
    v1 = backend._refresh_understanding_v1(obj)
    assert v1 is not None
    schema_before = {
        c.id: c.status
        for c in v1.revision.claims
        if c.kind == "SCOPE_IN" and "schema" in c.text.casefold()
    }
    assert schema_before
    backend._append_user_turn_for_objective(obj, "also PHP API")
    intent = backend._provenance.get_intent(obj.objective_id)
    assert intent is not None
    assert intent.text == original
    v2 = backend._provenance.latest_understanding(obj.objective_id)
    assert v2 is not None
    assert v2.id != v1.revision.id
    schema_after = [c for c in v2.claims if c.kind == "SCOPE_IN" and "schema" in c.text.casefold()]
    assert schema_after
    assert all(c.status in {"ACTIVE", "SUPERSEDED"} for c in schema_after)
    outcome = next(c for c in v2.claims if c.kind == "OUTCOME")
    gov = backend._ensure_governance(obj)
    php = PlanTarget(
        kind="EXISTING",
        path="src/api.php",
        effect="LITERAL_REPLACE",
        rationale="add PHP API",
        outcome_claim_ids=(outcome.id,),
    )
    checked = validate_plan_target(
        target=php,
        intent=intent,
        understanding=v2,
        needs=backend._provenance.needs(obj.objective_id),
        evidence=backend._provenance.evidence(obj.objective_id),
        governance=gov,
    )
    assert checked.ok is False
    assert checked.decision == "NEED_EVIDENCE"


def test_adv13_reunderstand_mints_new_revision_and_needs_not_facet_scope() -> None:
    backend = DurableObjectiveCycleApiBackend(require_workhub_load=False)
    obj = _obj(objective_id="obj_adv13", objective="Fix login timeout in src/auth/jwt.py")
    backend._ensure_frozen_intent(obj)
    first = backend._refresh_understanding_v1(obj)
    assert first is not None
    second = backend._refresh_understanding_v1(obj)
    assert second is not None
    e2 = validate_reunderstand(
        previous_understanding_id=first.revision.id,
        new_understanding=second.revision,
    )
    assert e2.ok is True
    assert second.revision.id != first.revision.id
    intent = backend._provenance.get_intent(obj.objective_id)
    assert intent is not None
    needs = needs_from_understanding(second.revision, intent=intent)
    assert all(need.understanding_revision_id == second.revision.id for need in needs)
    assert all(
        {cite.kind for cite in claim.cites} <= {"INTENT_SPAN", "USER_TURN", "CLAIM"}
        for claim in second.revision.claims
        if claim.kind == "SCOPE_IN"
    )
    src = _function_source("_execute_claimed_step")
    assert "validate_reunderstand" in src
    assert "needs_from_understanding" in src
    reunderstand = src[src.index("verdict == \"REUNDERSTAND\"") :]
    assert "derive_obligations" not in reunderstand
    assert "allowed_files" not in reunderstand


def test_adv20_v1_approval_does_not_dispatch_v2() -> None:
    backend = DurableObjectiveCycleApiBackend(require_workhub_load=False)
    obj = _obj(objective_id="obj_adv20")
    _intent, _understanding, _need, _evidence, gov, v1 = _accept_plan(backend, obj, revision=1)
    backend._bind_approval_to_plan(obj, "appr_v1")
    v1_check = backend._approval_for_execute(obj)
    assert v1_check is not None and v1_check.ok is True

    v2 = v1.model_copy(update={"plan_id": "plan_v2", "revision": 2, "approval_ref": None, "status": "ACCEPTED"})
    backend._provenance.put_plan(v2)
    backend._bind_approval_to_plan(obj, "appr_v1")
    v2_check = backend._approval_for_execute(obj)
    assert v2_check is not None
    assert v2_check.ok is False
    assert v2_check.decision == "NEED_APPROVAL"
    stolen = validate_approval_for_execute(
        plan=v2,
        governance=gov,
        approval=backend._approvals.get(v1.plan_id),
    )
    assert stolen.ok is False
    assert backend._provenance_blocks_auto_replan(obj) is True
    src = _function_source("_execute_claimed_step")
    assert "_provenance_blocks_auto_replan" in src
    auto = src[src.index("provenance_blocks_auto") :]
    assert "approved_gate_id = cycle.current_gate_id" in auto.split("if not provenance_blocks_auto")[1].split(
        "Matches replan_from_blocked"
    )[0]


def test_capsule_is_intent_cited_claims_evidence_and_targets() -> None:
    understanding = UnderstandingEvidenceV1(
        objective_id="obj_1",
        cycle_id="cyc_1",
        files_read=("FeeDue.php", "README.md", "src/auth/jwt.py"),
        symbols_found=("getDueFeeStudent",),
        observed_problem="mixed dump",
        evidence_refs=("toolcall://census",),
        understanding_complete=True,
    )
    capsule = build_step_context_capsule(
        step_id="step-1",
        understanding=understanding,
        target_scope=("src/auth/jwt.py",),
        intent_id="intent_1",
        intent_text="Fix login timeout in src/auth/jwt.py",
        cited_claim_ids=("cl_outcome",),
        cited_evidence_ids=("ev_jwt",),
        plan_target_paths=("src/auth/jwt.py",),
        plan_id="plan_1",
    )
    assert capsule.intent_id == "intent_1"
    assert capsule.intent_text == "Fix login timeout in src/auth/jwt.py"
    assert capsule.cited_claim_ids == ("cl_outcome",)
    assert capsule.cited_evidence_ids == ("ev_jwt",)
    assert capsule.plan_target_paths == ("src/auth/jwt.py",)
    assert capsule.relevant_files == ("src/auth/jwt.py",)
    assert "FeeDue.php" not in capsule.relevant_files
    assert "README.md" not in capsule.relevant_files
    assert capsule.understanding_evidence_id is None
    assert not hasattr(capsule, "observed_problem")
    package = build_executor_package(
        cycle_id="cyc_1",
        objective_id="obj_1",
        objective="Fix login timeout in src/auth/jwt.py",
        envelope=None,
        step_contract=None,
        context_capsule=capsule,
        repair_context=None,
        plan_id="plan_1",
        plan_revision=1,
        approval_ref="appr_v1",
        target_paths=("src/auth/jwt.py",),
    )
    assert package.objective_boundary == "Fix login timeout in src/auth/jwt.py"
    assert package.approval_ref == "appr_v1"
    assert package.target_paths == ("src/auth/jwt.py",)


def test_e1_extra_changed_file_fails_verify_and_records_attempt() -> None:
    backend = DurableObjectiveCycleApiBackend(require_workhub_load=False)
    obj = _obj(objective_id="obj_e1")
    _intent, understanding, _need, _evidence, _gov, plan = _accept_plan(backend, obj)
    extra = validate_execute_diff(
        changed_files=("src/auth/jwt.py", "README.md"),
        plan=plan,
    )
    assert extra.ok is False
    assert extra.rule_id == "E1"
    ok_diff = validate_execute_diff(changed_files=("src/auth/jwt.py",), plan=plan)
    assert ok_diff.ok is True

    outcome = SemanticVerificationOutcome(
        verdict="PASS", reasons=(), mechanical_passed=True, semantic_checked=False
    )
    updated, failed = backend._enforce_execute_diff_and_record_attempt(
        obj=obj,
        result=SimpleNamespace(
            changed_source_files=[{"path": "src/auth/jwt.py"}, {"path": "README.md"}]
        ),
        verification_outcome=outcome,
        step_id="step-1",
        attempt=1,
    )
    assert failed is True
    assert updated.verdict == "HALT"
    attempts = backend._provenance.attempts(obj.objective_id)
    assert len(attempts) == 1
    assert attempts[0].plan_id == plan.plan_id
    assert "README.md" in attempts[0].changed_files
    latest = backend._provenance.latest_understanding(obj.objective_id)
    assert latest is not None
    assert latest.id == understanding.id
    assert all(claim.kind != "SITUATION" or "attempt" not in claim.text.casefold() for claim in latest.claims)


def test_world_state_assembles_provenance_refs_not_files_read_dump() -> None:
    state = build_world_state(
        objective_id="obj_1",
        cycle_id="cyc_1",
        objective="Fix login timeout in src/auth/jwt.py",
        obligations=(),
        understanding=UnderstandingEvidenceV1(
            objective_id="obj_1",
            cycle_id="cyc_1",
            files_read=("README.md", "docker-compose.yml"),
            observed_problem="dump",
            evidence_refs=("toolcall://census",),
        ),
        intent_id="intent_1",
        understanding_revision_id="u2",
        plan_id="plan_1",
        evidence_ids=("ev_jwt",),
        attempt_ids=("att_1",),
        provenance_known_facts=("src/auth/jwt.py",),
    )
    assert state.intent_id == "intent_1"
    assert state.plan_id == "plan_1"
    assert state.known_facts == ("src/auth/jwt.py",)
    assert "README.md" not in state.known_facts
    assert state.attempt_ids == ("att_1",)


def test_open_implementation_still_has_no_relevance_or_necessity_validator() -> None:
    source = _VALIDATORS.read_text(encoding="utf-8")
    tree = ast.parse(source)
    names = {node.name.casefold() for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
    assert not any("relevance" in name or "necessity" in name for name in names)
    assert "def validate_relevance" not in source
    assert "def validate_necessity" not in source
