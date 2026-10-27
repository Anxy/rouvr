from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, "C:/dev/hyeve")

from backend.services.aix_run_ledger_service import persist_run_ledger_record
from backend.services.devexecutor_planner_bridge_service import plan_steps_via_llm
from backend.services.devexecutor_runlog_resolver_service import (
    get_planner_trace_root,
    get_run_ledger_root,
    get_runlog_root,
)


def _valid_planner_packet() -> dict[str, object]:
    return {
        "schema_name": "aix.planner.slice_packet.current",
        "schema_version": "v1",
        "task_number": 2448,
        "slice_id": "slice-2448-001",
        "objective": "Mutate bounded function behavior",
        "mutation_kind": "file_local_refactor",
        "target_file": "backend/services/example.py",
        "target_symbol": "_build_envelope_rejection",
        "behavior_change": "Return revised envelope rejection payload.",
        "implementation_hints": ["Keep changes local to the target function."],
        "acceptance_criteria": ["Mutation is grounded and validation stays scoped."],
        "validation_command": "pytest tests/test_example.py -q",
        "constraints": ["Do not widen scope."],
        "codeintel_anchor": {
            "file_path": "backend/services/example.py",
            "symbol": "_build_envelope_rejection",
            "signature": "def _build_envelope_rejection() -> dict[str, object]:",
            "start_line": 10,
            "end_line": 24,
            "snapshot_id": "snap-2448",
            "commit_sha": "abc123",
            "symbol_source_hash": "hash-2448",
        },
        "hard_invariants": [{"kind": "proof_surface", "summary": "No unrelated edits."}],
        "bounded_edit_surface": ["backend/services/example.py"],
        "stop_conditions": [{"code": "VALIDATION_FAILS", "summary": "Stop when validation fails."}],
        "tool_policy": {
            "execution_lane": "mutation",
            "allowed_mutation_tools": ["edit_file", "write_file", "apply_patch"],
        },
        "provenance": {
            "admission_status": "ready_for_planning",
            "ticket_contract_ref": "workhub://tasks/2448/ticket_contract_current",
        },
        "lineage": {
            "slice_id": "slice-2448-001",
            "parent_task_number": 2448,
            "root_task_number": 2448,
            "dependency_order": 1,
            "depends_on_slice_ids": [],
            "total_declared_slices": 1,
            "completion_policy": "single_slice_completes_parent",
            "rollup_policy": "parent_done_requires_active_slice_complete",
        },
        "capability_contract": {
            "producer_tier": "planner",
            "executor_tier": "coder",
            "allowed_action_class": "bounded_code_change",
            "allowed_actions": ["inspect_context", "edit_file", "run_validation"],
            "forbidden_actions": ["cross_repo_edit"],
            "assistance_route": "plannerai_or_onlineai",
        },
    }


def _sample_payload() -> dict:
    return {
        "ok": False,
        "project_id": "bh-migrated-7-aix",
        "task_number": 2448,
        "task_id": "task-2448",
        "task_intent": "code_change_required",
        "final_status": "needs_review",
        "result": {
            "run_id": "run-2448",
            "correlation_id": "corr-2448",
            "project_id": "bh-migrated-7-aix",
            "task_id": "task-2448",
            "task_number": 2448,
            "status": "failed",
            "evidence": {},
            "error": {"code": "quality_gate_rejected", "message": "quality gate rejected the run"},
            "evidence_refs": ["local://evidence/ref-1"],
        },
        "control_plane_outcome": {
            "phase": "final",
            "status": "needs_review",
            "substate": "quality_gate_rejected",
            "reason_code": "TARGET_LOGIC_NOT_CONFIRMED",
            "reason_summary": "planner exhausted bounded discovery without confirming a safe mutating path",
            "handoff_status": "needs_replan",
            "failure_category": "bad_decomposition",
            "blocked_reason": None,
        },
        "planner": {
            "planner_used": True,
            "attempts": 1,
            "provider": "openrouter",
            "local_trace_ref": "local://planner-traces/trace-2448.json",
            "error": "DIAGNOSTIC_BLOCKED",
        },
        "quality_gate": {"passed": False, "code": "diagnostic_blocked", "message": "diagnostic blocked"},
        "repair_loop": {"failure_code": "TARGET_LOGIC_NOT_CONFIRMED", "failure_class": "unknown"},
        "diagnostic_outcome": {
            "status": "diagnostic_blocked",
            "reason_code": "TARGET_LOGIC_NOT_CONFIRMED",
            "reason_summary": "planner exhausted bounded discovery without confirming a safe mutating path",
        },
        "task_packet": {
            "task_id": "task-2448",
            "task_number": 2448,
            "title": "planner durability",
            "priority": "P1",
            "metadata": {
                "repo_key": "hyeve-aix-service",
                "slice_focus": "runlog_authority",
                "change_intent": "code_change_required",
            },
        },
        "validation_contract": {
            "expected_commands": ["pytest tests/test_example.py -q"],
            "target_files": ["backend/services/example.py"],
            "code_change_required": True,
        },
        "temporal": {"enabled": True, "state_machine": {"state": "needs_review", "events": []}},
    }


