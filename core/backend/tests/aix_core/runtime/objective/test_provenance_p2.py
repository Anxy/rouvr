"""P2: Understanding v1 from Intent + UserTurns only. No Explore, no world."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from backend.aix_core.runtime.objective.contracts import BudgetV1
from backend.aix_core.runtime.objective.durable_api_backend import DurableObjectiveCycleApiBackend
from backend.aix_core.runtime.objective.obligations import derive_obligations
from backend.aix_core.runtime.objective.provenance.admit import freeze_user_intent
from backend.aix_core.runtime.objective.provenance.contracts import GovernanceRevision
from backend.aix_core.runtime.objective.provenance.understanding import (
    V1_CLAIM_KINDS,
    V1_FORBIDDEN_KINDS,
    build_understanding_v1,
)

_PROVENANCE_DIR = (
    Path(__file__).resolve().parents[4] / "aix_core" / "runtime" / "objective" / "provenance"
)

ADV01 = (
    "Can you create an attendance registration system with a frontend in HTML, "
    "a backend in PHP, and a database in MySQL? Please provide only the schema, "
    "as I will create it myself."
)


def _budget() -> BudgetV1:
    return BudgetV1(time_seconds=60, tokens=1000, cost_usd=1.0, retries=1, steps=4, concurrency=1)


def _governance(*, create_root: tuple[str, ...] = ("proposed/",)) -> GovernanceRevision:
    return GovernanceRevision(
        objective_id="obj_1",
        create_capability=("CREATE_FILE",),
        create_root=create_root,
        task_class="code_change",
        budgets=_budget(),
    )


def _kinds(revision) -> set[str]:
    return {c.kind for c in revision.claims if c.status == "ACTIVE"}


def _scope_in(revision) -> tuple[str, ...]:
    return tuple(c.text for c in revision.claims if c.kind == "SCOPE_IN" and c.status == "ACTIVE")


def test_adv01_pre_clarification_is_need_clarification() -> None:
    intent = freeze_user_intent(objective_id="obj_1", text=ADV01)
    result = build_understanding_v1(intent=intent)
    assert result.decision == "NEED_CLARIFICATION"
    assert "AMBIGUITY" in _kinds(result.revision)
    assert result.validation.rule_id == "U6"
    assert "SITUATION" not in _kinds(result.revision)
    assert "CAUSE" not in _kinds(result.revision)


def test_only_schema_sql_is_scope_in() -> None:
    intent = freeze_user_intent(objective_id="obj_1", text="Change only schema.sql")
    result = build_understanding_v1(intent=intent)
    assert result.revision.scope_mode == "BOUNDED"
    assert "schema.sql" in _scope_in(result.revision)
    assert result.decision != "NEED_CLARIFICATION"


def test_fix_login_does_not_invent_file_scope() -> None:
    intent = freeze_user_intent(objective_id="obj_1", text="fix login")
    result = build_understanding_v1(intent=intent)
    assert _scope_in(result.revision) == ()
    assert "MISSING_INFO" in _kinds(result.revision)
    missing = next(c for c in result.revision.claims if c.kind == "MISSING_INFO")
    assert all(cite.kind == "CLAIM" for cite in missing.cites)


def test_whatever_files_you_need_is_open_implementation() -> None:
    intent = freeze_user_intent(
        objective_id="obj_1",
        text="Fix login, whatever files you need",
    )
    result = build_understanding_v1(intent=intent)
    assert result.revision.scope_mode == "OPEN_IMPLEMENTATION"
    assert _scope_in(result.revision) == ()


def test_governance_proposed_never_becomes_scope_in() -> None:
    intent = freeze_user_intent(
        objective_id="obj_1",
        text="Build a landing page in proposed/index.html",
    )
    result = build_understanding_v1(intent=intent, governance=_governance())
    assert "proposed/index.html" not in _scope_in(result.revision)
    assert all("proposed" not in text.replace("\\", "/").split("/")[0] for text in _scope_in(result.revision) or ("",))
    # Named CREATE under create_root answers artifact identity without SCOPE_IN (U3).
    assert "MISSING_INFO" not in _kinds(result.revision)
    assert result.decision == "ACCEPT"


def test_greenfield_proposed_named_files_accept_without_scope_in() -> None:
    intent = freeze_user_intent(
        objective_id="obj_1",
        text=(
            "Create a greenfield attendance registration system under proposed/attendance/, "
            "including proposed/attendance/index.html, proposed/attendance/style.css, "
            "proposed/attendance/register.php, proposed/attendance/list.php, and "
            "proposed/attendance/schema.sql, with an HTML frontend, PHP backend, and MySQL schema."
        ),
    )
    result = build_understanding_v1(intent=intent, governance=_governance())
    assert _scope_in(result.revision) == ()
    assert "MISSING_INFO" not in _kinds(result.revision)
    assert "AMBIGUITY" not in _kinds(result.revision)
    assert result.decision == "ACCEPT"


def test_proposed_paths_without_create_capability_still_need_evidence() -> None:
    intent = freeze_user_intent(
        objective_id="obj_1",
        text="Create proposed/attendance/index.html",
    )
    gov = GovernanceRevision(
        objective_id="obj_1",
        create_capability=(),
        create_root=("proposed/",),
        task_class="code_change",
        budgets=_budget(),
    )
    result = build_understanding_v1(intent=intent, governance=gov)
    assert _scope_in(result.revision) == ()
    assert "MISSING_INFO" in _kinds(result.revision)
    assert result.decision == "NEED_EVIDENCE"


def test_client_facet_dod_is_not_scope_unless_user_authored() -> None:
    intent = freeze_user_intent(
        objective_id="obj_1",
        text="Only the MySQL schema",
        definition_of_done=("Frontend / UI surface",),
    )
    assert intent.dod_user_authored is False
    result = build_understanding_v1(intent=intent)
    assert "Frontend / UI surface" not in _scope_in(result.revision)
    assert all("frontend" not in text.casefold() for text in _scope_in(result.revision))


def test_missing_info_cites_claim() -> None:
    intent = freeze_user_intent(objective_id="obj_1", text="fix login")
    result = build_understanding_v1(intent=intent)
    missing = next(c for c in result.revision.claims if c.kind == "MISSING_INFO")
    assert missing.cites and all(c.kind == "CLAIM" for c in missing.cites)
    outcome_ids = {c.id for c in result.revision.claims if c.kind == "OUTCOME"}
    assert missing.cites[0].ref in outcome_ids


def test_v1_cannot_emit_situation_or_cause() -> None:
    intent = freeze_user_intent(objective_id="obj_1", text="fix login")
    result = build_understanding_v1(intent=intent)
    assert _kinds(result.revision) <= V1_CLAIM_KINDS
    assert not (_kinds(result.revision) & V1_FORBIDDEN_KINDS)


def test_active_ambiguity_prevents_plan_acceptance() -> None:
    intent = freeze_user_intent(objective_id="obj_1", text=ADV01)
    result = build_understanding_v1(intent=intent)
    assert result.decision == "NEED_CLARIFICATION"
    assert result.validation.ok is False
    assert result.validation.decision == "NEED_CLARIFICATION"


def test_k13_derive_obligations_does_not_mint_facet_what() -> None:
    obligations = derive_obligations(
        objective_id="obj-1",
        objective=ADV01,
        definition_of_done=("Frontend / UI surface", "scoped change complete"),
    )
    descriptions = [o.description for o in obligations]
    assert "Frontend / UI surface" not in descriptions
    assert "Backend / server runtime" not in descriptions


def test_p2_imports_no_explore_implementation() -> None:
    source = (_PROVENANCE_DIR / "understanding.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert "explore" not in node.module
            assert "discovery" not in node.module
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert "explore" not in alias.name
                assert alias.name not in {"glob", "os"}
    assert "run_explore" not in source
    assert "execute_need" not in source


def test_p2_does_not_consult_repository_state() -> None:
    source = (_PROVENANCE_DIR / "understanding.py").read_text(encoding="utf-8")
    forbidden = (
        "run_repository_discovery",
        "search_files",
        "glob",
        "listdir",
        "read_text",
        "rglob",
    )
    for token in forbidden:
        assert token not in source


@pytest.mark.asyncio
async def test_admit_records_understanding_v1_without_explore() -> None:
    backend = DurableObjectiveCycleApiBackend(mode="inprocess")
    result = await backend.command(
        operation="admit_objective",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=None,
        body={
            "workhub_task_id": "wh-task-1",
            "objective": ADV01,
            "task_class": "bounded_repair",
            "definition_of_done": ("Frontend / UI surface",),
            "repository_id": "aix",
            "allowed_files": (),
        },
        idempotency_key="p2-adv01-admit",
        expected_version=None,
    )
    assert result["ok"] is True
    assert result["understanding_decision"] == "NEED_CLARIFICATION"
    revision = backend._provenance.latest_understanding(result["objective_id"])  # noqa: SLF001
    assert revision is not None
    assert any(c.kind == "AMBIGUITY" and c.status == "ACTIVE" for c in revision.claims)
    assert not any(c.kind in V1_FORBIDDEN_KINDS for c in revision.claims)


def test_do_not_create_mock_is_not_scope_out_create() -> None:
    """Quality bans like 'Do not create mock-only' must not SCOPE_OUT the verb create."""
    text = (
        "Create a complete greenfield ERP Mini-System under proposed/erp-system/. "
        "Governance create_root = proposed/. CREATE index.html, styles.css, app.js, "
        "api.php, config.php, database.sql, and README.md. "
        "Do not create mock-only functionality or disconnected UI. "
        "Do not modify existing repo files outside proposed/."
    )
    intent = freeze_user_intent(objective_id="obj_erp", text=text)
    result = build_understanding_v1(intent=intent, governance=_governance())
    scope_out = tuple(
        c.text.casefold()
        for c in result.revision.claims
        if c.kind == "SCOPE_OUT" and c.status == "ACTIVE"
    )
    assert "create" not in scope_out
    assert "modify" not in scope_out
    assert "proposed" not in scope_out
    assert "proposed/" not in scope_out
    # mock-only must not flip scope_mode to BOUNDED
    assert result.revision.scope_mode == "OPEN_IMPLEMENTATION"


def test_mock_only_does_not_bound_scope_mode() -> None:
    from backend.aix_core.runtime.objective.provenance.understanding import derive_scope_mode

    assert derive_scope_mode("Do not create mock-only functionality") == "OPEN_IMPLEMENTATION"
    assert derive_scope_mode("Change only schema.sql") == "BOUNDED"
    assert derive_scope_mode("production-only build") == "OPEN_IMPLEMENTATION"


def test_product_class_create_without_named_files_is_not_missing_info() -> None:
    intent = freeze_user_intent(
        objective_id="obj_expense",
        text=(
            "Build a complete Expense Tracker web app using FastAPI + Python backend, "
            "HTML/CSS/JavaScript frontend, and SQLite. Include registration/login, "
            "add/edit/delete expenses, categories, monthly budgets, dashboard totals, "
            "and README."
        ),
    )
    result = build_understanding_v1(intent=intent, governance=_governance())
    assert _scope_in(result.revision) == ()
    assert "MISSING_INFO" not in _kinds(result.revision)
    assert result.decision == "ACCEPT"
