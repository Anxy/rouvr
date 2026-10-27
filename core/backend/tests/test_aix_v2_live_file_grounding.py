from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from backend.services.aix_quality_enforcement_service import QualityEnforcementError
from backend.services.aix_quality_enforcement_service import validate as validate_quality
from backend.services.aix_v2_live_file_grounding_service import (
    V2LiveFileGroundingError,
    validate_v2_edit_against_live_file,
)
from backend.legacy.orphaned_services.devexecutor_plan_validator_service import validate_planned_steps
from backend.services.devexecutor_planner_bridge_service import _normalize_planner_steps
from backend.services.devexecutor_tool_loop_service import execute_tool_step


def _service_workspace(tmp_path: Path) -> tuple[Path, Path]:
    service_root = tmp_path / "services" / "hyeve-codeintel-service"
    service_root.mkdir(parents=True, exist_ok=True)
    return service_root, service_root


def _planner_packet_with_anchor(snippet: str, *, target_file: str = "backend/service.py") -> dict[str, Any]:
    return {
        "schema_name": "aix.planner.slice_packet.v2",
        "target_file": target_file,
        "allowed_surface": {"anchor": {"symbol": "backend.service.run", "compact_snippet": snippet}},
        "validation_command": f"python -m py_compile {target_file}",
    }


def test_v2_live_grounding_accepts_search_and_range_without_preread(tmp_path: Path) -> None:
    workspace_root, service_root = _service_workspace(tmp_path)
    target = service_root / "backend" / "service.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("def run():\n    return 1\n", encoding="utf-8")

    search_grounding = validate_v2_edit_against_live_file(
        workspace_root=workspace_root,
        args={
            "relative_path": "services/hyeve-codeintel-service/backend/service.py",
            "grounding_source": "anchor",
            "search_range": {"start_line": 1, "end_line": 2},
            "replacement_text": "def run():\n    return 2\n",
        },
        planner_packet=_planner_packet_with_anchor("def run():\n    return 1\n"),
    )
    assert search_grounding.mode == "search_range"
    assert search_grounding.relative_path == "backend/service.py"
    assert search_grounding.target_resolution.exists is True
    assert search_grounding.grounding_source == "anchor"

    range_grounding = validate_v2_edit_against_live_file(
        workspace_root=workspace_root,
        args={
            "relative_path": "services/hyeve-codeintel-service/backend/service.py",
            "grounding_source": "anchor",
            "search_range": {"start_line": 1, "end_line": 2},
            "replace_text": "def run():\n    return 3\n",
        },
    )
    assert range_grounding.mode == "search_range"
    assert range_grounding.derived_search_text == "def run():\n    return 1\n"


def test_v2_live_grounding_rejects_missing_search_in_live_file(tmp_path: Path) -> None:
    workspace_root, service_root = _service_workspace(tmp_path)
    target = service_root / "backend" / "service.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("def run():\n    return 1\n", encoding="utf-8")

    with pytest.raises(V2LiveFileGroundingError) as exc_info:
        validate_v2_edit_against_live_file(
            workspace_root=workspace_root,
            args={
                "relative_path": "services/hyeve-codeintel-service/backend/service.py",
                "grounding_source": "anchor",
                "search_range": {"start_line": 5, "end_line": 6},
                "replacement_text": "def missing():\n    return 1\n",
            },
            planner_packet=_planner_packet_with_anchor("def missing():\n    return 0\n    return 1\n"),
        )

    assert exc_info.value.code == "EDIT_FILE_SEARCH_NOT_FOUND"


