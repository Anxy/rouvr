from __future__ import annotations

from typing import Any


def _normalize_targets(targets: list[str] | None, *, fallback: list[str] | None = None) -> list[str]:
    normalized = [str(item).strip().replace("\\", "/") for item in list(targets or []) if str(item).strip()]
    if normalized:
        return normalized
    return [str(item).strip().replace("\\", "/") for item in list(fallback or []) if str(item).strip()]


def recovery_registry_entry(code: str) -> dict[str, Any]:
    normalized = str(code or "").strip().upper()
    entries: dict[str, dict[str, Any]] = {
        "TARGET_LOGIC_NOT_CONFIRMED": {
            "next_best_actions": [
                "inject stronger semantic anchors for the target files",
                "retry with narrower owning-symbol guidance",
                "escalate for human clarification if target logic is absent from allowed files",
            ],
            "retry_rules": [
                "confirm the target logic in an allowed file before any mutating step",
                "inject owning-symbol or route-level anchors before replanning",
                "retry with a single-file mutating plan only after the target logic is confirmed",
            ],
        },
        "GROUNDING_MISMATCH": {
            "next_best_actions": [
                "re-read the exact target symbol and confirm anchor text",
                "refresh semantic anchors before any symbol-scoped mutation",
                "retry with current file state instead of stale planner assumptions",
            ],
            "retry_rules": [
                "re-read the exact target symbol immediately before mutation",
                "confirm anchor text against current file content",
                "do not mutate against stale symbol boundaries or stale planner context",
            ],
        },
        "INVALID_NEW_CODE_SYNTAX": {
            "next_best_actions": [
                "repair syntax and indentation boundaries in the changed files",
                "re-run the targeted validation commands after syntax repair",
            ],
            "retry_rules": [
                "preserve valid function boundaries and indentation",
                "do not insert new definitions inside an existing function body or parameter list",
                "retry with file-aware edits on exact confirmed ranges when syntax drift occurs",
            ],
        },
        "PYTHON_SYNTAX_INVALID": {
            "next_best_actions": [
                "repair syntax and indentation boundaries in the changed files",
                "re-run the targeted validation commands after syntax repair",
            ],
            "retry_rules": [
                "preserve valid function boundaries and indentation",
                "do not insert new definitions inside an existing function body or parameter list",
                "retry with file-aware edits on exact confirmed ranges when syntax drift occurs",
            ],
        },
        "WIRING_INCOMPLETE_ROUTER_THREADING": {
            "retry_rules": [
                "expose the missing router parameter and thread it through the service callsite",
                "rerun the targeted router and service validation commands after wiring is complete",
            ],
        },
        "FEATURE_SLICE_INCOMPLETE": {
            "retry_rules": [
                "extend the declared test slice so the requested behavior is asserted explicitly",
                "run the required targeted validation command and attach its output as evidence",
            ],
        },
        "CANDIDATE_VALIDATION_MISSING": {
            "retry_rules": [
                "run the required targeted validation command for the changed slice",
                "attach validation output as evidence before review",
            ],
        },
        "NOOP_CODE_CHANGE": {
            "retry_rules": [
                "produce at least one substantive code change in an allowed target file",
                "run and capture the targeted validation command for the changed slice",
            ],
        },
        "REPLACE_RANGE_OUT_OF_RANGE": {
            "retry_rules": [
                "do not emit replace_range with guessed or stale line coordinates",
                "read the file first and use file-aware edits when line windows are unstable",
            ],
        },
        "APPLY_PATCH_INVALID": {
            "retry_rules": [
                "do not emit malformed or placeholder patch text",
                "do not use apply_patch in this retry plan",
                "use read_file first, then write_file or replace_range with exact file content and coordinates",
            ],
        },
        "MUTATION_INVALID_FUNCTION_SHAPE": {
            "retry_rules": [
                "preserve async or def exactly as in the confirmed current symbol",
                "preserve receiver arguments such as self for class methods",
                "append only the requested defaulted parameter without dropping existing ones",
            ],
        },
        "PLANNER_MUTATION_OUTSIDE_TARGET_FILES": {
            "retry_rules": [
                "perform discovery on the explicit target files first",
                "mutate only the explicit target files",
                "do not return discovery-only plans",
            ],
        },
        "PLANNER_NO_MUTATING_STEP_FOR_CODE_CHANGE": {
            "retry_rules": [
                "perform discovery on the explicit target files first",
                "return a mutating plan for this code-change task",
                "do not return discovery-only plans",
            ],
        },
        "PLANNER_EDIT_WITHOUT_IMMEDIATE_READ": {
            "retry_rules": [
                "read the exact target file or range immediately before each replace_range or apply_patch step",
                "do not mutate against guessed coordinates or stale file state",
            ],
        },
        "PLANNER_APPLY_PATCH_REQUIRES_SINGLE_FILE_CONTEXT": {
            "retry_rules": [
                "do not emit multi-file apply_patch in this task",
                "read the exact target file immediately before apply_patch",
                "prefer replace_range with exact confirmed line windows",
            ],
        },
        "PLANNER_FIRST_PASS_TEST_MUTATION_FORBIDDEN": {
            "retry_rules": [
                "first pass may mutate implementation files only",
                "do not mutate tests in the same plan as service or router edits",
                "prove the implementation change first with existing targeted tests",
            ],
        },
    }
    entry = entries.get(normalized, {})
    return {
        "code": normalized or "UNKNOWN",
        "next_best_actions": list(entry.get("next_best_actions") or []),
        "retry_rules": list(entry.get("retry_rules") or []),
    }


