from __future__ import annotations

from typing import Any

FIRST_CANONICAL_SUCCESS_CASE: dict[str, Any] = {
    "run_id": "aixv2-b1267e8431214cba",
    "task_number": 2677,
    "workflow_id": "aix-v2-bh-migrated-7-aix-2677",
    "status": "writeback_complete",
    "execution_status": "completed",
    "verification_classification": "success",
    "workhub_status": "done",
    "changed_files": [
        "backend/tests/test_monorepo_imports.py",
    ],
    "commands": [
        "python3 -m py_compile backend/tests/test_monorepo_imports.py",
        "pytest backend/tests/test_monorepo_imports.py -q",
    ],
    "artifact_names": [
        "recon",
        "planner_input_summary",
        "planner_raw",
        "normalized_plan",
        "planner_trace",
        "execution_summary",
        "verifier_result",
        "writeback",
    ],
}


def build_proof_task_contract(
    *,
    target_file: str,
    validation_command: str,
    mutation_instructions: str,
    behavior_change: str,
    title: str,
    description: str,
) -> dict[str, Any]:
    constraints = [
        "Edit only the declared target file.",
        "Do not change unrelated runtime behavior.",
        "Make an executable mutation, not a comment/docstring/format-only edit.",
    ]
    acceptance_criteria = [
        "The executable mutation in the declared target file is applied.",
        "The declared validation command completes successfully.",
    ]
    return {
        "scope_in": [f"single-file bounded executable edit in {target_file}"],
        "objective": title,
        "constraints": constraints,
        "handoff_status": "ready_for_planning",
        "task_metadata_json": {
            "slice_id": "aix-v2-proof-task",
            "behavior_change": behavior_change,
            "mutation_instructions": mutation_instructions,
        },
        "ticket_contract_current": {
            "scope": {
                "in_scope": [f"single-file bounded executable edit in {target_file}"],
                "out_of_scope": ["runtime code changes", "multi-file edits", "schema changes"],
            },
            "non_goals": ["No legacy runner changes.", "No multi-file refactors."],
            "objective": title,
            "constraints": constraints,
            "handoff_status": "ready_for_planning",
            "target_surface": {
                "target_files": [target_file],
                "owning_symbols": [],
                "prompt_symbol_targets": [],
                "bounded_search_surface": [target_file],
            },
            "execution_slice": {
                "summary": description,
                "target_files": [target_file],
                "validation_commands": [validation_command],
                "interface_change_policy": "preserve",
            },
            "mutation_kind": "signature_preserving_edit",
            "behavior_change": behavior_change,
            "mutation_instructions": mutation_instructions,
            "implementation_hints": [
                "Touch only the declared target file.",
                "Keep the declared validation command unchanged.",
                "Do not use comment-only or formatting-only edits.",
            ],
            "stop_conditions": [
                "{'code': 'OUT_OF_BOUNDS_EDIT', 'summary': 'Stop if the edit escapes the declared file.'}",
                "{'code': 'VALIDATION_FAILS', 'summary': 'Stop if the declared validation command fails.'}",
            ],
            "validation_plan": [validation_command],
            "failure_category": None,
            "acceptance_criteria": acceptance_criteria,
        },
        "acceptance_criteria": acceptance_criteria,
        "validation_commands": [validation_command],
        "bounded_search_surface": [target_file],
        "task_packet_quality_current": {
            "weak": False,
            "warnings": [],
            "warning_codes": [],
            "dispatch_ready": True,
            "canonical_ticket_valid": True,
            "dispatch_missing_fields": [],
            "canonical_missing_fields": [],
            "canonical_execution_slice_required": False,
        },
    }


NEXT_PROOF_TASK_CANDIDATES: list[dict[str, Any]] = [
    {
        "proof_kind": "single-file executable mutation",
        "title": "AIX v2 proof task: executable assertion mutation in test_monorepo_imports.py",
        "target_file": "backend/tests/test_monorepo_imports.py",
        "validation_command": "pytest backend/tests/test_monorepo_imports.py -q",
        "mutation_shape": "Change one executable assertion in-place.",
    },
    {
        "proof_kind": "single-file forced failure",
        "title": "AIX v2 proof task: forced failure with mismatched validation selector",
        "target_file": "backend/tests/test_monorepo_imports.py",
        "validation_command": "pytest backend/tests/test_aix_main_route_surface.py::test_missing_case -q",
        "mutation_shape": "Keep bounded target file but force a declared validation mismatch.",
    },
    {
        "proof_kind": "small bounded multi-step mutation",
        "title": "AIX v2 proof task: two-step bounded test mutation with local validation",
        "target_file": "backend/tests/test_monorepo_imports.py",
        "validation_command": "pytest backend/tests/test_monorepo_imports.py -q",
        "mutation_shape": "Read anchored block, change one executable assertion, then add one adjacent behavior-bearing setup tweak in the same file.",
    },
]