def test_v2_live_grounding_accepts_read_grounded_search(tmp_path: Path) -> None:
    workspace_root, service_root = _service_workspace(tmp_path)
    target = service_root / "backend" / "service.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("def run():\n    return 1\n", encoding="utf-8")

    result = validate_v2_edit_against_live_file(
        workspace_root=workspace_root,
        args={
            "relative_path": "services/hyeve-codeintel-service/backend/service.py",
            "grounding_source": "read_range",
            "read_step_idx": 1,
            "edit_range_within_read": {"start_line": 1, "end_line": 2},
            "replace_text": "def run():\n    return 2\n",
        },
        read_buffers={
            "backend/service.py": {"text": "def run():\n    return 1\n", "step_idx": 1, "start_line": 1, "end_line": 2}
        },
    )

    assert result.grounding_source == "explicit_read_output"
    assert result.mode == "read_range_within_read"
    assert result.search_range == {"start_line": 1, "end_line": 2}


def test_v2_live_grounding_rejects_hallucinated_search_without_anchor_or_read(tmp_path: Path) -> None:
    workspace_root, service_root = _service_workspace(tmp_path)
    target = service_root / "backend" / "service.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("def run():\n    return 1\n", encoding="utf-8")

    with pytest.raises(V2LiveFileGroundingError) as exc_info:
        validate_v2_edit_against_live_file(
            workspace_root=workspace_root,
            args={
                "relative_path": "services/hyeve-codeintel-service/backend/service.py",
                "grounding_source": "anchor",
                "search": "def run():\n    return 1\n    # guessed line\n",
                "replacement_text": "def run():\n    return 2\n",
            },
            planner_packet=_planner_packet_with_anchor("def run():\n    return 1\n"),
        )

    assert exc_info.value.code == "EDIT_FILE_TEXTUAL_SEARCH_FORBIDDEN"


def test_v2_live_grounding_rejects_partial_nonverbatim_search(tmp_path: Path) -> None:
    workspace_root, service_root = _service_workspace(tmp_path)
    target = service_root / "backend" / "service.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("def run():\n    return 1\n", encoding="utf-8")

    with pytest.raises(V2LiveFileGroundingError) as exc_info:
        validate_v2_edit_against_live_file(
            workspace_root=workspace_root,
            args={
                "relative_path": "services/hyeve-codeintel-service/backend/service.py",
                "grounding_source": "anchor",
                "search_replace_blocks": "<<<<<<< SEARCH\nreturn  1\n=======\nreturn  2\n>>>>>>> REPLACE\n",
            },
            planner_packet=_planner_packet_with_anchor("def run():\n    return 1\n"),
        )

    assert exc_info.value.code == "EDIT_FILE_TEXTUAL_PATCH_BLOCKS_FORBIDDEN"


def test_v2_validate_planned_steps_allows_self_contained_edit_without_preread(tmp_path: Path) -> None:
    workspace_root, service_root = _service_workspace(tmp_path)
    target = service_root / "backend" / "indexing" / "runner.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("def run() -> None:\n    return None\n", encoding="utf-8")

    error = validate_planned_steps(
        steps=[
            {
                "tool": "edit_file",
                "args": {
                    "relative_path": "services/hyeve-codeintel-service/backend/indexing/runner.py",
                    "grounding_source": "anchor",
                    "search_range": {"start_line": 1, "end_line": 2},
                    "replacement_text": "def run() -> None:\n    return 1\n",
                },
            },
            {"tool": "cmd.run", "args": {"command_text": "python -m py_compile backend/indexing/runner.py"}},
        ],
        task_intent="code_change_required",
        metadata={
            "target_files": ["backend/indexing/runner.py"],
            "validation_commands": ["python -m py_compile backend/indexing/runner.py"],
            "planner_packet": {
                "schema_name": "aix.planner.slice_packet.v2",
                "target_file": "backend/indexing/runner.py",
                "allowed_surface": {
                    "anchor": {
                        "symbol": "backend.indexing.runner.run",
                        "compact_snippet": "def run() -> None:\n    return None\n",
                    }
                },
            },
        },
        workspace_root=workspace_root,
    )

    assert error is None


