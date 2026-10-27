"""P3: ExplorationNeed → Explore → ExplorationEvidence → Understanding v2."""

from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path

import pytest

from backend.aix_core.runtime.objective.durable_api_backend import (
    DurableObjectiveCycleApiBackend,
    _understanding_complete,
)
from backend.aix_core.runtime.objective.provenance.admit import append_user_turn, freeze_user_intent
from backend.aix_core.runtime.objective.provenance.explore import (
    ExploreHit,
    build_understanding_v2,
    class_name_hints,
    execute_need,
    needs_from_understanding,
    run_explore,
    topic_hints,
)
from backend.aix_core.runtime.objective.provenance.history import ProvenanceHistory
from backend.aix_core.runtime.objective.provenance.understanding import build_understanding_v1
from backend.aix_core.runtime.objective.provenance.validators import (
    validate_explore_gate_sources,
    validate_need_evidence,
)
from backend.aix_core.runtime.planner.execution_context import ExecutionContext

_PROVENANCE_DIR = (
    Path(__file__).resolve().parents[4] / "aix_core" / "runtime" / "objective" / "provenance"
)
_BACKEND = Path(__file__).resolve().parents[4] / "aix_core" / "runtime" / "objective" / "durable_api_backend.py"


def _kinds(revision) -> set[str]:
    return {c.kind for c in revision.claims if c.status == "ACTIVE"}


def _scope_in(revision) -> tuple[str, ...]:
    return tuple(c.text for c in revision.claims if c.kind == "SCOPE_IN" and c.status == "ACTIVE")


def _collector(*paths: str):
    def collect(need) -> tuple[ExploreHit, ...]:
        return tuple(
            ExploreHit(
                location=path,
                observation=f"Search for {need.question!r} matched {path}",
                exists=True,
                tool="search_files",
                query=need.question,
            )
            for path in paths
        )

    return collect


def _v1(text: str):
    intent = freeze_user_intent(objective_id="obj_1", text=text)
    result = build_understanding_v1(intent=intent)
    return intent, result


def _function_source(name: str) -> str:
    text = _BACKEND.read_text(encoding="utf-8")
    match = re.search(rf"    (?:async )?def {name}\(.*?(?=\n    (?:async )?def |\nclass |\Z)", text, re.S)
    assert match is not None, f"function {name} not found"
    return match.group(0)


def test_adv05_class_heuristic_produces_need_never_scope() -> None:
    intent, v1 = _v1("Refactor CronJobController")
    assert _scope_in(v1.revision) == ()
    assert "MISSING_INFO" in _kinds(v1.revision)
    assert class_name_hints(intent.text) == ("CronJobController",)
    needs = needs_from_understanding(v1.revision, intent=intent)
    assert len(needs) == 1
    assert needs[0].result_type == "symbol"
    assert "CronJobController" in needs[0].question
    run = run_explore(
        intent=intent,
        understanding=v1.revision,
        collect=_collector("app/Http/CronJobController.php", "tests/CronJobControllerTest.php"),
    )
    assert run.evidence
    assert all(item.need_id == needs[0].id or item.need_id == run.needs[0].id for item in run.evidence)
    assert "CronJobController.php" not in _scope_in(run.understanding)
    assert all(c.kind != "SCOPE_IN" for c in run.understanding.claims if c.status == "ACTIVE")


def test_adv06_topic_heuristic_produces_need_never_scope() -> None:
    intent, v1 = _v1("Implement feature flag expiry")
    assert _scope_in(v1.revision) == ()
    needs = needs_from_understanding(v1.revision, intent=intent)
    assert needs
    assert needs[0].result_type == "implementation_locations"
    assert topic_hints(intent.text)
    run = run_explore(
        intent=intent,
        understanding=v1.revision,
        collect=_collector("backend/feature_flag.py"),
    )
    assert run.evidence
    assert _scope_in(run.understanding) == ()