def build_recovery_plan(
    *,
    code: str,
    targets: list[str] | None = None,
    fallback_targets: list[str] | None = None,
    summary: str | None = None,
) -> list[dict[str, Any]]:
    entry = recovery_registry_entry(code)
    normalized = str(entry.get("code") or code or "").strip().upper()
    target_list = _normalize_targets(targets, fallback=fallback_targets)
    if normalized == "TARGET_LOGIC_NOT_CONFIRMED":
        return [
            {
                "step": 1,
                "action": "reread_symbol_context",
                "summary": "Re-read the zoomed symbol context around the blocked target before mutating anything.",
                "targets": target_list,
            },
            {
                "step": 2,
                "action": "inject_semantic_anchors",
                "summary": "Add owning-symbol or route-level anchors for the allowed mutation targets before replanning.",
                "targets": target_list,
            },
            {
                "step": 3,
                "action": "retry_narrow_plan",
                "summary": "Retry with a single-file mutating plan only after the target logic is confirmed in an allowed file.",
                "targets": target_list,
            },
        ]
    if normalized == "GROUNDING_MISMATCH":
        return [
            {
                "step": 1,
                "action": "reread_symbol_context",
                "summary": "Re-read the resolved symbol and nearby lines before attempting another symbol-scoped mutation.",
                "targets": target_list,
            },
            {
                "step": 2,
                "action": "refresh_symbol_anchor",
                "summary": "Refresh the anchor text against current file content before replanning.",
                "targets": target_list,
            },
            {
                "step": 3,
                "action": "retry_narrow_plan",
                "summary": "Retry with the current symbol boundaries and a bounded retry budget.",
                "targets": target_list,
            },
        ]
    if normalized == "WIRING_INCOMPLETE_ROUTER_THREADING":
        return [
            {
                "step": 1,
                "action": "thread_missing_wiring",
                "summary": "Expose the missing router parameter and thread it through the service callsite.",
                "targets": target_list,
            },
            {
                "step": 2,
                "action": "rerun_targeted_validation",
                "summary": "Run the targeted validation commands after wiring is complete.",
                "targets": target_list,
            },
        ]
    if normalized in {"FEATURE_SLICE_INCOMPLETE", "CANDIDATE_VALIDATION_MISSING"}:
        return [
            {
                "step": 1,
                "action": "complete_declared_validation_slice",
                "summary": "Extend the declared validation slice so the requested behavior is asserted explicitly.",
                "targets": target_list,
            },
            {
                "step": 2,
                "action": "execute_required_validation",
                "summary": "Run the required targeted validation command and attach its output as evidence.",
                "targets": target_list,
            },
        ]
    if normalized in {"CANDIDATE_SYNTAX_INVALID", "PYTHON_SYNTAX_INVALID", "INVALID_NEW_CODE_SYNTAX"}:
        return [
            {
                "step": 1,
                "action": "repair_python_syntax",
                "summary": "Fix syntax and indentation boundaries in the changed Python files before any further review.",
                "targets": target_list,
            },
            {
                "step": 2,
                "action": "rerun_python_validation",
                "summary": "Re-run the targeted validation commands after syntax repair.",
                "targets": target_list,
            },
        ]
    if normalized == "NOOP_CODE_CHANGE":
        return [
            {
                "step": 1,
                "action": "perform_required_mutation",
                "summary": "Produce at least one substantive code change in an allowed target file for this mutation-required task.",
                "targets": target_list,
            },
            {
                "step": 2,
                "action": "attach_validation_evidence",
                "summary": "Run and capture the targeted validation command for the changed slice.",
                "targets": target_list,
            },
        ]
    if normalized == "VERIFIED":
        return [
            {
                "step": 1,
                "action": "archive_evidence",
                "summary": "Preserve patch and validation refs for human verification.",
                "targets": target_list,
            }
        ]
    return [
        {
            "step": 1,
            "action": "repair_blocking_issue",
            "summary": str(summary or "Resolve the blocking issue in the declared target files."),
            "targets": target_list,
        },
        {
            "step": 2,
            "action": "rerun_targeted_validation",
            "summary": "Re-run the targeted validation commands and attach the new evidence.",
            "targets": target_list,
        },
    ]