def test_v2_quality_and_runtime_ground_from_live_file_without_preread(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    workspace_root, service_root = _service_workspace(tmp_path)
    target = service_root / "backend" / "service.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("def run():\n    return 1\n", encoding="utf-8")
    planner_packet = {
        "schema_name": "aix.planner.slice_packet.v2",
        "target_file": "backend/service.py",
        "allowed_surface": {
            "anchor": {"symbol": "backend.service.run", "compact_snippet": "def run():\n    return 1\n"}
        },
        "validation_command": "python -m py_compile backend/service.py",
    }
    step = {
        "tool": "edit_file",
        "args": {
            "relative_path": "services/hyeve-codeintel-service/backend/service.py",
            "grounding_source": "anchor",
            "search_range": {"start_line": 1, "end_line": 2},
            "replacement_text": "def run():\n    return 2\n",
        },
    }

    validate_quality(
        "PLANNER_OUTPUT",
        {"steps": [step]},
        {
            "task": {"task_intent": "code_change_required"},
            "planner_packet": planner_packet,
            "workspace_root": str(workspace_root),
        },
    )

    captured: dict[str, Any] = {}

    def fake_replace_range(**kwargs):
        captured.update(kwargs)
        return {"operation": "replace_range", "changed_files": ["backend/service.py"]}

    monkeypatch.setattr("backend.services.devexecutor_tool_loop_service.replace_range", fake_replace_range)

    result = execute_tool_step(
        workspace_root=workspace_root,
        step=step,
        allow_prefixes=[["python"]],
        attempt_tracker={},
        execution_context={"planner_context": {"packet": planner_packet}},
    )

    assert result["ok"] is True
    assert captured["relative_path"] == "backend/service.py"
    assert captured["start_line"] == 1
    assert captured["end_line"] == 2
    assert result["result"]["contract_validation"]["target_resolution"]["exists"] is True
    assert result["result"]["contract_validation"]["mode"] == "live_file_search_range"


def test_v2_runtime_executes_form_b_read_then_edit(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    workspace_root, service_root = _service_workspace(tmp_path)
    target = service_root / "backend" / "service.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("def run():\n    return 1\n", encoding="utf-8")
    planner_packet = {
        "schema_name": "aix.planner.slice_packet.v2",
        "target_file": "services/hyeve-codeintel-service/backend/service.py",
        "allowed_surface": {
            "anchor": {"symbol": "backend.service.run", "compact_snippet": "def run():\n    return 1\n"}
        },
        "validation_command": "python -m py_compile backend/service.py",
    }
    execution_context = {"planner_context": {"packet": planner_packet}}
    read_step = {
        "tool": "read_range",
        "step_idx": 0,
        "args": {"relative_path": "backend/service.py", "start_line": 1, "end_line": 2},
    }
    edit_step = {
        "tool": "edit_file",
        "args": {
            "relative_path": "backend/service.py",
            "grounding_source": "read_range",
            "read_step_idx": 0,
            "edit_range_within_read": {"start_line": 1, "end_line": 2},
            "replace_text": "def run():\n    return 2\n",
        },
    }

    read_result = execute_tool_step(
        workspace_root=workspace_root,
        step=read_step,
        allow_prefixes=[["python"]],
        attempt_tracker={},
        execution_context=execution_context,
    )
    assert read_result["ok"] is True

    captured: dict[str, Any] = {}

    def fake_replace_range(**kwargs):
        captured.update(kwargs)
        return {"operation": "replace_range", "changed_files": ["backend/service.py"]}

    monkeypatch.setattr("backend.services.devexecutor_tool_loop_service.replace_range", fake_replace_range)

    result = execute_tool_step(
        workspace_root=workspace_root,
        step=edit_step,
        allow_prefixes=[["python"]],
        attempt_tracker={},
        execution_context=execution_context,
    )

    assert result["ok"] is True
    assert captured["relative_path"] == "backend/service.py"
    assert captured["start_line"] == 1
    assert captured["end_line"] == 2
    assert result["result"]["contract_validation"]["grounding_source"] == "explicit_read_output"


def test_v2_live_grounding_rejects_form_b_with_search_text(tmp_path: Path) -> None:
    workspace_root, service_root = _service_workspace(tmp_path)
    target = service_root / "backend" / "service.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("def run():\n    return 1\n", encoding="utf-8")

    with pytest.raises(V2LiveFileGroundingError) as exc_info:
        validate_v2_edit_against_live_file(
            workspace_root=workspace_root,
            args={
                "relative_path": "services/hyeve-codeintel-service/backend/service.py",
                "grounding_source": "read_range",
                "read_step_idx": 1,
                "search": "def run():\n    return 1\n",
                "edit_range_within_read": {"start_line": 1, "end_line": 2},
                "replace_text": "def run():\n    return 2\n",
            },
            read_buffers={
                "backend/service.py": {
                    "text": "def run():\n    return 1\n",
                    "step_idx": 1,
                    "start_line": 1,
                    "end_line": 2,
                }
            },
        )

    assert exc_info.value.code == "EDIT_FILE_READ_RANGE_SEARCH_FORBIDDEN"


def test_v2_live_grounding_rejects_invalid_edit_range_within_read(tmp_path: Path) -> None:
    workspace_root, service_root = _service_workspace(tmp_path)
    target = service_root / "backend" / "service.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("def run():\n    return 1\n", encoding="utf-8")

    with pytest.raises(V2LiveFileGroundingError) as exc_info:
        validate_v2_edit_against_live_file(
            workspace_root=workspace_root,
            args={
                "relative_path": "services/hyeve-codeintel-service/backend/service.py",
                "grounding_source": "read_range",
                "read_step_idx": 1,
                "edit_range_within_read": {"start_line": 3, "end_line": 4},
                "replace_text": "return 2\n",
            },
            read_buffers={
                "backend/service.py": {
                    "text": "def run():\n    return 1\n",
                    "step_idx": 1,
                    "start_line": 1,
                    "end_line": 2,
                }
            },
        )

    assert exc_info.value.code in {"EDIT_FILE_RANGE_INVALID", "EDIT_FILE_RANGE_EMPTY"}


def test_v2_bridge_normalization_survives_to_runtime_without_read_linkage(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    workspace_root, service_root = _service_workspace(tmp_path)
    target = service_root / "backend" / "service.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("def run():\n    return 1\n", encoding="utf-8")
    planner_packet = {
        "schema_name": "aix.planner.slice_packet.v2",
        "task_intent": "code_change_required",
        "target_file": "backend/service.py",
        "allowed_surface": {
            "anchor": {"symbol": "backend.service.run", "compact_snippet": "def run():\n    return 1\n"}
        },
        "validation_command": "python -m py_compile backend/service.py",
    }
    normalized_steps = _normalize_planner_steps(
        {
            "steps": [
                {
                    "type": "edit_file",
                    "file": "services/hyeve-codeintel-service/backend/service.py",
                    "grounding_source": "anchor",
                    "search_range": {"start_line": 1, "end_line": 2},
                    "replacement_text": "def run():\n    return 2\n",
                }
            ]
        },
        max_steps=4,
        planner_packet=planner_packet,
        workspace_root=str(workspace_root),
    )

    assert all("read_source" not in dict(step.get("args") or {}) for step in normalized_steps)
    assert normalized_steps[0]["args"]["relative_path"] == "backend/service.py"

    captured: dict[str, Any] = {}

    def fake_edit_file(**kwargs):
        captured.update(kwargs)
        return {"operation": "edit_file", "changed_files": ["backend/service.py"]}

    monkeypatch.setattr("backend.services.devexecutor_tool_loop_service.edit_file", fake_edit_file)

    result = execute_tool_step(
        workspace_root=workspace_root,
        step=normalized_steps[0],
        allow_prefixes=[["python"]],
        attempt_tracker={},
        execution_context={"planner_context": {"packet": planner_packet}},
    )

    assert result["ok"] is True
    assert captured["relative_path"] == "backend/service.py"


def test_v2_quality_rejects_legacy_read_source_contamination(tmp_path: Path) -> None:
    workspace_root, service_root = _service_workspace(tmp_path)
    target = service_root / "backend" / "service.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("def run():\n    return 1\n", encoding="utf-8")

    with pytest.raises(QualityEnforcementError) as exc_info:
        validate_quality(
            "PLANNER_OUTPUT",
            {
                "steps": [
                    {
                        "tool": "edit_file",
                        "args": {
                            "relative_path": "backend/service.py",
                            "grounding_source": "anchor",
                            "search_range": {"start_line": 1, "end_line": 2},
                            "replacement_text": "def run():\n    return 2\n",
                            "read_source": {"step_idx": 1},
                        },
                    }
                ]
            },
            {
                "task": {"task_intent": "code_change_required"},
                "planner_packet": {"schema_name": "aix.planner.slice_packet.v2"},
                "workspace_root": str(workspace_root),
            },
        )

    assert exc_info.value.reason_code == "PLANNER_EDIT_CROSS_STEP_LINKAGE_FORBIDDEN"


def test_v2_live_grounding_rejects_missing_grounding_source(tmp_path: Path) -> None:
    workspace_root, service_root = _service_workspace(tmp_path)
    target = service_root / "backend" / "service.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("def run():\n    return 1\n", encoding="utf-8")

    with pytest.raises(V2LiveFileGroundingError) as exc_info:
        validate_v2_edit_against_live_file(
            workspace_root=workspace_root,
            args={
                "relative_path": "services/hyeve-codeintel-service/backend/service.py",
                "search_range": {"start_line": 1, "end_line": 2},
                "replacement_text": "def run():\n    return 2\n",
            },
            planner_packet=_planner_packet_with_anchor("def run():\n    return 1\n"),
        )

    assert exc_info.value.code == "EDIT_FILE_MISSING_GROUNDING_SOURCE"


def test_v2_live_grounding_rejects_missing_read_provenance_for_read_grounding(tmp_path: Path) -> None:
    workspace_root, service_root = _service_workspace(tmp_path)
    target = service_root / "backend" / "service.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("def run():\n    return 1\n", encoding="utf-8")

    with pytest.raises(V2LiveFileGroundingError) as exc_info:
        validate_v2_edit_against_live_file(
            workspace_root=workspace_root,
            args={
                "relative_path": "services/hyeve-codeintel-service/backend/service.py",
                "grounding_source": "read_range",
                "edit_range_within_read": {"start_line": 1, "end_line": 2},
                "replace_text": "def run():\n    return 2\n",
            },
            read_buffers={
                "backend/service.py": {
                    "text": "def run():\n    return 1\n",
                    "step_idx": 1,
                    "start_line": 1,
                    "end_line": 2,
                }
            },
        )

    assert exc_info.value.code == "EDIT_FILE_MISSING_READ_PROVENANCE"


def test_v2_live_grounding_rejects_cross_file_read_provenance(tmp_path: Path) -> None:
    workspace_root, service_root = _service_workspace(tmp_path)
    target = service_root / "backend" / "service.py"
    other = service_root / "backend" / "other.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("def run():\n    return 1\n", encoding="utf-8")
    other.write_text("def other():\n    return 1\n", encoding="utf-8")

    with pytest.raises(V2LiveFileGroundingError) as exc_info:
        validate_v2_edit_against_live_file(
            workspace_root=workspace_root,
            args={
                "relative_path": "services/hyeve-codeintel-service/backend/service.py",
                "grounding_source": "read_range",
                "read_step_idx": 3,
                "edit_range_within_read": {"start_line": 1, "end_line": 2},
                "replace_text": "def run():\n    return 2\n",
            },
            read_buffers={
                "backend/other.py": {
                    "text": "def run():\n    return 1\n",
                    "step_idx": 3,
                    "start_line": 1,
                    "end_line": 2,
                }
            },
        )

    assert exc_info.value.code == "EDIT_FILE_READ_PROVENANCE_UNAVAILABLE"
