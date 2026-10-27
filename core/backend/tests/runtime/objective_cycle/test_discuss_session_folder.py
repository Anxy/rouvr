"""Chat with AIX must create one .tmp session folder for discuss/plan/token/objective."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from backend.aix_core.runtime.executor.workspace_isolation import workspace_date_key
from backend.aix_core.runtime.objective.discuss_store import (
    discuss_root,
    persist_discuss_turn,
    persist_session_plan,
)

# Avoid pytest tmp_path on this machine (WinError 5 under AppData\\Local\\Temp).
_WS_ROOT = Path(__file__).resolve().parents[5] / ".tmp" / "discuss-session-folder-tests"


@pytest.fixture
def workspace_base(request: pytest.FixtureRequest) -> Path:
    base = _WS_ROOT / request.node.name
    if base.exists():
        shutil.rmtree(base, ignore_errors=True)
    base.mkdir(parents=True, exist_ok=True)
    yield base
    shutil.rmtree(base, ignore_errors=True)


def _persist(workspace_base: Path, *, thread_id: str = "disc_session_folder") -> dict:
    return persist_discuss_turn(
        tenant_id="tenant-live",
        actor_id="actor-test",
        thread_id=thread_id,
        messages=[{"role": "user", "content": "Create an attendance page."}],
        reply="Click Create & run with AIX.",
        task_class="greenfield_page",
        proposed_scope=["proposed/index.html"],
        ambiguous=False,
        provider="openrouter",
        model="test/model",
        usage={"prompt_tokens": 12, "completion_tokens": 4, "total_tokens": 16},
        workspace_base=workspace_base,
        normalized_objective="Create an attendance page.",
        intake_scope_code="INTAKE_SCOPE_PRESERVED",
    )


def test_discuss_turn_creates_dated_session_folder_with_core_files(workspace_base: Path) -> None:
    persisted = _persist(workspace_base)
    session = Path(persisted["session_path"])
    expected = workspace_base / "tenant-live" / workspace_date_key() / "disc_session_folder"
    assert session == expected
    assert session.is_dir()
    for name in ("discuss.md", "plan.md", "objective.json", "token_usage.json"):
        assert (session / name).is_file(), name
    objective = json.loads((session / "objective.json").read_text(encoding="utf-8"))
    assert objective["normalized_objective"] == "Create an attendance page."
    plan = (session / "plan.md").read_text(encoding="utf-8")
    assert "# AIX Discuss Plan" in plan
    assert "Create an attendance page." in plan
    discuss = (session / "discuss.md").read_text(encoding="utf-8")
    assert "Create an attendance page." in discuss
    usage = json.loads((session / "token_usage.json").read_text(encoding="utf-8"))
    assert usage["cumulative"]["total_tokens"] == 16


def test_second_turn_stays_in_the_same_session_folder(workspace_base: Path) -> None:
    first = _persist(workspace_base)
    second = persist_discuss_turn(
        tenant_id="tenant-live",
        actor_id="actor-test",
        thread_id="disc_session_folder",
        messages=[{"role": "user", "content": "Yes, create it."}],
        reply="Ready.",
        task_class="greenfield_page",
        proposed_scope=["proposed/index.html"],
        ambiguous=False,
        provider="openrouter",
        model="test/model",
        usage={"prompt_tokens": 8, "completion_tokens": 2, "total_tokens": 10},
        workspace_base=workspace_base,
        normalized_objective="Create an attendance page.",
    )
    assert Path(second["session_path"]) == Path(first["session_path"])
    usage = json.loads(Path(second["token_usage_path"]).read_text(encoding="utf-8"))
    assert usage["cumulative"]["total_tokens"] == 26


def test_legacy_discuss_tree_is_reused(workspace_base: Path) -> None:
    thread = "disc_legacy"
    legacy = workspace_base / "discuss" / "tenant-live" / thread
    legacy.mkdir(parents=True)
    root = discuss_root(tenant_id="tenant-live", thread_id=thread, workspace_base=workspace_base)
    assert root == legacy
    persisted = _persist(workspace_base, thread_id=thread)
    assert Path(persisted["session_path"]) == legacy


def test_accepted_plan_overwrites_session_plan_md(workspace_base: Path) -> None:
    persisted = _persist(workspace_base)
    session = persist_session_plan(
        tenant_id="tenant-live",
        thread_id="disc_session_folder",
        plan_md="# AIX Accepted Plan\n\n## Original Objective\n\nCreate an attendance page.\n",
        objective={"objective_id": "obj_1", "cycle_id": "cyc_1", "status": "ACCEPTED"},
        workspace_base=workspace_base,
    )
    assert session == Path(persisted["session_path"])
    assert "Accepted Plan" in (session / "plan.md").read_text(encoding="utf-8")
    objective = json.loads((session / "objective.json").read_text(encoding="utf-8"))
    assert objective["objective_id"] == "obj_1"
    assert objective["normalized_objective"] == "Create an attendance page."
    assert objective["status"] == "ACCEPTED"
