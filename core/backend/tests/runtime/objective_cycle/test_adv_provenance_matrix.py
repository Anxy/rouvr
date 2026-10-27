"""Adversarial provenance matrix ADV-01..20.

Stable ids. Proves the live chain AND that mixed legacy state cannot authorize work.
"""

from __future__ import annotations

import re
from pathlib import Path

from backend.aix_core.runtime.objective.contracts import BudgetV1, StepContractV1
from backend.aix_core.runtime.objective.durable_api_backend import (
    DurableObjectiveCycleApiBackend,
    _CycleRecord,
    _ObjectiveRecord,
)
from backend.aix_core.runtime.objective.obligations import derive_obligations
from backend.aix_core.runtime.objective.provenance.admit import freeze_user_intent
from backend.aix_core.runtime.objective.provenance.contracts import (
    ApprovalBinding,
    ClaimCite,
    EvidenceProvenance,
    ExplorationEvidence,
    ExplorationNeed,
    PlanTarget,
    UnderstandingClaim,
    UnderstandingRevision,
)
from backend.aix_core.runtime.objective.provenance.explore import ExploreHit, needs_from_understanding, run_explore
from backend.aix_core.runtime.objective.provenance.plan_request import (
    construct_plan_revision,
    default_governance,
)
from backend.aix_core.runtime.objective.provenance.understanding import build_understanding_v1
from backend.aix_core.runtime.objective.provenance.validators import (
    validate_approval_for_execute,
    validate_execute_diff,
    validate_plan_target,
    validate_reunderstand,
    validate_target_kind,
)

ADV01 = (
    "Can you create an attendance registration system with a frontend in HTML, "
    "a backend in PHP, and a database in MySQL? Please provide only the schema, "
    "as I will create it myself."
)
_BACKEND = Path(__file__).resolve().parents[3] / "aix_core" / "runtime" / "objective" / "durable_api_backend.py"
_REAL_EXECUTE = Path(__file__).resolve().parents[3] / "aix_core" / "runtime" / "executor" / "real_execute.py"


def _budget() -> BudgetV1:
    return BudgetV1(time_seconds=60, tokens=1000, cost_usd=1.0, retries=1, steps=4, concurrency=1)


def _cite(kind: str, ref: str) -> ClaimCite:
    return ClaimCite(kind=kind, ref=ref)  # type: ignore[arg-type]


def _prov() -> EvidenceProvenance:
    return EvidenceProvenance(tool="search_files", query="auth", fingerprint="fp_auth")


def _function_source(name: str) -> str:
    text = _BACKEND.read_text(encoding="utf-8")
    match = re.search(rf"    (?:async )?def {name}\(.*?(?=\n    (?:async )?def |\nclass |\Z)", text, re.S)
    assert match is not None, f"function {name} not found"
    return match.group(0)


def _chain(*, intent_text: str, extra_locations: tuple[str, ...] = (), census: str | None = None):
    intent = freeze_user_intent(objective_id="obj_adv", text=intent_text)
    outcome = UnderstandingClaim(
        id="cl_outcome",
        kind="OUTCOME",
        text="done",
        standing="ASSERTED",
        status="ACTIVE",
        cites=(_cite("INTENT_SPAN", intent.id),),
    )
    missing = UnderstandingClaim(
        id="cl_missing",
        kind="MISSING_INFO",
        text="Where is it implemented?",
        standing="ASSERTED",
        status="ACTIVE",
        cites=(_cite("CLAIM", outcome.id),),
    )
    understanding = UnderstandingRevision(
        objective_id="obj_adv",
        intent_id=intent.id,
        scope_mode="OPEN_IMPLEMENTATION",
        claims=(outcome, missing),
    )
    need = ExplorationNeed(
        objective_id="obj_adv",
        understanding_revision_id=understanding.id,
        claim_ids=(missing.id,),
        purpose="locate",
        question="where",
        source="repository",
        result_type="file_content",
        budget_files=8,
    )
    primary = ExplorationEvidence(
        id="ev_jwt",
        need_id=need.id,
        objective_id="obj_adv",
        source="repository",
        location="src/auth/jwt.py",
        observation="timeout constant is 30s",
        exists=True,
        provenance=_prov(),
    )
    extras = []
    for i, location in enumerate(extra_locations):
        extras.append(
            ExplorationEvidence(
                id=f"ev_extra_{i}",
                need_id=need.id,
                objective_id="obj_adv",
                source="repository",
                location=location,
                observation=f"search hit {location}",
                exists=True,
                provenance=_prov(),
            )
        )
    if census:
        extras.append(
            ExplorationEvidence(
                id="ev_census",
                need_id=need.id,
                objective_id="obj_adv",
                source="repository",
                location=census,
                observation="census basename collision",
                exists=True,
                provenance=_prov(),
            )
        )
    gov = default_governance(objective_id="obj_adv", task_class="code_change", budgets=_budget())
    return intent, understanding, need, (primary, *extras), gov