def test_adv08_four_search_hits_are_evidence_not_targets() -> None:
    intent, v1 = _v1("Fix login timeout")
    hits = (
        "src/auth/jwt.py",
        "src/auth/session.py",
        "docker-compose.yml",
        "README.md",
    )
    run = run_explore(intent=intent, understanding=v1.revision, collect=_collector(*hits))
    assert len(run.evidence) == 4
    assert {item.location for item in run.evidence} == set(hits)
    assert len({item.need_id for item in run.evidence}) == 1
    assert all(item.need_id == run.needs[0].id for item in run.evidence)
    assert validate_need_evidence(need=run.needs[0], evidence=run.evidence).ok is True
    assert not hasattr(run, "targets")
    uncited = [item for item in run.evidence if item.location == "docker-compose.yml"]
    assert uncited, "uncited search hits remain Evidence and unused as targets"


def test_adv15_no_glob_repository_dump() -> None:
    intent, v1 = _v1("Review this service")
    called: list[str] = []

    def collect(need):
        called.append(need.question)
        return ()

    run = run_explore(
        intent=intent,
        understanding=v1.revision,
        collect=collect,
        sources=("search_files",),
    )
    assert validate_explore_gate_sources(run.sources).ok is True
    assert "run_repository_discovery" not in run.sources
    assert "glob_fallback" not in run.sources
    assert called, "Need was executed"
    assert "MISSING_INFO" in _kinds(run.understanding)
    gate = _function_source("_run_exploration_gate")
    assert "run_repository_discovery" not in gate
    assert ".glob(" not in gate
    assert "_seed_unbound_topic_reads" not in gate


def test_adv16_url_heuristic_does_not_create_implementation_scope() -> None:
    intent, v1 = _v1("Remove the unsafe production URL hardcoded in the client")
    assert _scope_in(v1.revision) == ()
    run = run_explore(
        intent=intent,
        understanding=v1.revision,
        collect=_collector("src/lib/service-endpoints.ts"),
    )
    assert any(item.location == "src/lib/service-endpoints.ts" for item in run.evidence)
    assert "src/lib/service-endpoints.ts" not in _scope_in(run.understanding)
    gate = _function_source("_run_exploration_gate")
    assert "service-endpoints.ts" not in gate
    assert "obj.allowed_files =" not in gate


def test_adv19_readme_can_exist_as_evidence_without_becoming_a_target() -> None:
    intent, v1 = _v1("Fix login timeout")
    run = run_explore(
        intent=intent,
        understanding=v1.revision,
        collect=_collector("src/auth/jwt.py", "README.md"),
    )
    readme = next(item for item in run.evidence if item.location == "README.md")
    assert readme.need_id == run.needs[0].id
    assert "README.md" not in _scope_in(run.understanding)
    situation_locations = [
        c.text for c in run.understanding.claims if c.kind == "SITUATION" and c.status == "ACTIVE"
    ]
    assert any("README.md" in text for text in situation_locations)


def test_need_budget_is_enforced() -> None:
    intent, v1 = _v1("fix login")
    needs = needs_from_understanding(v1.revision, intent=intent, budget_files=3)
    paths = tuple(f"file_{i}.py" for i in range(12))
    evidence = execute_need(needs[0], understanding=v1.revision, collect=_collector(*paths))
    assert len(evidence) == 3
    assert validate_need_evidence(need=needs[0], evidence=evidence).ok is True


def test_every_evidence_record_has_valid_need_id() -> None:
    intent, v1 = _v1("fix login")
    run = run_explore(
        intent=intent,
        understanding=v1.revision,
        collect=_collector("a.py", "b.py"),
    )
    need_ids = {need.id for need in run.needs}
    assert run.evidence
    for item in run.evidence:
        assert item.need_id in need_ids
        assert item.provenance.tool
        assert item.provenance.query
        assert item.provenance.fingerprint