def test_runlog_root_resolution_uses_shared_authority(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "backend.services.devexecutor_runlog_resolver_service.settings.AIX_RUNLOG_ROOT",
        str(tmp_path / "_runlogs"),
    )

    root = get_runlog_root()

    assert root == tmp_path / "_runlogs"
    assert get_planner_trace_root() == root / "planner-traces"
    assert get_run_ledger_root() == root / "aix-run-ledger"


def test_runlog_root_falls_back_to_temp_when_default_root_is_unwritable(monkeypatch, tmp_path: Path) -> None:
    from backend.services import devexecutor_runlog_resolver_service as module

    default_root = (tmp_path / "readonly" / "_runlogs").resolve(strict=False)
    fallback_root = tmp_path / "temp-runlogs"
    original_materialize = module._materialize_root

    monkeypatch.setattr(module.settings, "AIX_RUNLOG_ROOT", None)
    monkeypatch.setattr(module, "DEFAULT_LOCAL_RUNLOG_ROOT", default_root)
    monkeypatch.setattr(module, "_fallback_runlog_root", lambda: fallback_root)

    def _fake_materialize(root: Path) -> Path:
        if root == default_root:
            raise OSError(30, "Read-only file system")
        return original_materialize(root)

    monkeypatch.setattr(module, "_materialize_root", _fake_materialize)

    root = module.get_runlog_root()

    assert root == fallback_root
    assert root.exists()


def test_planner_trace_materializes_file_under_shared_root(monkeypatch, tmp_path: Path) -> None:
    from backend.services import devexecutor_planner_bridge_service as module

    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"data": {"output_text": '{"steps":[{"tool":"get_status","args":{}}]}'}}

    class _Client:
        def __init__(self, *, timeout: int) -> None:
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def post(self, _target_url: str, *, headers: dict[str, str], json: dict[str, object]):
            return _Response()

    monkeypatch.setattr(
        "backend.services.devexecutor_runlog_resolver_service.settings.AIX_RUNLOG_ROOT",
        str(tmp_path / "_runlogs"),
    )
    monkeypatch.setattr(module.httpx, "Client", _Client)
    monkeypatch.setattr(module.settings, "AYRPACK_SHADOW_WRITE_ENABLED", False)

    _, diagnostics = plan_steps_via_llm(
        tenant_id="default",
        project_id="bh-migrated-7-aix",
        correlation_id="corr-runlog-root",
        workspace_root="/workspace/hyeve",
        prompt="Produce the next bounded step.",
        planner_packet=_valid_planner_packet(),
        provider="openrouter",
        model="openrouter/deepseek/deepseek-r1",
        authorization="Bearer token",
    )

    local_trace_path = Path(str(diagnostics["local_trace_ref"]))
    assert local_trace_path.exists()
    assert local_trace_path.parent == get_planner_trace_root()


def test_run_ledger_materializes_file_under_shared_root(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "backend.services.devexecutor_runlog_resolver_service.settings.AIX_RUNLOG_ROOT",
        str(tmp_path / "_runlogs"),
    )

    outcome = persist_run_ledger_record(_sample_payload())
    ledger_path = Path(str(outcome["ref"]))

    assert ledger_path.exists()
    assert ledger_path.parent == get_run_ledger_root()