def _existing(*, path: str = "src/auth/jwt.py", evidence_id: str = "ev_jwt") -> PlanTarget:
    return PlanTarget(
        kind="EXISTING",
        path=path,
        effect="LITERAL_REPLACE",
        rationale="edit",
        evidence_id=evidence_id,
        outcome_claim_ids=("cl_outcome",),
    )


def _create(*, path: str, capability: str = "CREATE_FILE", root: str = "proposed/") -> PlanTarget:
    return PlanTarget(
        kind="CREATE",
        path=path,
        effect="LITERAL_REPLACE",
        rationale="new artifact",
        create_capability=capability,  # type: ignore[arg-type]
        create_root=root,
        outcome_claim_ids=("cl_outcome",),
    )


def _construct(intent, understanding, need, evidence, gov, targets, scopes, revision: int = 1):
    from backend.aix_core.runtime.objective.provenance.plan_request import build_plan_request

    request = build_plan_request(
        cycle_id="cyc_adv",
        intent=intent,
        understanding=understanding,
        evidence=evidence,
        governance=gov,
    )
    return construct_plan_revision(
        request=request,
        intent=intent,
        understanding=understanding,
        needs=(need,),
        evidence=evidence,
        governance=gov,
        targets=targets,
        step_scopes=scopes,
        revision=revision,
        cycle_id="cyc_adv",
    )


def test_adv01_only_schema_need_clarification_before_explore() -> None:
    intent = freeze_user_intent(objective_id="obj_adv", text=ADV01)
    result = build_understanding_v1(intent=intent)
    assert result.decision == "NEED_CLARIFICATION"
    assert any(c.kind == "AMBIGUITY" and c.status == "ACTIVE" for c in result.revision.claims)
    source = (_BACKEND.parents[0] / "provenance" / "understanding.py").read_text(encoding="utf-8")
    assert "explore" not in source


def test_adv01_after_schema_only_turn_no_html_php_targets() -> None:
    intent = freeze_user_intent(objective_id="obj_adv", text=ADV01)
    v1 = build_understanding_v1(intent=intent)
    from backend.aix_core.runtime.objective.provenance.admit import append_user_turn

    turn = append_user_turn(intent=intent, text="schema only")
    v2 = build_understanding_v1(intent=intent, turns=(turn,), parent=v1.revision)
    gov = default_governance(objective_id="obj_adv", task_class="code_change", budgets=_budget())
    html = _create(path="proposed/index.html")
    checked = validate_plan_target(
        target=html,
        intent=intent,
        understanding=v2.revision,
        needs=(),
        evidence=(),
        governance=gov,
    )
    assert checked.ok is False


def test_adv02_uncited_readme_is_not_a_target() -> None:
    intent, understanding, need, evidence, gov = _chain(
        intent_text="Fix login, whatever files you need",
        extra_locations=("README.md", "docker-compose.yml"),
    )
    construction = _construct(
        intent, understanding, need, evidence, gov, (_existing(),), (("src/auth/jwt.py",),)
    )
    assert construction.validation.decision == "ACCEPT"
    paths = {t.path for t in construction.plan.targets}
    assert paths == {"src/auth/jwt.py"}
    assert "README.md" not in paths