def test_explore_does_not_invent_a_need() -> None:
    intent, v1 = _v1("Change only schema.sql")
    assert "MISSING_INFO" not in _kinds(v1.revision)
    invented = []

    def collect(need):
        invented.append(need)
        return _collector("secret.py")(need)

    run = run_explore(intent=intent, understanding=v1.revision, collect=collect)
    assert run.needs == ()
    assert run.evidence == ()
    assert invented == []


def test_ambiguity_blocks_explore() -> None:
    intent, v1 = _v1(
        "Can you create an attendance registration system with a frontend in HTML, "
        "a backend in PHP, and a database in MySQL? Please provide only the schema, "
        "as I will create it myself."
    )
    assert v1.decision == "NEED_CLARIFICATION"
    run = run_explore(
        intent=intent,
        understanding=v1.revision,
        collect=_collector("schema.sql", "index.html"),
    )
    assert run.needs == ()
    assert run.evidence == ()
    assert "SITUATION" not in _kinds(run.understanding)


def test_situation_and_cause_cite_evidence_and_stay_hypothesized() -> None:
    intent, v1 = _v1("fix login")
    run = run_explore(
        intent=intent,
        understanding=v1.revision,
        collect=_collector("src/auth/login.py"),
    )
    situations = [c for c in run.understanding.claims if c.kind == "SITUATION"]
    causes = [c for c in run.understanding.claims if c.kind == "CAUSE"]
    assert situations
    assert causes
    for claim in (*situations, *causes):
        assert claim.standing == "HYPOTHESIZED"
        assert all(cite.kind == "EVIDENCE" for cite in claim.cites)
        assert claim.cites[0].ref in {item.id for item in run.evidence}
    assert causes[0].standing == "HYPOTHESIZED"


def test_missing_info_cleared_only_by_evidence_grounded_revision() -> None:
    intent, v1 = _v1("fix login")
    empty = run_explore(intent=intent, understanding=v1.revision, collect=_collector())
    assert any(c.kind == "MISSING_INFO" and c.status == "ACTIVE" for c in empty.understanding.claims)
    assert empty.decision == "NEED_EVIDENCE"
    filled = run_explore(
        intent=intent,
        understanding=v1.revision,
        collect=_collector("src/auth/login.py"),
    )
    missing = [c for c in filled.understanding.claims if c.kind == "MISSING_INFO"]
    assert missing
    assert all(c.status == "SUPERSEDED" for c in missing)
    assert filled.understanding.id != v1.revision.id
    assert filled.understanding.parent_revision_id == v1.revision.id


def test_u7_preserved_across_v2_and_user_turns() -> None:
    intent = freeze_user_intent(objective_id="obj_1", text="Change only schema.sql")
    v1 = build_understanding_v1(intent=intent)
    schema = next(c for c in v1.revision.claims if c.kind == "SCOPE_IN")
    turn = append_user_turn(intent=intent, text="Keep schema.sql in scope.")
    v1b = build_understanding_v1(intent=intent, turns=(turn,), parent=v1.revision)
    child_schema = next(c for c in v1b.revision.claims if c.id == schema.id)
    assert child_schema.status == "ACTIVE"
    run = run_explore(
        intent=intent,
        understanding=v1b.revision,
        collect=_collector("README.md"),
        turns=(turn,),
    )
    still = next(c for c in run.understanding.claims if c.id == schema.id)
    assert still.status == "ACTIVE"
    assert still.kind == "SCOPE_IN"


def test_k10_files_read_is_not_understanding_complete() -> None:
    context = ExecutionContext(objective_id="obj_1", execution_root="/tmp/repo")
    assert (
        _understanding_complete(
            task_family="REVIEW",
            context=context,
            target_path=None,
            symbol_name=None,
        )
        is False
    )


