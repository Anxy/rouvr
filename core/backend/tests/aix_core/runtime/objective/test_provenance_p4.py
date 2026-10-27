"""P4: PlanRequest four sections → PlanTarget validation → accepted Plan.

Parser must not repair, canonicalize, infer effects, or add files.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any

import pytest

from backend.aix_core.runtime.objective.contracts import (
    BudgetV1,
    UnderstandingEvidenceV1,
)
from backend.aix_core.runtime.objective.durable_api_backend import (
    DurableObjectiveCycleApiBackend,
    _CycleRecord,
    _ObjectiveRecord,
)
from backend.aix_core.runtime.objective.obligations import derive_obligations
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
from backend.aix_core.runtime.objective.provenance.plan_request import (
    build_plan_request,
    construct_plan_revision,
    default_governance,
    parse_plan_targets,
    plan_request_sections,
)
from backend.aix_core.runtime.objective.provenance.understanding import build_understanding_v1
from backend.aix_core.runtime.objective.provenance.validators import validate_plan_target
from backend.aix_core.runtime.planner.step_contract_planner import (
    _canonicalize_target_scope,
    _files_by_name,
    _target_scope_grounded,
    propose_step_contract,
)
from backend.infrastructure.model_gateway.hyeve_integration_client import ChatCompletionResultV1
from backend.infrastructure.model_gateway.intelligence_layer.prompts import (
    build_provenance_planner_evidence,
    build_step_contract_planner_evidence,
)

_PROVENANCE_DIR = (
    Path(__file__).resolve().parents[4] / "aix_core" / "runtime" / "objective" / "provenance"
)
_BACKEND = Path(__file__).resolve().parents[4] / "aix_core" / "runtime" / "objective" / "durable_api_backend.py"
_PLANNER = Path(__file__).resolve().parents[4] / "aix_core" / "runtime" / "planner" / "step_contract_planner.py"


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


def _chain(
    *,
    intent_text: str = "Fix login timeout in src/auth/jwt.py",
    extra_locations: tuple[str, ...] = (),
    scope_mode: str = "OPEN_IMPLEMENTATION",
    scope_in: tuple[str, ...] = (),
):
    intent = freeze_user_intent(objective_id="obj_1", text=intent_text)
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
    scope_claims = tuple(
        UnderstandingClaim(
            kind="SCOPE_IN",
            text=path,
            standing="ASSERTED",
            status="ACTIVE",
            cites=(_cite("INTENT_SPAN", intent.id),),
        )
        for path in scope_in
    )
    understanding = UnderstandingRevision(
        objective_id="obj_1",
        intent_id=intent.id,
        scope_mode=scope_mode,  # type: ignore[arg-type]
        claims=(outcome, missing, *scope_claims),
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
    primary = ExplorationEvidence(
        id="ev_jwt",
        need_id=need.id,
        objective_id="obj_1",
        source="repository",
        location="src/auth/jwt.py",
        observation="timeout constant is 30s",
        exists=True,
        provenance=_prov(),
    )
    extras = tuple(
        ExplorationEvidence(
            id=f"ev_extra_{i}",
            need_id=need.id,
            objective_id="obj_1",
            source="repository",
            location=location,
            observation=f"search hit {location}",
            exists=True,
            provenance=_prov(),
        )
        for i, location in enumerate(extra_locations)
    )
    gov = default_governance(
        objective_id="obj_1",
        task_class="code_change",
        budgets=_budget(),
    )
    return intent, understanding, need, (primary, *extras), gov


def _request(intent, understanding, evidence, gov, *, cycle_id: str = "cyc_1"):
    return build_plan_request(
        cycle_id=cycle_id,
        intent=intent,
        understanding=understanding,
        evidence=evidence,
        governance=gov,
    )


def _existing(*, path: str = "src/auth/jwt.py", evidence_id: str = "ev_jwt", outcome_id: str = "cl_outcome") -> PlanTarget:
    return PlanTarget(
        kind="EXISTING",
        path=path,
        effect="LITERAL_REPLACE",
        rationale="change the timeout constant",
        evidence_id=evidence_id,
        outcome_claim_ids=(outcome_id,),
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


def test_plan_request_has_four_typed_sections_only() -> None:
    intent, understanding, _need, evidence, gov = _chain(
        extra_locations=("README.md", "docker-compose.yml"),
    )
    request = _request(intent, understanding, evidence, gov)
    payload = plan_request_sections(request)
    assert set(payload) == {"intent", "understanding", "evidence", "governance"}
    dumped = json.dumps(payload)
    assert "files_read" not in dumped
    assert "allowed_files" not in dumped
    assert payload["governance"]["create_root_is"] == "permission"
    assert "SCOPE_IN" not in json.dumps(payload["governance"])
    evidence_bag = build_provenance_planner_evidence(payload)
    assert set(evidence_bag) == {"intent", "understanding", "evidence", "governance"}


def test_adv02_and_adv19_uncited_readme_is_not_a_target() -> None:
    intent, understanding, need, evidence, gov = _chain(
        extra_locations=("README.md", "docker-compose.yml"),
    )
    request = _request(intent, understanding, evidence, gov)
    construction = construct_plan_revision(
        request=request,
        intent=intent,
        understanding=understanding,
        needs=(need,),
        evidence=evidence,
        governance=gov,
        targets=(_existing(),),
        step_scopes=(("src/auth/jwt.py",),),
    )
    assert construction.validation.ok is True
    assert construction.validation.decision == "ACCEPT"
    paths = {t.path for t in construction.plan.targets}
    assert paths == {"src/auth/jwt.py"}
    assert "README.md" not in paths
    assert "docker-compose.yml" not in paths
    locations = {item.location for item in request.evidence}
    assert "README.md" in locations
    assert "docker-compose.yml" in locations


def test_adv03_missing_test_file_is_need_evidence_not_target_derived() -> None:
    intent, understanding, need, evidence, gov = _chain()
    request = _request(intent, understanding, evidence, gov)
    derived, parse = parse_plan_targets(
        [
            {
                "kind": "TARGET_DERIVED",
                "path": "tests/test_auth.py",
                "effect": "LITERAL_REPLACE",
                "rationale": "tests usually sit next to auth",
                "outcome_claim_ids": ["cl_outcome"],
            }
        ]
    )
    assert derived == ()
    assert parse.ok is False
    assert parse.rule_id == "P12"

    missing = PlanTarget(
        kind="EXISTING",
        path="tests/test_auth.py",
        effect="LITERAL_REPLACE",
        rationale="add coverage",
        outcome_claim_ids=("cl_outcome",),
    )
    construction = construct_plan_revision(
        request=request,
        intent=intent,
        understanding=understanding,
        needs=(need,),
        evidence=evidence,
        governance=gov,
        targets=(missing,),
        step_scopes=(("tests/test_auth.py",),),
    )
    assert construction.validation.ok is False
    assert construction.validation.decision == "NEED_EVIDENCE"
    assert construction.validation.rule_id in {"P1", "P2"}
    assert "TARGET_DERIVED" not in construction.validation.detail


def test_adv04_create_file_in_root_accepts_project_capability_rejects() -> None:
    intent, understanding, need, evidence, gov = _chain(intent_text="Build a landing page")
    request = _request(intent, understanding, evidence, gov)
    ok = construct_plan_revision(
        request=request,
        intent=intent,
        understanding=understanding,
        needs=(need,),
        evidence=evidence,
        governance=gov,
        targets=(_create(path="proposed/index.html"),),
        step_scopes=(("proposed/index.html",),),
    )
    assert ok.validation.ok is True
    assert ok.validation.decision == "ACCEPT"

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
    assert result.rule_id == "P4"

    prose_effect, prose_ok = parse_plan_targets(
        [
            {
                "kind": "CREATE",
                "path": "proposed/attendance/index.html",
                "effect": (
                    "Plan to create the attendance registration system under "
                    "proposed/attendance/ with an HTML frontend"
                ),
                "rationale": "",
                "create_capability": "CREATE_FILE",
                "create_root": "proposed/",
                "outcome_claim_ids": ["cl_outcome"],
            }
        ]
    )
    assert prose_ok.ok is True
    assert len(prose_effect) == 1
    assert prose_effect[0].effect == "LITERAL_REPLACE"
    assert "attendance registration" in prose_effect[0].rationale

    existing_bad, existing_parse = parse_plan_targets(
        [
            {
                "kind": "EXISTING",
                "path": "src/auth/jwt.py",
                "effect": "refactor authentication architecture",
                "rationale": "fix login",
                "evidence_id": "ev_1",
                "outcome_claim_ids": ["cl_outcome"],
            }
        ]
    )
    assert existing_bad == ()
    assert existing_parse.ok is False
    assert existing_parse.rule_id == "P9"

    terraform = validate_plan_target(
        target=PlanTarget(
            kind="CREATE",
            path="proposed/terraform/main.tf",
            effect="LITERAL_REPLACE",
            rationale="terraform project",
            create_capability="CREATE_FILE",
            create_root="src/",
            outcome_claim_ids=("cl_outcome",),
        ),
        intent=intent,
        understanding=understanding,
        needs=(need,),
        evidence=evidence,
        governance=gov,
    )
    assert terraform.ok is False
    assert terraform.rule_id == "P4"


def test_adv07_census_basename_cannot_substitute_exact_location() -> None:
    intent, understanding, need, _primary, gov = _chain()
    census = ExplorationEvidence(
        id="ev_census",
        need_id=need.id,
        objective_id="obj_1",
        source="repository",
        location="backend/.tmp/aix-cycle-workspaces/abc/schema.sql",
        observation="census listed schema.sql",
        exists=True,
        provenance=_prov(),
    )
    request = _request(intent, understanding, (census,), gov)
    construction = construct_plan_revision(
        request=request,
        intent=intent,
        understanding=understanding,
        needs=(need,),
        evidence=(census,),
        governance=gov,
        targets=(_existing(path="schema.sql", evidence_id="ev_census"),),
        step_scopes=(("schema.sql",),),
    )
    assert construction.validation.ok is False
    assert construction.validation.decision == "NEED_EVIDENCE"
    assert construction.validation.rule_id == "P2"
    assert _canonicalize_target_scope("schema.sql", (census.location,)) == "schema.sql"
    assert _files_by_name((census.location,)) == {}


def test_adv08_search_hits_remain_evidence_until_cited() -> None:
    hits = (
        "src/auth/jwt.py",
        "docker-compose.yml",
        "README.md",
        "src/auth/session.py",
    )
    intent, understanding, need, evidence, gov = _chain(extra_locations=hits[1:])
    request = _request(intent, understanding, evidence, gov)
    assert {item.location for item in request.evidence} >= set(hits)
    construction = construct_plan_revision(
        request=request,
        intent=intent,
        understanding=understanding,
        needs=(need,),
        evidence=evidence,
        governance=gov,
        targets=(_existing(),),
        step_scopes=(("src/auth/jwt.py",),),
    )
    assert construction.validation.ok is True
    assert {t.path for t in construction.plan.targets} == {"src/auth/jwt.py"}
    cited = construct_plan_revision(
        request=request,
        intent=intent,
        understanding=understanding,
        needs=(need,),
        evidence=evidence,
        governance=gov,
        targets=(
            _existing(),
            _existing(path="docker-compose.yml", evidence_id="ev_extra_0"),
        ),
        step_scopes=(("src/auth/jwt.py", "docker-compose.yml"),),
    )
    assert cited.validation.ok is True
    assert cited.validation.decision == "ACCEPT"


def test_adv09_client_dod_cannot_create_html_scope() -> None:
    intent = freeze_user_intent(
        objective_id="obj_1",
        text="Change only schema.sql",
        definition_of_done=("Frontend / UI surface",),
        dod_user_authored=False,
    )
    result = build_understanding_v1(intent=intent)
    assert intent.dod_user_authored is False
    assert result.revision.scope_mode == "BOUNDED"
    assert all("frontend" not in c.text.casefold() for c in result.revision.claims if c.kind == "SCOPE_IN")
    outcome = next(c for c in result.revision.claims if c.kind == "OUTCOME")
    gov = default_governance(objective_id="obj_1", task_class="code_change", budgets=_budget())
    html = PlanTarget(
        kind="CREATE",
        path="proposed/index.html",
        effect="LITERAL_REPLACE",
        rationale="client DoD mentioned frontend",
        create_capability="CREATE_FILE",
        create_root="proposed/",
        outcome_claim_ids=(outcome.id,),
    )
    checked = validate_plan_target(
        target=html,
        intent=intent,
        understanding=result.revision,
        needs=(),
        evidence=(),
        governance=gov,
    )
    assert checked.ok is False
    assert checked.rule_id == "P7"


def test_p7_allows_create_root_descendant_for_bare_scope_in() -> None:
    intent = freeze_user_intent(
        objective_id="obj_erp",
        text=(
            "Create ERP under proposed/erp-system/. Governance create_root = proposed/. "
            "CREATE index.html and api.php. Do not create mock-only UI."
        ),
    )
    result = build_understanding_v1(intent=intent, governance=default_governance(
        objective_id="obj_erp", task_class="code_change", budgets=_budget()
    ))
    outcome = next(c for c in result.revision.claims if c.kind == "OUTCOME")
    gov = default_governance(objective_id="obj_erp", task_class="code_change", budgets=_budget())
    # Force BOUNDED + bare SCOPE_IN to prove create_root descendant matching.
    claims = tuple(
        c
        for c in result.revision.claims
        if c.kind != "SCOPE_IN"
    ) + (
        UnderstandingClaim(
            kind="SCOPE_IN",
            text="index.html",
            cites=(ClaimCite(kind="INTENT_SPAN", ref=intent.id),),
            standing="ASSERTED",
            status="ACTIVE",
        ),
        UnderstandingClaim(
            kind="SCOPE_IN",
            text="api.php",
            cites=(ClaimCite(kind="INTENT_SPAN", ref=intent.id),),
            standing="ASSERTED",
            status="ACTIVE",
        ),
    )
    bounded = UnderstandingRevision(
        objective_id="obj_erp",
        intent_id=intent.id,
        scope_mode="BOUNDED",
        claims=claims,
    )
    target = PlanTarget(
        kind="CREATE",
        path="proposed/erp-system/index.html",
        effect="LITERAL_REPLACE",
        rationale="greenfield under create_root",
        create_capability="CREATE_FILE",
        create_root="proposed/",
        outcome_claim_ids=(outcome.id,),
    )
    checked = validate_plan_target(
        target=target,
        intent=intent,
        understanding=bounded,
        needs=(),
        evidence=(),
        governance=gov,
    )
    assert checked.ok is True


def test_adv14_seeded_create_directory_has_empty_obligation_refs() -> None:
    backend = DurableObjectiveCycleApiBackend(require_workhub_load=False)
    obj = _ObjectiveRecord(
        objective_id="obj_dir",
        tenant_id="t1",
        workhub_task_id="wh-1",
        objective="Create an empty directory named my-web-app",
        task_class="code_change",
        definition_of_done=("directory exists",),
        repository_id="repo-1",
        allowed_files=("my-web-app/.gitkeep",),
    )
    backend._obligations[obj.objective_id] = derive_obligations(  # noqa: SLF001
        objective_id=obj.objective_id,
        objective=obj.objective,
        definition_of_done=obj.definition_of_done,
    )
    assert backend._obligations[obj.objective_id]  # noqa: SLF001
    cycle = _CycleRecord(
        cycle_id="cyc_dir",
        objective_id=obj.objective_id,
        tenant_id="t1",
        candidate_sha="abc",
        budgets={},
    )
    backend._seed_create_directory_contract(obj=obj, cycle=cycle, envelope=None)  # noqa: SLF001
    contract = backend._step_contracts[obj.objective_id]  # noqa: SLF001
    assert contract.obligation_refs == ()
    src = _function_source("_seed_create_directory_contract")
    assert "obligation_refs=()" in src
    assert "obligation_refs=obligation_ids" not in src


def test_open_implementation_has_no_relevance_or_necessity_gate() -> None:
    intent = freeze_user_intent(
        objective_id="obj_1",
        text="Fix login, whatever files you need",
    )
    v1 = build_understanding_v1(intent=intent)
    assert v1.revision.scope_mode == "OPEN_IMPLEMENTATION"
    outcome = next(c for c in v1.revision.claims if c.kind == "OUTCOME")
    gov = default_governance(objective_id="obj_1", task_class="code_change", budgets=_budget())
    request = _request(intent, v1.revision, (), gov)
    target = PlanTarget(
        kind="CREATE",
        path="proposed/index.html",
        effect="LITERAL_REPLACE",
        rationale="new page",
        create_capability="CREATE_FILE",
        create_root="proposed/",
        outcome_claim_ids=(outcome.id,),
    )
    construction = construct_plan_revision(
        request=request,
        intent=intent,
        understanding=v1.revision,
        needs=(),
        evidence=(),
        governance=gov,
        targets=(target,),
        step_scopes=(("proposed/index.html",),),
    )
    assert construction.validation.ok is True
    assert construction.validation.decision == "ACCEPT"
    validators = (_PROVENANCE_DIR / "validators.py").read_text(encoding="utf-8")
    assert "relevance" not in validators.casefold() or "no relevance/necessity" in validators
    assert "necessity" not in validators.casefold() or "no relevance/necessity" in validators


def test_parser_kills_canonicalization_constraints_and_enrichment() -> None:
    planner = _PLANNER.read_text(encoding="utf-8")
    assert "infer_required_effects" not in planner
    assert "attach_effects" not in planner
    assert "item in constraint" not in planner
    assert _canonicalize_target_scope("backend/schema.sql", ("schema.sql",)) == "backend/schema.sql"
    assert _target_scope_grounded("proposed/index.html", files_read=()) is False
    assert _target_scope_grounded(
        "proposed/index.html",
        files_read=(),
        plan_target_paths=("proposed/index.html",),
    ) is True
    assert _target_scope_grounded(
        "getDueFeeStudent",
        files_read=("FeeDue.php",),
        symbols_found=("getDueFeeStudent",),
        require_plan_targets=True,
    ) is False
    constraints = _function_source("_planner_constraints")
    assert "allowed_files:" not in constraints
    backend = _BACKEND.read_text(encoding="utf-8")
    assert "Create a new HTML/CSS page under the cycle workspace" not in backend
    assert "demo_capability=self._demo_capability_from_accepted_plan(obj)" in backend
    old_bag = build_step_contract_planner_evidence(
        objective="Create an HTML login page",
        definition_of_done=[],
        files_read=[],
        symbols_found=[],
        observed_problem="GREENFIELD_PAGE_GENERATION: do not use this flag",
        constraints=["allowed_files:proposed/index.html"],
    )
    assert "greenfield_page_generation" not in old_bag
    assert "proposal_output_root" not in old_bag


def test_p2_understanding_never_imports_plan_or_planner() -> None:
    source = (_PROVENANCE_DIR / "understanding.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert "plan_request" not in node.module
            assert "planner" not in node.module
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert "plan" not in alias.name
    assert "PlanTarget" not in source


def test_provenance_valid_is_not_plan_correct() -> None:
    intent, understanding, need, evidence, gov = _chain(
        extra_locations=("docker-compose.yml",),
    )
    request = _request(intent, understanding, evidence, gov)
    construction = construct_plan_revision(
        request=request,
        intent=intent,
        understanding=understanding,
        needs=(need,),
        evidence=evidence,
        governance=gov,
        targets=(_existing(path="docker-compose.yml", evidence_id="ev_extra_0"),),
        step_scopes=(("docker-compose.yml",),),
    )
    assert construction.validation.ok is True
    assert construction.validation.rule_id == "P8"
    assert construction.validation.decision == "ACCEPT"


def test_plan_does_not_mutate_upstream_objects() -> None:
    intent, understanding, need, evidence, gov = _chain()
    intent_text = intent.text
    claim_ids = tuple(c.id for c in understanding.claims)
    evidence_ids = tuple(item.id for item in evidence)
    request = _request(intent, understanding, evidence, gov)
    construct_plan_revision(
        request=request,
        intent=intent,
        understanding=understanding,
        needs=(need,),
        evidence=evidence,
        governance=gov,
        targets=(_existing(),),
        step_scopes=(("src/auth/jwt.py",),),
    )
    assert intent.text == intent_text
    assert tuple(c.id for c in understanding.claims) == claim_ids
    assert tuple(item.id for item in evidence) == evidence_ids


@pytest.mark.asyncio
async def test_planner_accepts_explicit_create_without_parser_repair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    intent, understanding, need, evidence, gov = _chain(intent_text="Create an HTML login page")
    request = _request(intent, understanding, evidence, gov)
    payload = {
        "reply": "Create proposed/index.html via content.generate_html_css.",
        "needs_more_evidence": False,
        "targets": [
            {
                "kind": "CREATE",
                "path": "proposed/index.html",
                "effect": "LITERAL_REPLACE",
                "rationale": "new login page",
                "create_capability": "CREATE_FILE",
                "create_root": "proposed/",
                "outcome_claim_ids": ["cl_outcome"],
            }
        ],
        "steps": [
            {
                "purpose": "Generate the login page",
                "target_scope": ["proposed/index.html"],
                "acceptance_criteria": ["proposed/index.html exists"],
                "validation_commands": ["test -f proposed/index.html"],
                "actions": [
                    {"capability": "content", "operation": "generate_html_css", "arguments": {}}
                ],
                "required_effects": [],
                "depends_on": [],
                "risk_tier": "LOW",
            }
        ],
    }

    async def _fake_chat_completion(**_kwargs: Any) -> ChatCompletionResultV1:
        return ChatCompletionResultV1(
            output_text=json.dumps(payload),
            provider="test",
            resolved_provider="test",
            model="test-model",
            usage={},
        )

    monkeypatch.setattr(
        "backend.aix_core.runtime.planner.step_contract_planner.chat_completion",
        _fake_chat_completion,
    )
    snapshot = UnderstandingEvidenceV1(
        objective_id="obj_1",
        cycle_id="cyc_1",
        files_read=(),
        observed_problem="plan requested",
        understanding_complete=True,
    )
    proposal = await propose_step_contract(
        objective=intent.text,
        definition_of_done=[],
        understanding=snapshot,
        constraints=[],
        tenant_id="t1",
        cycle_id="cyc_1",
        step_index=0,
        assigned_role="executor",
        step_budget=_budget(),
        actor_id="actor",
        authorization_header="",
        timeout_seconds=5.0,
        plan_request=request,
        intent=intent,
        understanding_revision=understanding,
        needs=(need,),
        exploration_evidence=evidence,
        governance=gov,
    )
    assert proposal.reason is None, proposal.reason
    assert proposal.provenance_decision == "ACCEPT"
    assert proposal.contract is not None
    assert proposal.contract.target_scope == ("proposed/index.html",)
    assert proposal.provenance_plan is not None
    assert {t.path for t in proposal.provenance_plan.targets} == {"proposed/index.html"}
    assert all(str(t.get("kind")) != "SYSTEM_ENRICHED" for t in proposal.transformations)


@pytest.mark.asyncio
async def test_planner_rejects_uncited_readme_as_step_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    intent, understanding, need, evidence, gov = _chain(extra_locations=("README.md",))
    request = _request(intent, understanding, evidence, gov)
    payload = {
        "reply": "edit jwt and also rewrite README",
        "needs_more_evidence": False,
        "targets": [
            {
                "kind": "EXISTING",
                "path": "src/auth/jwt.py",
                "effect": "LITERAL_REPLACE",
                "rationale": "timeout lives here",
                "evidence_id": "ev_jwt",
                "outcome_claim_ids": ["cl_outcome"],
            }
        ],
        "steps": [
            {
                "purpose": "Fix timeout",
                "target_scope": ["src/auth/jwt.py", "README.md"],
                "acceptance_criteria": ["timeout is 60s"],
                "validation_commands": ["echo ok"],
                "required_effects": [{"kind": "LITERAL_REPLACE"}],
                "depends_on": [],
                "risk_tier": "LOW",
            }
        ],
    }

    async def _fake_chat_completion(**_kwargs: Any) -> ChatCompletionResultV1:
        return ChatCompletionResultV1(
            output_text=json.dumps(payload),
            provider="test",
            resolved_provider="test",
            model="test-model",
            usage={},
        )

    monkeypatch.setattr(
        "backend.aix_core.runtime.planner.step_contract_planner.chat_completion",
        _fake_chat_completion,
    )
    snapshot = UnderstandingEvidenceV1(
        objective_id="obj_1",
        cycle_id="cyc_1",
        files_read=("src/auth/jwt.py", "README.md"),
        observed_problem="plan requested",
        understanding_complete=True,
    )
    proposal = await propose_step_contract(
        objective=intent.text,
        definition_of_done=[],
        understanding=snapshot,
        constraints=[],
        tenant_id="t1",
        cycle_id="cyc_1",
        step_index=0,
        assigned_role="executor",
        step_budget=_budget(),
        actor_id="actor",
        authorization_header="",
        timeout_seconds=5.0,
        plan_request=request,
        intent=intent,
        understanding_revision=understanding,
        needs=(need,),
        exploration_evidence=evidence,
        governance=gov,
    )
    assert proposal.contract is None
    assert proposal.reason is not None
    assert "TARGET_SCOPE_NOT_GROUNDED_IN_EVIDENCE" in proposal.reason


def test_demo_capability_requires_accepted_create_file_in_root() -> None:
    backend = DurableObjectiveCycleApiBackend(require_workhub_load=False)
    obj = _ObjectiveRecord(
        objective_id="obj_html",
        tenant_id="t1",
        workhub_task_id="wh-2",
        objective="Create an HTML login page",
        task_class="code_change",
        definition_of_done=("page exists",),
        repository_id="repo-1",
        allowed_files=(),
    )
    assert backend._demo_capability_from_accepted_plan(obj) is False  # noqa: SLF001
    intent, understanding, need, evidence, gov = _chain(intent_text=obj.objective)
    request = _request(intent, understanding, evidence, gov)
    construction = construct_plan_revision(
        request=request,
        intent=intent,
        understanding=understanding,
        needs=(need,),
        evidence=evidence,
        governance=gov,
        targets=(_create(path="proposed/index.html"),),
        step_scopes=(("proposed/index.html",),),
    )
    backend._provenance.put_intent(intent)  # noqa: SLF001
    backend._provenance.put_understanding(understanding)  # noqa: SLF001
    backend._provenance.put_governance(gov)  # noqa: SLF001
    backend._provenance.put_plan(construction.plan.model_copy(update={"status": "ACCEPTED"}))  # noqa: SLF001
    obj.objective_id = intent.objective_id
    assert backend._demo_capability_from_accepted_plan(obj) is True  # noqa: SLF001