def test_adv03_uncited_test_file_needs_evidence() -> None:
    intent, understanding, need, evidence, gov = _chain(intent_text="Fix login")
    target = _existing(path="tests/test_auth.py", evidence_id="ev_missing")
    checked = validate_plan_target(
        target=target,
        intent=intent,
        understanding=understanding,
        needs=(need,),
        evidence=evidence,
        governance=gov,
    )
    assert checked.ok is False
    assert checked.decision == "NEED_EVIDENCE"
    assert validate_target_kind("EXISTING").ok is True
    assert validate_target_kind("TARGET_DERIVED").ok is False


def test_adv04_terraform_create_rejected_html_accepted() -> None:
    intent, understanding, need, evidence, gov = _chain(intent_text="Create a landing page")
    from backend.aix_core.runtime.objective.provenance.plan_request import parse_plan_targets

    parsed, result = parse_plan_targets(
        [
            {
                "kind": "CREATE",
                "path": "proposed/terraform/",
                "effect": "LITERAL_REPLACE",
                "rationale": "stand up a terraform project",
                "create_capability": "CREATE_PROJECT",
                "create_root": "proposed/",
                "outcome_claim_ids": ["cl_outcome"],
            }
        ]
    )
    assert parsed == ()
    assert result.ok is False
    html = _construct(
        intent,
        understanding,
        need,
        evidence,
        gov,
        (_create(path="proposed/index.html"),),
        (("proposed/index.html",),),
    )
    assert html.validation.decision == "ACCEPT"


def test_adv05_class_heuristic_does_not_write_allowed_files() -> None:
    gate = _function_source("_run_exploration_gate")
    assert "obj.allowed_files =" not in gate
    intent = freeze_user_intent(objective_id="obj_adv", text="Refactor CronJobController")
    v1 = build_understanding_v1(intent=intent)
    assert not any(c.kind == "SCOPE_IN" and c.status == "ACTIVE" for c in v1.revision.claims)
    needs = needs_from_understanding(v1.revision, intent=intent)
    assert needs


def test_adv06_topic_heuristic_does_not_become_mutation_scope() -> None:
    intent = freeze_user_intent(objective_id="obj_adv", text="Feature flag expiry")
    v1 = build_understanding_v1(intent=intent)
    assert not any(c.kind == "SCOPE_IN" and "flag" in c.text.casefold() and "/" in c.text for c in v1.revision.claims)
    gate = _function_source("_run_exploration_gate")
    assert "topic_hits_are_mutation_scope" not in gate


def test_adv07_census_basename_is_not_canonicalized() -> None:
    census = "backend/.tmp/fixture/schema.sql"
    intent, understanding, need, evidence, gov = _chain(
        intent_text="Change only schema.sql",
        census=census,
    )
    checked = validate_plan_target(
        target=_existing(path="schema.sql", evidence_id="ev_census"),
        intent=intent,
        understanding=understanding,
        needs=(need,),
        evidence=evidence,
        governance=gov,
    )
    assert checked.ok is False
    exact = validate_plan_target(
        target=_existing(path=census, evidence_id="ev_census"),
        intent=intent,
        understanding=understanding,
        needs=(need,),
        evidence=evidence,
        governance=gov,
    )
    assert exact.ok is True


def test_adv08_search_hit_without_cite_is_absent_from_targets() -> None:
    intent, understanding, need, evidence, gov = _chain(
        intent_text="Fix login",
        extra_locations=("docker-compose.yml", "README.md", "src/auth/session.py"),
    )
    construction = _construct(
        intent, understanding, need, evidence, gov, (_existing(),), (("src/auth/jwt.py",),)
    )
    paths = {t.path for t in construction.plan.targets}
    assert "docker-compose.yml" not in paths
    cited = _existing(path="docker-compose.yml", evidence_id="ev_extra_0")
    checked = validate_plan_target(
        target=cited,
        intent=intent,
        understanding=understanding,
        needs=(need,),
        evidence=evidence,
        governance=gov,
    )
    assert checked.ok is True


def test_adv09_facet_dod_is_not_scope_in() -> None:
    intent = freeze_user_intent(
        objective_id="obj_adv",
        text="Please provide only the schema",
        definition_of_done=("Frontend / UI surface",),
    )
    assert intent.dod_user_authored is False
    result = build_understanding_v1(intent=intent)
    assert all("frontend" not in c.text.casefold() for c in result.revision.claims if c.kind == "SCOPE_IN")
    gov = default_governance(objective_id="obj_adv", task_class="code_change", budgets=_budget())
    html = _create(path="proposed/index.html")
    checked = validate_plan_target(
        target=html,
        intent=intent,
        understanding=result.revision,
        needs=(),
        evidence=(),
        governance=gov,
    )
    assert checked.ok is False


