from __future__ import annotations

from backend.services.devexecutor_planner_request_envelope_service import build_planner_request_envelope
from backend.services.devexecutor_tool_contract_registry_service import build_planner_contract_text


def test_build_planner_request_envelope_carries_multi_file_surface() -> None:
    planner_packet = {
        "schema_name": "aix.planner.slice_packet.current",
        "schema_version": "v1",
        "task_number": 2691,
        "slice_id": "task-2691-primary",
        "objective": "Bounded source plus test mutation",
        "mutation_kind": "signature_preserving_edit",
        "interface_change_policy": "preserve",
        "target_file": "projects/AIX/hyeve-aix-service/backend/aix_orchestrator_v2/run_summary.py",
        "target_files": [
            "projects/AIX/hyeve-aix-service/backend/aix_orchestrator_v2/run_summary.py",
            "projects/AIX/hyeve-aix-service/backend/tests/test_aix_orchestrator_v2_run_summary.py",
        ],
        "target_symbol": "build_run_summary",
        "behavior_change": "Mutate source and focused test in a causally linked way.",
        "acceptance_criteria": ["validation passes"],
        "validation_command": "pytest projects/AIX/hyeve-aix-service/backend/tests/test_aix_orchestrator_v2_run_summary.py -q",
        "constraints": ["touch only the bounded source and test files"],
        "enforce_read_before_write": True,
        "strict_anchor_validation": True,
        "codeintel_anchor": {
            "file_path": "projects/AIX/hyeve-aix-service/backend/aix_orchestrator_v2/run_summary.py",
            "symbol": "build_run_summary",
            "signature": "def build_run_summary(state: dict[str, Any]) -> dict[str, Any]:",
            "start_line": 69,
            "end_line": 110,
            "output_content": "def build_run_summary(state):\n    return {}\n",
            "snapshot_id": "snap-2691",
            "commit_sha": "sha-2691",
            "symbol_source_hash": "hash-2691",
        },
        "grounded_symbol_source": "def build_run_summary(state):\n    return {}\n",
        "grounding_hints": {
            "matched_entities": [],
            "callers": [],
            "endpoint_entities": [],
            "likely_tests": ["projects/AIX/hyeve-aix-service/backend/tests/test_aix_orchestrator_v2_run_summary.py"],
            "adjacent_files": ["projects/AIX/hyeve-aix-service/backend/tests/test_aix_orchestrator_v2_run_summary.py"],
        },
        "bounded_edit_surface": [
            "projects/AIX/hyeve-aix-service/backend/aix_orchestrator_v2/run_summary.py",
            "projects/AIX/hyeve-aix-service/backend/tests/test_aix_orchestrator_v2_run_summary.py",
        ],
        "tool_policy": {
            "execution_lane": "devexecutor",
            "edit_profile": "bounded_symbol_scoped",
            "verification_profile": "declared_validation_only",
            "allowed_mutation_tools": ["read_range", "edit_file", "cmd.run"],
            "disallowed_mutation_tools": ["write_file"],
        },
        "stop_conditions": [{"code": "OUT_OF_BOUNDS_EDIT", "summary": "stop if edit escapes bounded surface"}],
        "provenance": {
            "admission_status": "admitted",
            "admission_decision_version": "aix-orchestrator-v2",
            "ticket_contract_ref": "workhub://tasks/2691/execution-contracts/2",
            "ticket_contract_version": "workhub.task_execution_contract.current",
            "repo_key": "hyeve-aix-service",
        },
        "lineage": {
            "parent_task_number": 2691,
            "root_task_number": 2691,
            "dependency_order": 1,
            "depends_on_slice_ids": [],
            "total_declared_slices": 1,
            "completion_policy": "single_slice_vertical",
            "rollup_policy": "task_local",
        },
        "capability_contract": {
            "producer_tier": "orchestrator_v2",
            "executor_tier": "devexecutor_runtime",
            "allowed_action_class": "bounded_code_change",
            "allowed_actions": ["read", "edit", "run_validation"],
            "forbidden_actions": ["override_packet"],
            "assistance_route": "human_or_followup_task",
        },
    }

    envelope = build_planner_request_envelope(
        prompt="Compact packet prompt",
        planner_packet=planner_packet,
        task_intent="code_change_required",
        max_steps=8,
        planner_attempt_number=1,
    )

    assert envelope["planner_packet"]["target_files"] == planner_packet["target_files"]
    assert envelope["planner_packet"]["bounded_edit_surface"] == planner_packet["bounded_edit_surface"]


def test_build_planner_contract_text_allows_bounded_multi_file_surface() -> None:
    text = build_planner_contract_text(
        {
            "target_file": "backend/a.py",
            "target_files": ["backend/a.py", "backend/tests/test_a.py"],
            "bounded_edit_surface": ["backend/a.py", "backend/tests/test_a.py"],
        }
    )

    assert "only touch files explicitly declared in planner_packet.bounded_edit_surface" in text
    assert "only touch the single target_file in planner_packet" not in text
