"""Intake must never silently drop explicit multi-deliverable user scope."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from backend.aix_core.runtime.objective.discuss_store import persist_discuss_turn
from backend.infrastructure.model_gateway.intelligence_layer.intake_objective_scope import (
    extract_explicit_deliverables,
    resolve_normalized_objective,
    validate_intake_normalized_objective,
)
from backend.infrastructure.model_gateway.intelligence_layer.prompts import planner_intake_prompt

_WS_ROOT = Path(__file__).resolve().parents[5] / ".tmp" / "intake-scope-tests"


ATTENDANCE_ORIGINAL = (
    "Can you create an attendance registration system with a frontend in HTML, "
    "a backend in PHP, and a database in MySQL? Please provide only the schema, "
    "as I will create it myself."
)

ATTENDANCE_REDUCED = (
    "Provide only the MySQL database schema needed for an attendance registration "
    "system intended for an HTML frontend and PHP backend."
)

ATTENDANCE_PRESERVED = (
    "Create an attendance registration system with an HTML frontend, a PHP backend, "
    "and a MySQL database. Constraint: provide only the schema; I will create it myself."
)


def test_intake_prompt_forbids_scope_reduction() -> None:
    prompt = planner_intake_prompt(["single_file_helper", "bounded_repair"])
    assert "WITHOUT reducing scope" in prompt or "never reduce scope" in prompt.lower() or (
        "Preserve every explicit deliverable" in prompt
    )
    assert "constraints" in prompt
    assert "intended for" in prompt or "supporting" in prompt


def test_attendance_extracts_all_stack_deliverables() -> None:
    roles = {d.role for d in extract_explicit_deliverables(ATTENDANCE_ORIGINAL)}
    assert {"frontend", "backend", "database"} <= roles


def test_attendance_reduced_rewrite_is_rejected_and_falls_back() -> None:
    result = validate_intake_normalized_objective(
        original_objective=ATTENDANCE_ORIGINAL,
        normalized_objective=ATTENDANCE_REDUCED,
    )
    assert result.ok is False
    assert result.code == "INTAKE_SCOPE_REDUCED"
    missing_roles = {m.role for m in result.missing}
    assert "frontend" in missing_roles
    assert "backend" in missing_roles

    resolved, meta = resolve_normalized_objective(
        original_objective=ATTENDANCE_ORIGINAL,
        normalized_objective=ATTENDANCE_REDUCED,
    )
    assert meta.used_fallback is True
    assert resolved == ATTENDANCE_ORIGINAL


def test_attendance_preserved_rewrite_is_accepted() -> None:
    result = validate_intake_normalized_objective(
        original_objective=ATTENDANCE_ORIGINAL,
        normalized_objective=ATTENDANCE_PRESERVED,
    )
    assert result.ok is True
    assert result.code == "INTAKE_SCOPE_OK"
    resolved, meta = resolve_normalized_objective(
        original_objective=ATTENDANCE_ORIGINAL,
        normalized_objective=ATTENDANCE_PRESERVED,
    )
    assert meta.used_fallback is False
    assert resolved == ATTENDANCE_PRESERVED


@pytest.mark.parametrize(
    ("original", "reduced"),
    [
        (
            "Build a billing portal with a React frontend, a Go backend, and a Postgres database. "
            "Just return the API contract for now.",
            "Return only the API contract for a billing portal intended for a React frontend "
            "and Go backend.",
        ),
        (
            "Implement inventory tracking with a mobile client, a NestJS backend, and Redis. "
            "Provide only the migration plan.",
            "Provide only the migration plan for inventory tracking supporting a mobile client "
            "and NestJS backend.",
        ),
        (
            "Create a docs site with a static frontend and a search API. Do not deploy. "
            "Only output the IA outline.",
            "Only output the IA outline for a docs site intended for a static frontend.",
        ),
    ],
)
def test_multi_deliverable_restrictive_clause_cannot_silently_reduce(
    original: str, reduced: str
) -> None:
    result = validate_intake_normalized_objective(
        original_objective=original,
        normalized_objective=reduced,
    )
    assert result.ok is False
    assert result.code == "INTAKE_SCOPE_REDUCED"
    resolved, meta = resolve_normalized_objective(
        original_objective=original,
        normalized_objective=reduced,
    )
    assert meta.used_fallback is True
    assert resolved == original


def test_explicit_cancel_removes_deliverable_from_required_scope() -> None:
    original = (
        "Create a React frontend and a Node backend for the portal. "
        "Do not create the backend."
    )
    roles = {d.role for d in extract_explicit_deliverables(original)}
    assert "frontend" in roles
    assert "backend" not in roles

    normalized = "Create a React frontend for the portal. Do not create the backend."
    result = validate_intake_normalized_objective(
        original_objective=original,
        normalized_objective=normalized,
    )
    assert result.ok is True


def test_restrictive_only_ask_is_not_forced_to_invent_extra_deliverables() -> None:
    original = "Please provide only the database schema for attendance registration."
    normalized = "Provide only the database schema for attendance registration."
    result = validate_intake_normalized_objective(
        original_objective=original,
        normalized_objective=normalized,
    )
    assert result.ok is True


def test_persist_discuss_turn_writes_raw_intake_json() -> None:
    workspace_base = _WS_ROOT / "persist_raw_intake"
    if workspace_base.exists():
        shutil.rmtree(workspace_base, ignore_errors=True)
    workspace_base.mkdir(parents=True, exist_ok=True)
    try:
        raw = json.dumps(
            {
                "reply": "Click Create & run with AIX.",
                "normalized_objective": ATTENDANCE_REDUCED,
                "constraints": ["provide only the schema"],
                "ambiguous": False,
            }
        )
        persisted = persist_discuss_turn(
            tenant_id="tenant-test",
            actor_id="actor-test",
            thread_id="disc_test_intake_raw",
            messages=[{"role": "user", "content": ATTENDANCE_ORIGINAL}],
            reply="Click Create & run with AIX.",
            task_class=None,
            proposed_scope=[],
            ambiguous=False,
            provider="openrouter",
            model="test/model",
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            workspace_base=workspace_base,
            raw_model_output=raw,
            normalized_objective=ATTENDANCE_ORIGINAL,
            intake_scope_code="INTAKE_SCOPE_REDUCED",
            intake_scope_fallback=True,
        )
        intake_path = Path(persisted["intake_raw_path"])
        assert intake_path.is_file()
        doc = json.loads(intake_path.read_text(encoding="utf-8"))
        assert doc["turns"][0]["raw_model_output"] == raw
        assert doc["turns"][0]["raw_model_json"]["normalized_objective"] == ATTENDANCE_REDUCED
        assert doc["turns"][0]["intake_scope_fallback"] is True

        transcript = json.loads(Path(persisted["transcript_path"]).read_text(encoding="utf-8"))
        assert transcript["turns"][0]["raw_model_output"] == raw
        assert transcript["turns"][0]["normalized_objective"] == ATTENDANCE_ORIGINAL

        session = Path(persisted["session_path"])
        assert session.is_dir()
        assert (session / "discuss.md").is_file()
        assert (session / "plan.md").is_file()
        assert (session / "token_usage.json").is_file()
        assert (session / "objective.json").is_file()
        objective = json.loads((session / "objective.json").read_text(encoding="utf-8"))
        assert objective["normalized_objective"] == ATTENDANCE_ORIGINAL
        assert "tenant-test" in session.parts
        assert "disc_test_intake_raw" in session.parts
        assert "discuss" not in session.parts
    finally:
        shutil.rmtree(workspace_base, ignore_errors=True)