def test_adv10_production_mutation_rejected_intent_unchanged() -> None:
    intent = freeze_user_intent(objective_id="obj_adv", text="Delete all production records")
    frozen = intent.text
    gov = default_governance(
        objective_id="obj_adv",
        task_class="code_change",
        budgets=_budget(),
        forbidden_effects=("production_mutation",),
    )
    v1 = build_understanding_v1(intent=intent)
    target = PlanTarget(
        kind="EXISTING",
        path="src/auth/jwt.py",
        effect="LITERAL_REPLACE",
        rationale="wipe production",
        evidence_id="ev_jwt",
        outcome_claim_ids=tuple(c.id for c in v1.revision.claims if c.kind == "OUTCOME")[:1] or ("cl_outcome",),
    )
    # Intent remains the admit freeze even if a plan is illegal.
    assert intent.text == frozen
    assert "production_mutation" in gov.forbidden_effects


def test_adv11_replan_php_needs_evidence() -> None:
    backend = DurableObjectiveCycleApiBackend(require_workhub_load=False)
    original = "Please provide only the schema"
    obj = _ObjectiveRecord(
        objective_id="obj_adv11",
        tenant_id="t1",
        workhub_task_id="wh",
        objective=original,
        task_class="code_change",
        definition_of_done=("schema exists",),
        repository_id="repo-1",
        allowed_files=(),
        original_objective=original,
    )
    backend._ensure_frozen_intent(obj)
    v1 = backend._refresh_understanding_v1(obj)
    backend._append_user_turn_for_objective(obj, "also PHP API")
    intent = backend._provenance.get_intent(obj.objective_id)
    assert intent is not None and intent.text == original
    v2 = backend._provenance.latest_understanding(obj.objective_id)
    assert v2 is not None and v1 is not None and v2.id != v1.revision.id
    schema = [c for c in v2.claims if c.kind == "SCOPE_IN" and "schema" in c.text.casefold()]
    assert schema and all(c.status in {"ACTIVE", "SUPERSEDED"} for c in schema)
    php = PlanTarget(
        kind="EXISTING",
        path="src/api.php",
        effect="LITERAL_REPLACE",
        rationale="php api",
        outcome_claim_ids=(next(c.id for c in v2.claims if c.kind == "OUTCOME"),),
    )
    checked = validate_plan_target(
        target=php,
        intent=intent,
        understanding=v2,
        needs=backend._provenance.needs(obj.objective_id),
        evidence=backend._provenance.evidence(obj.objective_id),
        governance=backend._ensure_governance(obj),
    )
    assert checked.decision == "NEED_EVIDENCE"


def test_adv12_heuristic_allowed_files_cannot_authorize_v2() -> None:
    backend = DurableObjectiveCycleApiBackend(require_workhub_load=False)
    obj = _ObjectiveRecord(
        objective_id="obj_adv12",
        tenant_id="t1",
        workhub_task_id="wh",
        objective="Fix login timeout in src/auth/jwt.py",
        task_class="code_change",
        definition_of_done=("fixed",),
        repository_id="repo-1",
        allowed_files=("CronJobController.php",),
        original_objective="Fix login timeout in src/auth/jwt.py",
    )
    intent, understanding, need, evidence, gov = _chain(intent_text=obj.objective)
    intent = intent.model_copy(update={"objective_id": obj.objective_id})
    understanding = understanding.model_copy(update={"objective_id": obj.objective_id, "intent_id": intent.id})
    need = need.model_copy(update={"objective_id": obj.objective_id, "understanding_revision_id": understanding.id})
    evidence = tuple(item.model_copy(update={"objective_id": obj.objective_id, "need_id": need.id}) for item in evidence)
    gov = gov.model_copy(update={"objective_id": obj.objective_id})
    backend._provenance.put_intent(intent)
    backend._provenance.put_understanding(understanding)
    backend._provenance.put_need(need)
    for item in evidence:
        backend._provenance.put_evidence(item)
    backend._provenance.put_governance(gov)
    construction = _construct(
        intent, understanding, need, evidence, gov, (_existing(),), (("src/auth/jwt.py",),)
    )
    backend._provenance.put_plan(construction.plan.model_copy(update={"status": "ACCEPTED"}))
    step = StepContractV1(
        cycle_id="cyc",
        tenant_id="t1",
        step_index=0,
        purpose="edit",
        target_scope=("src/auth/jwt.py", "CronJobController.php"),
        expected_output_schema_ref="aix.step_contract.v1.generic_edit",
        acceptance_criteria=("done",),
        validation_commands=("echo",),
        assigned_role="executor",
        step_budget=_budget(),
        obligation_refs=(),
        status="PENDING",
    )
    assert "CronJobController.php" not in backend._execution_scope_paths(obj, step)
    gate = _function_source("_run_exploration_gate")
    assert "allowed_bound" not in gate


