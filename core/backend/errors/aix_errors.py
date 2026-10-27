"""Structured machine-readable error codes for the AIX API (AIFIRST-1 #1810).

Every 4xx/5xx response raised via ``aix_http_error`` contains:

  {
    "code":        top-level category   e.g. "PLAN_VALIDATION_FAILED",
    "sub_code":    specific failure     e.g. "INVALID_TRANSITION",
    "detail":      human-readable text  (backwards-compatible with prior consumers),
    "remediation": actionable steps for AI retry loops,
    "context":     optional structured key/value pairs about the failure,
  }

The ``AIX_ERROR_CODES`` registry is injected into the OpenAPI schema as the
top-level ``x-error-codes`` extension so tooling can enumerate all known codes
without having to call the API.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

# ---------------------------------------------------------------------------
# Remediation text indexed by sub_code — actionable for AI agent retry loops
# Keys are exact sub_codes OR prefixes (for codes that embed context like
# "INVALID_TRANSITION expected=confirm_anchor got=replace_in_function").
# ---------------------------------------------------------------------------
_REMEDIATION: dict[str, str] = {
    # PLAN_* sub-codes
    "EMPTY_PLAN": ("Provide at least one step in 'steps', a 'compact_carrier', or a 'planner' prompt."),
    "INVALID_COMPACT_CARRIER": ("Fix the compact_carrier JSON according to the carrier schema before retrying."),
    "PLAN_LLM_FAILED": ("Retry with a clearer planner prompt; the LLM could not produce a valid plan."),
    # INVALID_TRANSITION covers all variants: "INVALID_TRANSITION expected=X got=Y"
    "INVALID_TRANSITION": (
        "Add a confirm_anchor step immediately before the symbol-scoped mutating step "
        "(replace_in_function / update_call_kwarg). "
        "Required sequence: read_range -> confirm_anchor -> <mutation>."
    ),
    "INVALID_EDIT_SYMBOL_MODE": (
        "The legacy edit_symbol fallback call is using an invalid mode. Ensure 'mode' is one of the "
        "supported legacy values and that a confirm_anchor step immediately precedes this step. "
        "Prefer edit_file; edit_symbol is removed from default orchestration execution."
    ),
    "INVALID_SYMBOL_FORMAT": (
        "The symbol_name field has an invalid format. Use a fully qualified name "
        "(e.g. 'MyClass.my_method' or 'module_name.my_function'). "
        "Bare unqualified identifiers are not accepted."
    ),
    "INVALID_READ_RANGE_BOUNDS": (
        "The read_range step has invalid line bounds. Ensure start_line < end_line and "
        "both are positive integers within the file."
    ),
    "PLANNER_NO_MUTATING_STEP_FOR_CODE_CHANGE": (
        "The task declares a code change but no mutating step was found in the plan. "
        "Add at least one of: edit_file, replace_in_function, update_call_kwarg, replace_range, "
        "apply_patch, write_file."
    ),
    "PLANNER_INVALID_REPLACEMENT_CONTENT": (
        "The replace_function step contains placeholder, elided, or incomplete replacement content. "
        "Regenerate one complete concrete function definition from the grounded source."
    ),
    "PLANNER_MUTATION_OUTSIDE_TARGET_FILES": (
        "A mutating step targets a file not listed in metadata.target_files. "
        "Either add the file to target_files or remove the mutation step."
    ),
    "PLANNER_EDIT_WITHOUT_IMMEDIATE_READ": (
        "A symbol-mutation step must be immediately preceded by a read step on the same file. "
        "Add read_range or confirm_anchor before the mutation."
    ),
    "PLANNER_APPLY_PATCH_REQUIRES_SINGLE_FILE_CONTEXT": (
        "apply_patch must operate on a single file. Ensure exactly one file is in the "
        "step context before applying the patch."
    ),
    "PLANNER_FIRST_PASS_TEST_MUTATION_FORBIDDEN": (
        "Tests may not be mutated on the first planning pass. Implement the source change "
        "first; test mutations are permitted in subsequent passes."
    ),
    "BODY_INSERT_INVALID_PYTHON_SYNTAX": (
        "The legacy body_insert fallback payload contains invalid Python syntax. Fix the syntax before retrying, "
        "or switch to edit_file."
    ),
    "DUPLICATE_MUTATION_TARGET": (
        "Only one mutating step per target file is allowed per run. "
        "Split mutations into separate runs or merge into a single step."
    ),
    # SYMBOL_NOT_FOUND — from pre-execution resolver pass (AIFIRST-5)
    "SYMBOL_NOT_FOUND": (
        "The symbol_name could not be found in the target file via CodeIntel or AST. "
        "Verify the exact function/class name and relative_path, then retry."
    ),
    # WORKSPACE_* sub-codes
    "WORKSPACE_NOT_FOUND": (
        "The workspace_root path does not exist on the execution host. "
        "Verify the path and confirm the volume is mounted correctly."
    ),
    # AUTH_* sub-codes
    "AUTH_MISSING": "Include a valid Bearer token in the Authorization header.",
    # EXECUTOR_* sub-codes
    "EXECUTOR_RUN_FAILED": (
        "Check the run transcript (/runs/{run_id}/transcript) for the step that caused "
        "failure, then retry with a corrected plan."
    ),
    "EXECUTOR_RUN_NOT_FOUND": ("The run_id does not exist. Use GET /runs or /runs/{run_id} to locate active runs."),
    "TOKEN_BUDGET_EXCEEDED": (
        "Reduce read volume, split the run into smaller steps, or increase token_budget before retrying."
    ),
    "RESUME_RUN_NOT_FOUND": ("Provide a valid parent run_id before attempting to resume execution."),
    "RESUME_CHAIN_LIMIT_EXCEEDED": ("Start a fresh run instead of extending a resume chain beyond depth 3."),
    # GROUNDING_* sub-codes
    "GROUNDING_CONFIRMATION_REQUIRED": (
        "The executor requires a confirm_anchor step before the mutating tool call. "
        "Prepend confirm_anchor(relative_path, anchor_quote) immediately before the mutating step."
    ),
}

_DEFAULT_REMEDIATION = "Review the error details and correct the request before retrying."

# ---------------------------------------------------------------------------
# Registry — injected into the OpenAPI schema as x-error-codes in main.py
# ---------------------------------------------------------------------------
AIX_ERROR_CODES: dict[str, list[str]] = {
    "PLAN_VALIDATION_FAILED": [
        "EMPTY_PLAN",
        "INVALID_COMPACT_CARRIER",
        "PLAN_LLM_FAILED",
        "INVALID_TRANSITION",  # prefix; actual code may include "expected=X got=Y"
        "INVALID_EDIT_SYMBOL_MODE",
        "INVALID_SYMBOL_FORMAT",
        "INVALID_READ_RANGE_BOUNDS",
        "PLANNER_NO_MUTATING_STEP_FOR_CODE_CHANGE",
        "PLANNER_INVALID_REPLACEMENT_CONTENT",
        "PLANNER_MUTATION_OUTSIDE_TARGET_FILES",
        "PLANNER_EDIT_WITHOUT_IMMEDIATE_READ",
        "PLANNER_APPLY_PATCH_REQUIRES_SINGLE_FILE_CONTEXT",
        "PLANNER_FIRST_PASS_TEST_MUTATION_FORBIDDEN",
        "BODY_INSERT_INVALID_PYTHON_SYNTAX",
        "DUPLICATE_MUTATION_TARGET",
        "SYMBOL_NOT_FOUND",  # from pre-execution resolver pass (AIFIRST-5)
    ],
    "WORKSPACE_ERROR": ["WORKSPACE_NOT_FOUND"],
    "AUTH_FAILED": ["AUTH_MISSING"],
    "EXECUTOR_ERROR": ["EXECUTOR_RUN_FAILED", "EXECUTOR_RUN_NOT_FOUND"],
    "EXECUTION_ERROR": [
        "EDIT_SYMBOL_DRY_RUN_FAILED",
        "EXECUTOR_TERMINAL_STEP_FAILED",
        "TOKEN_BUDGET_EXCEEDED",
        "RESUME_RUN_NOT_FOUND",
        "RESUME_CHAIN_LIMIT_EXCEEDED",
    ],
    "GROUNDING_ERROR": ["GROUNDING_CONFIRMATION_REQUIRED"],
}


def _lookup_remediation(sub_code: str) -> str:
    """Return remediation text for sub_code, with prefix fallback.

    Some sub_codes embed context (e.g. "INVALID_TRANSITION expected=confirm_anchor got=replace_in_function").
    We try an exact match first, then fall back to prefix matching against known keys.
    """
    if sub_code in _REMEDIATION:
        return _REMEDIATION[sub_code]
    for key in _REMEDIATION:
        if sub_code.startswith(key):
            return _REMEDIATION[key]
    return _DEFAULT_REMEDIATION


def aix_http_error(
    *,
    http_status: int,
    code: str,
    sub_code: str,
    detail: str | None = None,
    context: dict[str, Any] | None = None,
) -> HTTPException:
    """Return an ``HTTPException`` with a structured AIX error body.

    The JSON ``detail`` payload is a dict suitable for AI agent consumption:
    ``code``, ``sub_code``, ``remediation``, ``detail`` (backwards compat),
    and optional ``context``.

    Usage::

        raise aix_http_error(
            http_status=422,
            code="PLAN_VALIDATION_FAILED",
            sub_code="INVALID_TRANSITION",
            detail="Step 2 is a mutation without preceding confirm_anchor",
            context={"step_index": 2, "tool": "replace_in_function"},
        )
    """
    remediation = _lookup_remediation(sub_code)
    body: dict[str, Any] = {
        "code": code,
        "sub_code": sub_code,
        "remediation": remediation,
        "detail": detail or f"{code}: {sub_code}",
    }
    if context:
        body["context"] = context
    return HTTPException(status_code=http_status, detail=body)


__all__ = ["AIX_ERROR_CODES", "aix_http_error"]