def test_p3_path_does_not_mutate_allowed_files() -> None:
    gate = _function_source("_run_exploration_gate")
    assert "obj.allowed_files =" not in gate
    fulfill = _function_source("_fulfill_planner_evidence_requests")
    assert "hits[:4]" not in fulfill
    assert "toolbox.read_file, path" not in fulfill


def test_p2_never_imports_or_calls_explore() -> None:
    source = (_PROVENANCE_DIR / "understanding.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert node.module != "backend.aix_core.runtime.objective.provenance.explore"
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name != "explore"
    assert "run_explore" not in source
    assert "needs_from_understanding" not in source


def test_explore_module_has_no_glob_or_discovery() -> None:
    source = (_PROVENANCE_DIR / "explore.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert "discovery" not in node.module
            assert node.module != "glob"
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name not in {"glob", "pathlib"}
    assert "run_repository_discovery" not in source
    assert "rglob" not in source


def test_need_source_and_result_type_enums() -> None:
    intent, v1 = _v1("fix login")
    needs = needs_from_understanding(v1.revision, intent=intent)
    assert needs[0].source in {"repository", "cycle_workspace"}
    assert needs[0].result_type in {
        "implementation_locations",
        "file_content",
        "symbol",
        "related_tests",
        "related_routes",
        "existence",
    }
    ws_intent, ws_v1 = _v1("Inspect the cycle workspace for the missing artifact")
    ws_needs = needs_from_understanding(ws_v1.revision, intent=ws_intent)
    assert ws_needs[0].source == "cycle_workspace"


def test_history_stores_needs_and_evidence() -> None:
    intent, v1 = _v1("fix login")
    history = ProvenanceHistory()
    history.put_intent(intent)
    history.put_understanding(v1.revision)
    run = run_explore(
        intent=intent,
        understanding=v1.revision,
        collect=_collector("src/auth/login.py"),
    )
    history.put_need(run.needs[0])
    history.put_evidence(run.evidence[0])
    assert history.needs(intent.objective_id)[0].id == run.needs[0].id
    assert history.evidence(intent.objective_id)[0].need_id == run.needs[0].id


def test_n5_rejects_glob_and_discovery_sources() -> None:
    intent, v1 = _v1("Review this service")
    run = run_explore(
        intent=intent,
        understanding=v1.revision,
        collect=_collector("a.py"),
        sources=("glob_fallback",),
    )
    assert run.validation.ok is False
    assert run.validation.rule_id == "N5"
    assert run.evidence == ()


def test_v2_does_not_mint_scope_in_from_evidence_locations() -> None:
    intent, v1 = _v1("fix login")
    run = run_explore(
        intent=intent,
        understanding=v1.revision,
        collect=_collector("backend/.tmp/census/schema.sql"),
    )
    result = build_understanding_v2(
        intent=intent,
        parent=v1.revision,
        evidence=run.evidence,
        needs=run.needs,
    )
    assert "backend/.tmp/census/schema.sql" not in _scope_in(result.revision)
    assert _scope_in(result.revision) == ()


@pytest.mark.asyncio
async def test_live_gate_does_not_write_allowed_files_from_heuristics() -> None:
    backend = DurableObjectiveCycleApiBackend(mode="inprocess")
    admitted = await backend.command(
        operation="admit_objective",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=None,
        body={
            "workhub_task_id": "wh-task-1",
            "objective": "Refactor CronJobController",
            "task_class": "bounded_repair",
            "definition_of_done": ("done",),
            "repository_id": "aix",
            "allowed_files": (),
        },
        idempotency_key="p3-adv05-admit",
        expected_version=None,
    )
    assert admitted["ok"] is True
    obj = backend._objectives[admitted["objective_id"]]  # noqa: SLF001
    assert tuple(obj.allowed_files or ()) == ()
    src = inspect.getsource(backend._run_exploration_gate)
    assert "obj.allowed_files =" not in src
    assert "resolve_class_hints_in_repository" not in src
    assert "topic_hits_are_mutation_scope" not in src