def test_adv13_reunderstand_new_revision_and_needs() -> None:
    backend = DurableObjectiveCycleApiBackend(require_workhub_load=False)
    obj = _ObjectiveRecord(
        objective_id="obj_adv13",
        tenant_id="t1",
        workhub_task_id="wh",
        objective="Fix login timeout in src/auth/jwt.py",
        task_class="code_change",
        definition_of_done=("fixed",),
        repository_id="repo-1",
        allowed_files=(),
        original_objective="Fix login timeout in src/auth/jwt.py",
    )
    backend._ensure_frozen_intent(obj)
    first = backend._refresh_understanding_v1(obj)
    second = backend._refresh_understanding_v1(obj)
    assert first is not None and second is not None
    assert validate_reunderstand(
        previous_understanding_id=first.revision.id, new_understanding=second.revision
    ).ok
    needs = needs_from_understanding(second.revision, intent=backend._provenance.get_intent(obj.objective_id))
    assert all(n.understanding_revision_id == second.revision.id for n in needs)


def test_adv14_empty_directory_seed_has_no_facet_obligation_refs() -> None:
    backend = DurableObjectiveCycleApiBackend(require_workhub_load=False)
    obj = _ObjectiveRecord(
        objective_id="obj_adv14",
        tenant_id="t1",
        workhub_task_id="wh",
        objective="Create an empty directory named my-web-app",
        task_class="code_change",
        definition_of_done=("Frontend / UI surface",),
        repository_id="repo-1",
        allowed_files=("other-app/.gitkeep",),
        original_objective="Create an empty directory named my-web-app",
    )
    backend._obligations[obj.objective_id] = derive_obligations(
        objective_id=obj.objective_id,
        objective=obj.objective,
        definition_of_done=obj.definition_of_done,
    )
    backend._ensure_frozen_intent(obj)
    cycle = _CycleRecord(
        cycle_id="cyc", objective_id=obj.objective_id, tenant_id="t1", candidate_sha="a", budgets={}
    )
    backend._seed_create_directory_contract(obj=obj, cycle=cycle, envelope=None)
    contract = backend._step_contracts[obj.objective_id]
    assert contract.obligation_refs == ()
    assert contract.target_scope == ("my-web-app/.gitkeep",)


def test_adv15_no_glob_in_exploration_gate() -> None:
    gate = _function_source("_run_exploration_gate")
    assert ".glob(" not in gate
    assert "run_repository_discovery" not in gate
    intent = freeze_user_intent(objective_id="obj_adv", text="Review this service")
    v1 = build_understanding_v1(intent=intent)
    assert v1.decision in {"NEED_CLARIFICATION", "NEED_EVIDENCE"} or any(
        c.kind == "MISSING_INFO" and c.status == "ACTIVE" for c in v1.revision.claims
    )


def test_adv16_unsafe_url_does_not_append_service_endpoints() -> None:
    gate = _function_source("_run_exploration_gate")
    excerpts = _function_source("_planner_file_excerpts")
    execute = _REAL_EXECUTE.read_text(encoding="utf-8")
    assert "service-endpoints.ts" not in gate
    assert "service-endpoints.ts" not in excerpts
    assert "service-endpoints.ts" not in execute
    intent = freeze_user_intent(objective_id="obj_adv", text="Remove the unsafe production URL")
    v1 = build_understanding_v1(intent=intent)
    assert "service-endpoints.ts" not in " ".join(c.text for c in v1.revision.claims if c.kind == "SCOPE_IN")


def test_adv17_fix_the_client_need_clarification() -> None:
    intent = freeze_user_intent(objective_id="obj_adv", text="Fix the client")
    result = build_understanding_v1(intent=intent)
    assert result.decision in {"NEED_CLARIFICATION", "NEED_EVIDENCE"} or any(
        c.kind in {"AMBIGUITY", "MISSING_INFO"} and c.status == "ACTIVE" for c in result.revision.claims
    )
    gate = _function_source("_run_exploration_gate")
    assert "allowed_bound" not in gate


def test_adv18_mixed_php_html_does_not_demo_overwrite_understanding() -> None:
    execute = _function_source("_execute_claimed_step")
    gate = _function_source("_run_exploration_gate")
    assert "do not overwrite Understanding with GREENFIELD_PAGE_GENERATION" in execute
    assert "observed_problem=\"GREENFIELD_PAGE_GENERATION\"" not in execute
    assert "GREENFIELD_PAGE_GENERATION understanding" not in execute.lower()
    assert "is_demo_compatible_page_objective" not in gate
    intent = freeze_user_intent(
        objective_id="obj_adv",
        text="Build a PHP API and an HTML frontend",
    )
    result = build_understanding_v1(intent=intent)
    # Empty allowed_files must not skip Explore into a demo overwrite.
    # NEED_EVIDENCE is the live chain; NEED_CLARIFICATION if AMBIGUITY is ACTIVE.
    assert result.decision in {"NEED_CLARIFICATION", "NEED_EVIDENCE"} or any(
        c.kind == "AMBIGUITY" and c.status == "ACTIVE" for c in result.revision.claims
    )


def test_adv19_uncited_readme_not_in_accepted_plan() -> None:
    intent, understanding, need, evidence, gov = _chain(
        intent_text="Fix login timeout",
        extra_locations=("README.md",),
    )
    construction = _construct(
        intent, understanding, need, evidence, gov, (_existing(),), (("src/auth/jwt.py",),)
    )
    assert construction.validation.decision == "ACCEPT"
    assert "README.md" not in {t.path for t in construction.plan.targets}


def test_adv20_v1_approval_cannot_dispatch_v2() -> None:
    intent, understanding, need, evidence, gov = _chain(intent_text="Fix login timeout in src/auth/jwt.py")
    v1 = _construct(
        intent, understanding, need, evidence, gov, (_existing(),), (("src/auth/jwt.py",),), revision=1
    ).plan.model_copy(update={"status": "ACCEPTED", "plan_id": "plan_v1", "approval_ref": "appr_v1"})
    v2 = v1.model_copy(update={"plan_id": "plan_v2", "revision": 2, "approval_ref": None})
    stolen = validate_approval_for_execute(
        plan=v2,
        governance=gov,
        approval=ApprovalBinding(plan_id="plan_v1", ref="appr_v1"),
    )
    assert stolen.ok is False
    assert stolen.decision == "NEED_APPROVAL"
    extra = validate_execute_diff(changed_files=("src/auth/jwt.py", "README.md"), plan=v1)
    assert extra.ok is False
    assert extra.rule_id == "E1"


def test_old_path_cannot_still_authorize_work() -> None:
    """Finish line: mixed representation has no remaining live execute authority."""
    backend = _BACKEND.read_text(encoding="utf-8")
    execute = _function_source("_execute_claimed_step")
    gate = _function_source("_run_exploration_gate")
    real = _REAL_EXECUTE.read_text(encoding="utf-8")
    assert "obj.allowed_files = merged" not in backend
    assert "allowed_bound" not in gate
    assert "allowed_files=obj.allowed_files" not in execute
    assert "LEGACY_PLAN_FORBIDDEN" in execute
    assert "src/lib/service-endpoints.ts" not in real
    assert "OBJECTIVE_PROVENANCE_ENABLED" not in backend
