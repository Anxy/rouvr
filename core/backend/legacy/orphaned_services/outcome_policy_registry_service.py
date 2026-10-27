from __future__ import annotations

from typing import Any

from backend.services.devexecutor_mutation_tool_taxonomy_service import canonical_code_mutation_tool_phrase


def _normalize_text(value: object) -> str:
    return str(value or "").strip()


def _normalize_targets(targets: list[str] | None, *, fallback: list[str] | None = None) -> list[str]:
    normalized = [_normalize_text(item).replace("\\", "/") for item in list(targets or []) if _normalize_text(item)]
    if normalized:
        return normalized
    return [_normalize_text(item).replace("\\", "/") for item in list(fallback or []) if _normalize_text(item)]


def _normalize_upper(value: object) -> str:
    return _normalize_text(value).upper()


def _normalize_lower(value: object) -> str:
    return _normalize_text(value).lower()


def _coerce_bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    text = _normalize_lower(value)
    if text in {"1", "true", "yes", "y"}:
        return True
    if text in {"0", "false", "no", "n"}:
        return False
    return None


def _base_entry(
    *,
    category: str,
    outcome_class: str,
    tool_class: str,
    policy_action: str,
    failure_family: str | None = None,
    retry_budget_key: str | None = None,
    retry_rules: list[str] | None = None,
    next_best_actions: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "category": category,
        "outcome_class": outcome_class,
        "tool_class": tool_class,
        "policy_action": policy_action,
        "failure_family": failure_family or "",
        "retry_budget_key": retry_budget_key or "",
        "retry_rules": list(retry_rules or []),
        "next_best_actions": list(next_best_actions or []),
    }


def _default_route_target(
    *,
    code: str,
    category: str,
    outcome_class: str,
    tool_class: str,
    policy_action: str,
    failure_family: str,
) -> str:
    normalized_code = _normalize_upper(code)
    normalized_category = _normalize_text(category)
    normalized_outcome = _normalize_text(outcome_class)
    normalized_tool_class = _normalize_text(tool_class)
    normalized_action = _normalize_text(policy_action)
    normalized_family = _normalize_text(failure_family)
    if normalized_code == "ADMISSION_CONTRACT_VIOLATED" or normalized_category == "platform_defect":
        return "platform_defect"
    if normalized_code in {"TASK_CLARIFICATION_REQUIRED", "VERIFICATION_TASK_INVALID_INTENT"}:
        return "onlineai"
    if normalized_code == "TASK_DECOMPOSITION_REQUIRED":
        return "plannerai"
    if normalized_action == "replan" or normalized_family == "grounding_failed":
        return "plannerai"
    if normalized_action == "retry":
        return "same_lane_retry"
    if normalized_action == "verifier_reject" or normalized_outcome.startswith("quality."):
        return "recovery_task"
    if normalized_action == "continue":
        return "same_lane_continue"
    if normalized_action == "fail":
        return "terminal_fail"
    if normalized_action == "escalate":
        if normalized_tool_class == "planner":
            return "onlineai"
        return "platform_defect"
    return "onlineai"


_OUTCOME_RULES: dict[str, dict[str, Any]] = {
    "ADMISSION_CONTRACT_VIOLATED": _base_entry(
        category="platform_defect",
        outcome_class="platform.admission_contract_violated",
        tool_class="runner",
        policy_action="escalate",
        next_best_actions=[
            "inspect the canonical pre-execution gate and runtime refusal payload together",
            "fix the control-plane divergence before retrying autonomous execution",
            "do not send this back to the user as a clarification request",
        ],
    ),
    "TASK_CLARIFICATION_REQUIRED": _base_entry(
        category="clarification_required",
        outcome_class="task.clarification_required",
        tool_class="planner",
        policy_action="escalate",
        next_best_actions=[
            "clarify the intended implementation surface before planner mutation begins",
            "add acceptance criteria or a targeted validation command",
            "provide grounded target files or symbol targets for the first mutation slice",
        ],
    ),
    "VERIFICATION_TASK_INVALID_INTENT": _base_entry(
        category="clarification_required",
        outcome_class="task.verification_invalid_intent",
        tool_class="planner",
        policy_action="escalate",
        next_best_actions=[
            "change the task intent to verification_only before retrying this slice",
            "keep verification slices out of the mutating code-change lane",
            "retry only after the task packet explicitly declares a non-mutating verification intent",
        ],
    ),
    "TASK_DECOMPOSITION_REQUIRED": _base_entry(
        category="clarification_required",
        outcome_class="task.decomposition_required",
        tool_class="planner",
        policy_action="escalate",
        next_best_actions=[
            "split the task into bounded subtasks before planner mutation begins",
            "start with a single-file or tightly grounded first slice with explicit acceptance criteria",
            "defer broader multi-file or new-abstraction work until the prerequisite slices are complete",
        ],
    ),
    "MUTATION_INVALID_FUNCTION_SHAPE": _base_entry(
        category="execution_failure",
        outcome_class="mutation.invalid_shape",
        tool_class="mutation",
        policy_action="retry",
        failure_family="parse_error",
        retry_budget_key="parse_failure",
        retry_rules=[
            "preserve the existing function signature and surrounding control flow unless the task explicitly changes them",
            "prefer a localized mutation instead of a broad whole-function rewrite when the previous candidate failed pre-mutation validation",
            "re-ground on the live symbol before retrying the mutation",
        ],
        next_best_actions=[
            "retry with a smaller localized edit that preserves the confirmed function shape",
            "switch away from broad full-symbol rewrite unless the task explicitly requires replacing the whole function",
            "re-run the targeted validation command after the corrected mutation lands",
        ],
    ),
    "ANCHOR_NOT_FOUND_COMPAT": _base_entry(
        category="execution_failure",
        outcome_class="mutation.anchor_compat_shim",
        tool_class="mutation",
        policy_action="retry",
        failure_family="parse_error",
        retry_budget_key="parse_failure",
        retry_rules=[
            "do not retry with body_insert+anchor_pattern; the anchor compatibility shim has been exhausted",
            "prefer edit_file with raw SEARCH/REPLACE blocks for localized edits after reading the current file content",
            "use replace_in_function with args.function_name and the complete updated function body as args.replacement only when a full function replacement is required",
            "if neither edit_file nor replace_in_function is suitable, use write_file with the full updated file content",
            "read the current function body or file text with read_range or read_file before writing the replacement",
        ],
        next_best_actions=[
            "switch from body_insert+anchor to edit_file for the failed localized change",
            "read the target text first, apply the intended logic change, then emit exact SEARCH/REPLACE blocks",
        ],
    ),
    "TARGET_LOGIC_NOT_CONFIRMED": _base_entry(
        category="diagnostic_blocked",
        outcome_class="discovery.miss",
        tool_class="planner",
        policy_action="replan",
        failure_family="grounding_failed",
        retry_rules=[
            "confirm the target logic in an allowed file before any mutating step",
            "inject owning-symbol or route-level anchors before replanning",
            "retry with a single-file mutating plan only after the target logic is confirmed",
        ],
        next_best_actions=[
            "inject stronger semantic anchors for the target files",
            "retry with narrower owning-symbol guidance",
            "escalate for human clarification if target logic is absent from allowed files",
        ],
    ),
    "GROUNDING_MISMATCH": _base_entry(
        category="diagnostic_blocked",
        outcome_class="discovery.anchor_mismatch",
        tool_class="planner",
        policy_action="replan",
        failure_family="grounding_failed",
        retry_rules=[
            "re-read the exact target symbol immediately before mutation",
            "confirm anchor text against current file content",
            "do not mutate against stale symbol boundaries or stale planner context",
        ],
        next_best_actions=[
            "re-read the exact target symbol and confirm anchor text",
            "refresh semantic anchors before any symbol-scoped mutation",
            "retry with current file state instead of stale planner assumptions",
        ],
    ),
    "INVALID_CONFIRM_ANCHOR_PREREAD": _base_entry(
        category="execution_failure",
        outcome_class="planner.invalid_transition",
        tool_class="planner",
        policy_action="retry",
        failure_family="parse_error",
        retry_budget_key="parse_failure",
        retry_rules=[
            "insert a read_range or read_file step immediately before confirm_anchor for the same file",
            "do not emit confirm_anchor until the file has been read in the current plan",
            "after confirm_anchor, proceed directly to one symbol-scoped mutation on that same file",
        ],
        next_best_actions=[
            "repair the step ordering so confirm_anchor follows an immediate same-file read",
            "retry with a single-file plan that reads, anchors, mutates, and validates in order",
        ],
    ),
    "INVALID_READ_RANGE_BOUNDS": _base_entry(
        category="execution_failure",
        outcome_class="planner.invalid_read_range_bounds",
        tool_class="planner",
        policy_action="retry",
        failure_family="parse_error",
        retry_budget_key="parse_failure",
        retry_rules=[
            "use positive start_line and end_line values with end_line >= start_line",
            "re-read the file before proposing bounds if exact line numbers are not known",
            "keep end_line within the actual file line count when that count is available",
        ],
        next_best_actions=[
            "repair the read_range bounds and retry with a concrete mutating plan",
            "prefer read_file before mutation when exact line ranges are unknown",
        ],
    ),
    "INVALID_EDIT_FILE_SEARCH_REPLACE_BLOCKS": _base_entry(
        category="execution_failure",
        outcome_class="planner.invalid_edit_file_blocks",
        tool_class="planner",
        policy_action="retry",
        failure_family="parse_error",
        retry_budget_key="parse_failure",
        retry_rules=[
            "emit raw SEARCH/REPLACE blocks only, with one or more complete <<<<<<< SEARCH / ======= / >>>>>>> REPLACE sections",
            "do not pass plain source text, prose, or partial block fragments as search_replace_blocks",
            "ground the edit_file payload on live file content and preserve the declared validation command after the mutating step",
        ],
        next_best_actions=[
            "retry with a properly framed SEARCH/REPLACE payload for edit_file",
            "copy exact live file text into SEARCH and only the intended replacement into REPLACE",
            "keep the retry plan mutation-capable instead of downgrading to discovery-only steps",
        ],
    ),
    "SEARCH_REPLACE_MISMATCH": _base_entry(
        category="execution_failure",
        outcome_class="mutation.search_replace_mismatch",
        tool_class="mutation",
        policy_action="retry",
        failure_family="mutation_semantic",
        retry_budget_key="parse_failure",
        retry_rules=[
            "do not escalate immediately when edit_file SEARCH text misses the live file content",
            "extract the failed SEARCH text and compare it against a localized live file window before regenerating the block",
            "regenerate only the SEARCH/REPLACE blocks for the failed edit_file step instead of rewriting the whole plan",
            "preserve the bounded target file and validation command from the original slice",
        ],
        next_best_actions=[
            "re-read a localized window around the failed SEARCH text",
            "repair the edit_file SEARCH/REPLACE block against the live file content",
            "rerun the bounded validation command after the corrected edit lands",
        ],
    ),
    "NEW_FILE_EXTRACTION_REQUIRES_CREATE_FIRST": _base_entry(
        category="execution_failure",
        outcome_class="planner.new_file_extraction_requires_create_first",
        tool_class="planner",
        policy_action="retry",
        failure_family="parse_error",
        retry_budget_key="parse_failure",
        retry_rules=[
            "for mixed extraction tasks, create the missing file before mutating the existing file",
            "use write_file or an explicit file-creating apply_patch for the new module",
            "after creating the new file, re-read the existing file before any edit_file SEARCH/REPLACE mutation",
        ],
        next_best_actions=[
            "retry with create-first sequencing for the missing extraction target",
            "then re-read the existing implementation file and apply a grounded localized edit",
        ],
    ),
    "BASE_DIVERGED": _base_entry(
        category="execution_failure",
        outcome_class="mutation.conflict",
        tool_class="mutation",
        policy_action="fail",
        failure_family="BASE_DIVERGED",
    ),
    "SYMBOL_DIVERGED": _base_entry(
        category="execution_failure",
        outcome_class="mutation.conflict",
        tool_class="mutation",
        policy_action="fail",
        failure_family="SYMBOL_DIVERGED",
    ),
    "FEATURE_SLICE_INCOMPLETE": _base_entry(
        category="quality_gate_rejected",
        outcome_class="evidence.incomplete",
        tool_class="verifier",
        policy_action="verifier_reject",
        failure_family="test_local",
        retry_budget_key="test_local_failure",
        retry_rules=[
            "extend the declared test slice so the requested behavior is asserted explicitly",
            "run the required targeted validation command and attach its output as evidence",
        ],
    ),
    "CANDIDATE_VALIDATION_MISSING": _base_entry(
        category="quality_gate_rejected",
        outcome_class="evidence.incomplete",
        tool_class="verifier",
        policy_action="verifier_reject",
        failure_family="test_local",
        retry_budget_key="test_local_failure",
        retry_rules=[
            "run the required targeted validation command for the changed slice",
            "attach validation output as evidence before review",
        ],
    ),
    "HARNESS_VALIDATION_MISMATCH": _base_entry(
        category="quality_gate_rejected",
        outcome_class="harness.validation_mismatch",
        tool_class="verifier",
        policy_action="verifier_reject",
        failure_family="harness",
        retry_budget_key="test_local_failure",
        retry_rules=[
            "preserve the candidate mutation; repair the harness command evidence projection",
            "compare validation command identity by argv hash, not shell display text",
        ],
    ),
    "ACCEPTANCE_MAPPING_MISSING": _base_entry(
        category="quality_gate_rejected",
        outcome_class="evidence.incomplete",
        tool_class="verifier",
        policy_action="retry",
        failure_family="test_local",
        retry_budget_key="test_local_failure",
        retry_rules=[
            "attach acceptance mapping rows for every declared acceptance criterion before review",
            "derive acceptance proof from the required validation evidence instead of leaving acceptance unmapped",
            "re-run or reuse the canonical targeted validation command so acceptance mapping has concrete proof references",
        ],
        next_best_actions=[
            "synthesize or attach acceptance mapping rows backed by the executed validation commands",
            "preserve the successful candidate and repair the missing acceptance proof semantics only",
        ],
    ),
    "ACCEPTANCE_PROOF_MISSING": _base_entry(
        category="quality_gate_rejected",
        outcome_class="evidence.incomplete",
        tool_class="verifier",
        policy_action="retry",
        failure_family="test_local",
        retry_budget_key="test_local_failure",
        retry_rules=[
            "ensure every declared acceptance mapping item is actually met before review",
            "repair the implementation or targeted validation evidence for any unmet acceptance item",
            "preserve the candidate and close the unmet acceptance proof gap directly",
        ],
        next_best_actions=[
            "repair the unmet acceptance item and rerun the targeted validation commands",
            "update acceptance mapping only after the unmet acceptance condition is truly satisfied",
        ],
    ),
    "SKIPPED_REQUIRED_VALIDATION": _base_entry(
        category="quality_gate_rejected",
        outcome_class="evidence.incomplete",
        tool_class="verifier",
        policy_action="retry",
        failure_family="test_local",
        retry_budget_key="test_local_failure",
        retry_rules=[
            "do not treat skipped targeted validation as sufficient proof for a successful run",
            "re-run the required validation commands without skip-only outcomes",
            "attach the completed validation output as evidence before review",
        ],
        next_best_actions=[
            "execute the required targeted validation commands to completion",
            "replace skipped-only proof with completed validation evidence",
        ],
    ),
    "WIRING_INCOMPLETE_ROUTER_THREADING": _base_entry(
        category="quality_gate_rejected",
        outcome_class="mutation.incomplete",
        tool_class="mutation",
        policy_action="replan",
    ),
    "NOOP_CODE_CHANGE": _base_entry(
        category="quality_gate_rejected",
        outcome_class="mutation.incomplete",
        tool_class="mutation",
        policy_action="retry",
        failure_family="mutation_missing",
        retry_budget_key="mutation_missing",
        retry_rules=[
            "produce at least one substantive code change in an allowed target file",
            "run and capture the targeted validation command for the changed slice",
        ],
        next_best_actions=[
            "perform the required implementation change in the declared target files",
            "rerun the targeted validation commands after the required mutation lands",
        ],
    ),
    "PLANNER_NO_MUTATING_STEP_FOR_CODE_CHANGE": _base_entry(
        category="execution_failure",
        outcome_class="planner.no_mutation",
        tool_class="planner",
        policy_action="retry",
        failure_family="mutation_missing",
        retry_budget_key="mutation_missing",
        retry_rules=[
            "code_change_required tasks must include at least one mutating step",
            "after reading and confirming the target, add a concrete mutation step in an allowed target file",
            "do not submit a discovery-only plan for a code_change_required task",
            "use the admitted symbol targets or allowed target files to ground the mutation step",
        ],
        next_best_actions=[
            f"add a valid mutating step such as {canonical_code_mutation_tool_phrase(include_deprecated=True)} in the allowed target file",
            "confirm the symbol with confirm_anchor before symbol-scoped mutation when symbols are grounded",
            "re-read the target file if mutation grounding is uncertain",
        ],
    ),
    "PLANNER_INVALID_REPLACEMENT_CONTENT": _base_entry(
        category="execution_failure",
        outcome_class="planner.invalid_replacement_content",
        tool_class="planner",
        policy_action="retry",
        failure_family="mutation_invalid",
        retry_budget_key="mutation_missing",
        retry_rules=[
            "replace_function replacement must be one complete concrete function definition copied from grounded source",
            "do not emit placeholders, elision markers, omitted unchanged code, or summarized bodies",
            "regenerate the full function definition from the observed read_range(symbol) output before retrying",
        ],
        next_best_actions=[
            "re-read the grounded symbol and regenerate the complete function body with only the bounded logic changed",
            "preserve async/def kind, exact symbol name, and receiver shape while keeping unchanged logic concrete",
        ],
    ),
    "MUTATION_REQUIRED_BUT_ABSENT": _base_entry(
        category="execution_failure",
        outcome_class="planner.no_mutation",
        tool_class="planner",
        policy_action="retry",
        failure_family="mutation_missing",
        retry_budget_key="mutation_missing",
        retry_rules=[
            "code_change_required tasks must include at least one mutating step or explicit failure_analysis after the grounded read path",
            "after reading and confirming the target, add a concrete mutation step in an allowed target file",
            "do not submit a discovery-only or no-op plan for a code_change_required task",
        ],
        next_best_actions=[
            f"add a concrete valid mutating step such as {canonical_code_mutation_tool_phrase(include_deprecated=True)} in the allowed target file",
            "if a safe mutation still cannot be proposed, return explicit failure_analysis instead of a fake success plan",
        ],
    ),
    "PLANNER_PHASE_TIMEOUT": _base_entry(
        category="execution_failure",
        outcome_class="planner.timeout",
        tool_class="planner",
        policy_action="escalate",
        failure_family="planner_timeout",
        retry_rules=[
            "the planner and its repair loop must finish inside the canonical plan phase budget",
            "tighten the bounded task contract or reduce planner retries before re-running the lane",
            "prefer deterministic target logic or explicit mutation semantics when planner retries are timing out",
        ],
        next_best_actions=[
            "inspect planner traces and retry history for repeated non-mutating or invalid plans",
            "tighten the task packet so the first planner candidate can emit a concrete mutation step",
            "rerun after the planner contract is made more deterministic",
        ],
    ),
    "PLANNER_MODEL_CAPACITY_EXCEEDED": _base_entry(
        category="execution_failure",
        outcome_class="planner.capacity_exceeded",
        tool_class="planner",
        policy_action="escalate",
        failure_family="capacity",
        retry_rules=[
            "the ordered planner candidate pool exhausted its admitted latency or capacity budget",
            "prefer a faster planner candidate or a narrower packet before re-running the lane",
            "do not collapse provider or model capacity exhaustion into an unknown planner failure class",
        ],
        next_best_actions=[
            "inspect planner trace latency and candidate fallback evidence",
            "narrow the planner packet or choose a lower-latency planner lane before retrying",
            "escalate with the preserved capacity lineage when the planner pool cannot satisfy the lane budget",
        ],
    ),
    "PLANNER_VALIDATION_COMMAND_MISSING": _base_entry(
        category="execution_failure",
        outcome_class="planner.validation_missing",
        tool_class="planner",
        policy_action="retry",
        failure_family="test_local",
        retry_budget_key="parse_failure",
        retry_rules=[
            "if validation_commands are declared, the plan must include one of them via cmd.run",
            "preserve the bounded mutation plan and add the canonical validation command after the mutation step",
            "do not treat py_compile or get_status as a substitute for the declared pytest command",
        ],
        next_best_actions=[
            "add a cmd.run step for the declared validation command after the mutation",
            "keep the validation command text identical to the declared canonical command",
        ],
    ),
    "CANDIDATE_SYNTAX_INVALID": _base_entry(
        category="quality_gate_rejected",
        outcome_class="mutation.invalid_patch",
        tool_class="mutation",
        policy_action="retry",
        failure_family="parse_error",
        retry_budget_key="parse_failure",
        retry_rules=[
            "preserve valid function boundaries and indentation",
            "do not insert new definitions inside an existing function body or parameter list",
            "retry with file-aware edits on exact confirmed ranges when syntax drift occurs",
        ],
        next_best_actions=[
            "repair syntax and indentation boundaries in the changed files",
            "re-run the targeted validation commands after syntax repair",
        ],
    ),
    "CONTENT_VALIDATION_FAILED": _base_entry(
        category="quality_gate_rejected",
        outcome_class="mutation.invalid_patch",
        tool_class="mutation",
        policy_action="retry",
        failure_family="lint_error",
        retry_budget_key="lint_failure",
        retry_rules=[
            "repair the candidate so it satisfies static content validation before any test run",
            "preserve route decorators and module-scope dependency imports while correcting the candidate",
            "re-run the declared validation command after the corrected mutation lands",
        ],
        next_best_actions=[
            "fix the content validator rejection in the changed Python file",
            "preserve module-scope imports and signature-level FastAPI dependencies",
            "retry the focused validation command after the correction",
        ],
    ),
    "PYTHON_SYNTAX_INVALID": _base_entry(
        category="quality_gate_rejected",
        outcome_class="mutation.invalid_patch",
        tool_class="mutation",
        policy_action="retry",
        failure_family="parse_error",
        retry_budget_key="parse_failure",
        retry_rules=[
            "preserve valid function boundaries and indentation",
            "do not insert new definitions inside an existing function body or parameter list",
            "retry with file-aware edits on exact confirmed ranges when syntax drift occurs",
        ],
        next_best_actions=[
            "repair syntax and indentation boundaries in the changed files",
            "re-run the targeted validation commands after syntax repair",
        ],
    ),
    "INVALID_NEW_CODE_SYNTAX": _base_entry(
        category="quality_gate_rejected",
        outcome_class="mutation.invalid_patch",
        tool_class="mutation",
        policy_action="retry",
        failure_family="parse_error",
        retry_budget_key="parse_failure",
        retry_rules=[
            "preserve valid function boundaries and indentation",
            "do not insert new definitions inside an existing function body or parameter list",
            "retry with file-aware edits on exact confirmed ranges when syntax drift occurs",
        ],
        next_best_actions=[
            "repair syntax and indentation boundaries in the changed files",
            "re-run the targeted validation commands after syntax repair",
        ],
    ),
    "COMPLETENESS_FAIL": _base_entry(
        category="quality_gate_rejected",
        outcome_class="evidence.incomplete",
        tool_class="verifier",
        policy_action="retry",
        failure_family="test_local",
        retry_budget_key="test_local_failure",
        retry_rules=[
            "strict completeness requires both implementation and test-side evidence for the changed slice",
            "add the missing implementation or test file change before review",
            "re-run the targeted validation commands after the slice is complete",
        ],
        next_best_actions=[
            "complete the missing implementation-or-test side of the slice",
            "rerun the targeted validation commands after the slice is complete",
        ],
    ),
    "PATCH_HYGIENE_MID_FILE_IMPORT": _base_entry(
        category="quality_gate_rejected",
        outcome_class="mutation.invalid_patch",
        tool_class="mutation",
        policy_action="retry",
        failure_family="lint_error",
        retry_budget_key="lint_failure",
        retry_rules=[
            "do not insert new imports after executable code in a python module",
            "reuse existing imports when possible and move any new imports into the top import block",
            "preserve the accepted behavior change and repair patch shape only",
        ],
        next_best_actions=[
            "move new imports into the module import block or replace them with an existing available symbol",
            "rerun the targeted validation commands after the import placement is repaired",
        ],
    ),
    "PATCH_HYGIENE_ROUTE_IN_LIFECYCLE_ZONE": _base_entry(
        category="quality_gate_rejected",
        outcome_class="mutation.invalid_patch",
        tool_class="mutation",
        policy_action="retry",
        failure_family="lint_error",
        retry_budget_key="lint_failure",
        retry_rules=[
            "do not insert new routes inside startup or shutdown lifecycle handlers",
            "place new route definitions as top-level sibling routes next to existing route functions",
            "preserve the requested endpoint behavior while repairing the route placement only",
        ],
        next_best_actions=[
            "move the new route outside lifecycle handlers into the normal route section",
            "rerun the targeted validation commands after the route placement is repaired",
        ],
    ),
    "PATCH_HYGIENE_LIFECYCLE_ROUTE_INSERTION": _base_entry(
        category="quality_gate_rejected",
        outcome_class="mutation.invalid_patch",
        tool_class="mutation",
        policy_action="retry",
        failure_family="lint_error",
        retry_budget_key="lint_failure",
        retry_rules=[
            "do not insert new routes inside startup or shutdown lifecycle handlers",
            "place new route definitions as top-level sibling routes next to existing route functions",
            "preserve the requested endpoint behavior while repairing the route placement only",
        ],
        next_best_actions=[
            "move the new route outside lifecycle handlers into the normal route section",
            "rerun the targeted validation commands after the route placement is repaired",
        ],
    ),
    "CANDIDATE_OUTSIDE_TARGETS": _base_entry(
        category="quality_gate_rejected",
        outcome_class="mutation.invalid_patch",
        tool_class="mutation",
        policy_action="retry",
        failure_family="parse_error",
        retry_budget_key="parse_failure",
        retry_rules=[
            "keep all candidate mutations inside the admitted target files",
            "remove or relocate edits that landed outside the declared target surface",
            "re-run the targeted validation commands after the patch is constrained to allowed files",
        ],
        next_best_actions=[
            "limit the patch to the admitted target files only",
            "rerun the targeted validation commands after removing out-of-scope edits",
        ],
    ),
    "CANDIDATE_STUB_REWRITE": _base_entry(
        category="quality_gate_rejected",
        outcome_class="mutation.invalid_patch",
        tool_class="mutation",
        policy_action="retry",
        failure_family="parse_error",
        retry_budget_key="parse_failure",
        retry_rules=[
            "do not leave placeholder, simulated, or TODO-style code in the final candidate",
            "replace stubbed logic with concrete implementation or remove the placeholder entirely",
            "preserve valid surrounding edits while repairing the placeholder content",
        ],
        next_best_actions=[
            "replace placeholder logic with concrete implementation",
            "rerun the targeted validation commands after removing stub markers",
        ],
    ),
    "CONTENT_SYNTAX_ERROR": _base_entry(
        category="execution_failure",
        outcome_class="mutation.invalid_patch",
        tool_class="mutation",
        policy_action="retry",
        failure_family="parse_error",
        retry_budget_key="parse_failure",
        retry_rules=[
            "for replace_in_function, provide content indented to match the target function body",
            "do not submit a bare indented block as top-level module content",
            "re-ground on the live symbol before retrying the mutation",
        ],
        next_best_actions=[
            "check that inserted content indentation matches the surrounding function scope",
            "retry with corrected indentation in the replacement block",
        ],
    ),
    "MUTATION_INVALID_FUNCTION_REPLACEMENT": _base_entry(
        category="execution_failure",
        outcome_class="mutation.invalid_shape",
        tool_class="mutation",
        policy_action="retry",
        failure_family="parse_error",
        retry_budget_key="parse_failure",
        retry_rules=[
            "replace_in_function requires a complete function definition starting with def or async def",
            "do not submit a body-only fragment; include the full signature and body",
            "confirm the exact function signature with confirm_anchor before retrying",
            "for body-only changes use edit_file or replace_range instead",
        ],
        next_best_actions=[
            "retry with replacement content that starts with the complete def or async def line",
            "if only a small body change is needed, switch to edit_file or replace_range",
        ],
    ),
    "COMPILE_FAIL": _base_entry(
        category="execution_failure",
        outcome_class="runtime.compile_failed",
        tool_class="mutation",
        policy_action="retry",
        failure_family="compile_error",
        retry_budget_key="parse_failure",
    ),
    "PY_COMPILE_FAILED": _base_entry(
        category="execution_failure",
        outcome_class="runtime.compile_failed",
        tool_class="mutation",
        policy_action="retry",
        failure_family="compile_error",
        retry_budget_key="parse_failure",
    ),
    "RUFF_FAILED": _base_entry(
        category="execution_failure",
        outcome_class="quality.lint_failed",
        tool_class="verifier",
        policy_action="retry",
        failure_family="lint_error",
        retry_budget_key="lint_failure",
    ),
    "LINT_FAILED": _base_entry(
        category="execution_failure",
        outcome_class="quality.lint_failed",
        tool_class="verifier",
        policy_action="retry",
        failure_family="lint_error",
        retry_budget_key="lint_failure",
    ),
    "EDIT_SYMBOL_DRY_RUN_FAILED": _base_entry(
        category="execution_failure",
        outcome_class="mutation.dry_run_failed",
        tool_class="mutation",
        policy_action="retry",
        failure_family="lint_error",
        retry_budget_key="lint_failure",
        retry_rules=[
            "switch away from broad whole-symbol replacement for large orchestrator functions",
            "prefer replace_in_function or edit_file for localized changes",
            "re-ground on the live symbol before retrying the mutation",
        ],
        next_best_actions=[
            "retry with replace_in_function or edit_file instead of a broad whole-symbol rewrite",
            "confirm symbol boundary with confirm_anchor before retrying",
            "re-run the targeted validation command after the corrected mutation lands",
        ],
    ),
    "INVALID_SYMBOL_FORMAT": _base_entry(
        category="execution_failure",
        outcome_class="planner.invalid_symbol",
        tool_class="planner",
        policy_action="retry",
        failure_family="parse_error",
        retry_budget_key="parse_failure",
        retry_rules=[
            "use the exact fully qualified symbol name from prompt_symbol_targets",
            "module-level functions require module-prefixed form: module_name.function_name",
            "class methods require Class.method form",
            "never use a bare unqualified identifier for confirm_anchor or any symbol-scoped step",
        ],
        next_best_actions=[
            "reuse the exact symbol string from admitted_symbol_targets in the planner packet",
            "do not strip the module or class prefix from the symbol name",
            "retry confirm_anchor with the fully qualified symbol name",
        ],
    ),
    "PRIMITIVE_POLICY_VIOLATION": _base_entry(
        category="execution_failure",
        outcome_class="planner.disallowed_primitive",
        tool_class="planner",
        policy_action="retry",
        failure_family="parse_error",
        retry_budget_key="parse_failure",
        retry_rules=[
            "do not use broad whole-symbol replacement for large orchestrator or runner-core functions",
            "use only the primitives listed in the constrained retry payload",
            "allowed primitives for large_orchestrator class: replace_in_function",
        ],
        next_best_actions=[
            "retry using replace_in_function or edit_file instead of a broad whole-symbol rewrite",
            "check validation_error_context in the planner packet for the exact allowed primitive list",
            "do not attempt a broad whole-symbol rewrite again for this symbol class",
        ],
    ),
    "PLANNER_UPSTREAM_REQUEST_FAILED": _base_entry(
        category="execution_failure",
        outcome_class="runtime.unreachable",
        tool_class="planner",
        policy_action="retry",
        failure_family="env_error",
        retry_budget_key="test_local_failure",
    ),
    "PLANNER_TRANSPORT_STALE_SOCKET": _base_entry(
        category="execution_failure",
        outcome_class="runtime.transport.stale_socket",
        tool_class="planner",
        policy_action="retry",
        failure_family="env_error",
        retry_budget_key="test_local_failure",
        next_best_actions=[
            "evict the stale planner connection and retry with a fresh socket",
            "prefer a transport-diverse fallback candidate before escalating",
        ],
    ),
    "PLANNER_TRANSPORT_TIMEOUT": _base_entry(
        category="execution_failure",
        outcome_class="runtime.transport.timeout",
        tool_class="planner",
        policy_action="retry",
        failure_family="env_error",
        retry_budget_key="test_local_failure",
        next_best_actions=[
            "mark the timed-out planner candidate degraded for this invocation",
            "retry on the next transport-diverse planner candidate with backoff",
        ],
    ),
    "PLANNER_TRANSPORT_CONNECT_FAILED": _base_entry(
        category="execution_failure",
        outcome_class="runtime.transport.connect_failed",
        tool_class="planner",
        policy_action="retry",
        failure_family="env_error",
        retry_budget_key="test_local_failure",
        next_best_actions=[
            "verify planner transport reachability before dispatching another request",
            "retry on a transport-diverse fallback candidate",
        ],
    ),
    "PLANNER_TRANSPORT_EXHAUSTED": _base_entry(
        category="execution_failure",
        outcome_class="runtime.transport.exhausted",
        tool_class="planner",
        policy_action="escalate",
        failure_family="env_error",
        next_best_actions=[
            "restore at least one healthy planner transport path before refiring the task",
            "inspect planner bridge preflight diagnostics and candidate failure chain",
        ],
    ),
    "PLANNER_PAYLOAD_CAP_EXCEEDED": _base_entry(
        category="execution_failure",
        outcome_class="planner.payload_too_large",
        tool_class="planner",
        policy_action="escalate",
        failure_family="parse_error",
        next_best_actions=[
            "downselect to an oversized-safe planner lane before dispatch",
            "reduce the planner payload size instead of retrying the same request body",
        ],
    ),
    "PLANNER_UPSTREAM_FAILED": _base_entry(
        category="execution_failure",
        outcome_class="runtime.upstream_failed",
        tool_class="planner",
        policy_action="escalate",
        failure_family="env_error",
    ),
    "WORKSPACE_ROOT_NOT_ALLOWED": _base_entry(
        category="execution_failure",
        outcome_class="runtime.policy_blocked",
        tool_class="executor",
        policy_action="escalate",
        failure_family="env_error",
        next_best_actions=[
            "use an allowed workspace root for the proposal or execution environment",
            "correct the workspace policy configuration before retrying",
        ],
    ),
    "HOST_MUTATION_BLOCKED": _base_entry(
        category="execution_failure",
        outcome_class="runtime.policy_blocked",
        tool_class="executor",
        policy_action="escalate",
        failure_family="env_error",
        next_best_actions=[
            "route the mutation through the proposal workspace instead of mutating the host checkout",
            "correct the execution environment policy before retrying",
        ],
    ),
    "PROPOSAL_WORKSPACE_PREP_FAILED": _base_entry(
        category="execution_failure",
        outcome_class="runtime.workspace_prep_failed",
        tool_class="executor",
        policy_action="retry",
        failure_family="env_error",
        retry_budget_key="test_local_failure",
        retry_rules=[
            "repair proposal workspace preparation before retrying execution",
            "preserve the candidate plan and rerun after workspace bootstrap succeeds",
        ],
        next_best_actions=[
            "repair proposal workspace setup and rerun the task",
            "verify the proposal workspace can emit diff and changed-files artifacts before retrying",
        ],
    ),
    "TOOL_STEP_FAILED": _base_entry(
        category="execution_failure",
        outcome_class="command.nonzero",
        tool_class="executor",
        policy_action="fail",
    ),
    "EXECUTION_FAILED": _base_entry(
        category="execution_failure",
        outcome_class="command.nonzero",
        tool_class="executor",
        policy_action="fail",
    ),
    "EXECUTION_NOT_COMPLETED": _base_entry(
        category="execution_failure",
        outcome_class="command.nonzero",
        tool_class="executor",
        policy_action="fail",
    ),
    "EXECUTOR_TERMINAL_STEP_FAILED": _base_entry(
        category="execution_failure",
        outcome_class="mutation.terminal_step_failed",
        tool_class="mutation",
        policy_action="retry",
        failure_family="lint_error",
        retry_budget_key="lint_failure",
        retry_rules=[
            "re-ground on the live symbol before retrying the mutation",
            "prefer a localized body-scoped mutation instead of a broad whole-symbol rewrite",
            "confirm the anchor quote matches the exact function definition in the current file",
        ],
        next_best_actions=[
            "retry with a narrower mutation primitive scoped to the confirmed symbol",
            "re-run confirm_anchor before any symbol-scoped mutation step",
            "re-run the targeted validation command after the corrected mutation lands",
        ],
    ),
    "WORKSPACE_MISSING": _base_entry(
        category="execution_failure",
        outcome_class="workspace.invalid_path",
        tool_class="runtime",
        policy_action="escalate",
        failure_family="env_error",
    ),
    "VERIFIED": _base_entry(
        category="success",
        outcome_class="success.verified",
        tool_class="verifier",
        policy_action="continue",
    ),
    "UNKNOWN": _base_entry(
        category="unknown",
        outcome_class="unknown",
        tool_class="unknown",
        policy_action="escalate",
    ),
}


_POLICY_OVERRIDES: list[dict[str, object]] = [
    {
        "name": "behavioural_tests_do_not_inline_retry",
        "workflow_stage": "quality_gate",
        "failure_family": "test_behavioural",
        "policy_action": "verifier_reject",
    },
    {
        "name": "readonly_or_review_intents_do_not_replan",
        "task_intent": "review_only",
        "category": "diagnostic_blocked",
        "policy_action": "escalate",
    },
    {
        "name": "codeintel_runtime_misses_escalate",
        "repo_profile_id": "RP-CODEINTEL",
        "outcome_class": "workspace.invalid_path",
        "policy_action": "escalate",
    },
]


def _fallback_failure_family(*, code: str, outcome_class: str, tool_class: str, message: str) -> str:
    normalized = _normalize_upper(code)
    lowered_message = _normalize_lower(message)
    lowered_outcome = _normalize_lower(outcome_class)
    lowered_tool = _normalize_lower(tool_class)
    if normalized in {"BASE_DIVERGED", "SYMBOL_DIVERGED"}:
        return normalized
    if normalized in {"GROUNDING_MISMATCH", "PYTHON_SYMBOL_NOT_FOUND", "TARGET_LOGIC_NOT_CONFIRMED"}:
        return "grounding_failed"
    if normalized.startswith("TEST_") and "BEHAVIOUR" in normalized:
        return "test_behavioural"
    if normalized in {"COMPILE_FAIL", "PY_COMPILE_FAILED"}:
        return "compile_error"
    if normalized in {"RUFF_FAILED", "LINT_FAILED"}:
        return "lint_error"
    if normalized in {"CANDIDATE_SYNTAX_INVALID", "PYTHON_SYNTAX_INVALID", "INVALID_NEW_CODE_SYNTAX"}:
        return "parse_error"
    if normalized == "SEARCH_REPLACE_MISMATCH":
        return "mutation_semantic"
    if normalized in {"CONTENT_SYNTAX_ERROR", "INVALID_SIGNATURE_SYNTAX"}:
        return "parse_error"
    if "invalid_patch" in lowered_outcome or "syntax" in lowered_message:
        return "parse_error"
    if lowered_tool == "runtime" or lowered_outcome.startswith("runtime.") or lowered_outcome.startswith("workspace."):
        return "env_error"
    if normalized.startswith("TEST_") or lowered_tool == "verifier":
        return "test_local"
    return ""


def _fallback_retry_budget_key(failure_family: str) -> str:
    budget_key_map = {
        "parse_error": "parse_failure",
        "compile_error": "parse_failure",
        "mutation_semantic": "parse_failure",
        "import_error": "lint_failure",
        "lint_error": "lint_failure",
        "mutation_missing": "mutation_missing",
        "type_trivial": "lint_failure",
        "test_local": "test_local_failure",
        "test_behavioural": "test_behavioural_failure",
        "env_error": "test_local_failure",
    }
    return str(budget_key_map.get(_normalize_text(failure_family)) or "")


def _fallback_next_best_actions(failure_family: str) -> list[str]:
    action_map = {
        "parse_error": [
            "repair syntax and indentation boundaries in the changed files",
            "re-run the targeted validation commands after syntax repair",
        ],
        "compile_error": [
            "repair syntax and indentation boundaries in the changed files",
            "re-run the targeted validation commands after syntax repair",
        ],
        "import_error": [
            "repair the import or symbol-resolution issue with the smallest safe edit",
            "rerun lint or the targeted validation commands after the import fix",
        ],
        "lint_error": [
            "apply the smallest lint-safe edit needed to satisfy the failing rule",
            "rerun lint or the targeted validation commands after the minimal fix",
        ],
        "mutation_missing": [
            "produce at least one substantive code change before asking for review",
            "rerun the declared targeted validation commands after the required mutation lands",
        ],
        "type_trivial": [
            "repair the trivial typing issue without broad rewrites",
            "rerun lint or the targeted validation commands after the typing fix",
        ],
        "test_local": [
            "close the missing local validation slice for the changed behavior",
            "rerun the declared targeted tests and attach the output as evidence",
        ],
        "test_behavioural": [
            "stabilize the requested behavior before asking for review",
            "escalate with the failing behavioural evidence if the contract remains ambiguous",
        ],
        "grounding_failed": [
            "refresh semantic anchors for the target files before mutating again",
            "reconfirm the owning symbol or route boundary against current file content",
        ],
        "env_error": [
            "separate environment failure from implementation logic before retrying",
            "rerun the blocked command without rewriting working implementation paths",
        ],
    }
    selected = action_map.get(_normalize_text(failure_family)) or []
    return [str(item) for item in selected if _normalize_text(item)]


def _context_payload(
    *,
    workflow_stage: str,
    task_intent: str | None = None,
    tenant_id: str | None = None,
    repo_profile_id: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    category: str | None = None,
    outcome_class: str | None = None,
    tool_class: str | None = None,
    failure_family: str | None = None,
) -> dict[str, str]:
    return {
        "workflow_stage": _normalize_lower(workflow_stage),
        "task_intent": _normalize_lower(task_intent),
        "tenant_id": _normalize_lower(tenant_id),
        "repo_profile_id": _normalize_upper(repo_profile_id),
        "provider": _normalize_lower(provider),
        "model": _normalize_lower(model),
        "category": _normalize_lower(category),
        "outcome_class": _normalize_lower(outcome_class),
        "tool_class": _normalize_lower(tool_class),
        "failure_family": _normalize_text(failure_family),
    }


def _override_matches(rule: dict[str, object], context: dict[str, str]) -> bool:
    for key in (
        "workflow_stage",
        "task_intent",
        "tenant_id",
        "repo_profile_id",
        "provider",
        "model",
        "category",
        "outcome_class",
        "tool_class",
        "failure_family",
    ):
        expected = rule.get(key)
        if expected is None:
            continue
        expected_text = _normalize_lower(expected) if key != "repo_profile_id" else _normalize_upper(expected)
        if context.get(key, "") != expected_text:
            return False
    return True


def outcome_registry_entry(code: str) -> dict[str, Any]:
    normalized = _normalize_upper(code) or "UNKNOWN"
    entry = dict(_OUTCOME_RULES.get(normalized, _OUTCOME_RULES["UNKNOWN"]))
    return {
        "code": normalized,
        "category": str(entry["category"]),
        "outcome_class": str(entry["outcome_class"]),
        "tool_class": str(entry["tool_class"]),
        "policy_action": str(entry["policy_action"]),
        "failure_family": str(entry.get("failure_family") or ""),
        "retry_budget_key": str(entry.get("retry_budget_key") or ""),
        "retry_rules": list(entry.get("retry_rules") or []),
        "next_best_actions": list(entry.get("next_best_actions") or []),
        "route_target": _default_route_target(
            code=normalized,
            category=str(entry["category"]),
            outcome_class=str(entry["outcome_class"]),
            tool_class=str(entry["tool_class"]),
            policy_action=str(entry["policy_action"]),
            failure_family=str(entry.get("failure_family") or ""),
        ),
    }


def resolve_recovery_policy(
    *,
    code: str,
    workflow_stage: str,
    task_intent: str | None = None,
    tenant_id: str | None = None,
    repo_profile_id: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    category: str | None = None,
    outcome_class: str | None = None,
    tool_class: str | None = None,
    failure_family: str | None = None,
    quality_message: str | None = None,
) -> dict[str, Any]:
    normalized_code = _normalize_upper(code)
    entry = outcome_registry_entry(code)
    resolved_outcome_class = _normalize_text(outcome_class) or str(entry.get("outcome_class") or "")
    resolved_tool_class = _normalize_text(tool_class) or str(entry.get("tool_class") or "")
    resolved_category = _normalize_text(category) or str(entry.get("category") or "")
    explicit_failure_family = _normalize_text(failure_family)
    entry_failure_family = _normalize_text(entry.get("failure_family") or "")
    if explicit_failure_family:
        resolved_failure_family = explicit_failure_family
    elif normalized_code in _OUTCOME_RULES:
        resolved_failure_family = entry_failure_family
    else:
        resolved_failure_family = _fallback_failure_family(
            code=code,
            outcome_class=resolved_outcome_class,
            tool_class=resolved_tool_class,
            message=str(quality_message or ""),
        )
    entry_retry_budget_key = _normalize_text(entry.get("retry_budget_key") or "")
    if entry_retry_budget_key:
        resolved_retry_budget_key = entry_retry_budget_key
    elif explicit_failure_family:
        resolved_retry_budget_key = _fallback_retry_budget_key(resolved_failure_family)
    elif normalized_code in _OUTCOME_RULES:
        resolved_retry_budget_key = ""
    else:
        resolved_retry_budget_key = _fallback_retry_budget_key(resolved_failure_family)
    context = _context_payload(
        workflow_stage=workflow_stage,
        task_intent=task_intent,
        tenant_id=tenant_id,
        repo_profile_id=repo_profile_id,
        provider=provider,
        model=model,
        category=resolved_category,
        outcome_class=resolved_outcome_class,
        tool_class=resolved_tool_class,
        failure_family=resolved_failure_family,
    )
    matched_override = "default"
    policy_action = str(entry.get("policy_action") or "")
    for override in _POLICY_OVERRIDES:
        if _override_matches(override, context):
            policy_action = _normalize_text(override.get("policy_action")) or policy_action
            matched_override = _normalize_text(override.get("name")) or "override"
            break
    return {
        **entry,
        "category": resolved_category or str(entry.get("category") or ""),
        "outcome_class": resolved_outcome_class or str(entry.get("outcome_class") or ""),
        "tool_class": resolved_tool_class or str(entry.get("tool_class") or ""),
        "policy_action": policy_action,
        "failure_family": resolved_failure_family,
        "retry_budget_key": resolved_retry_budget_key,
        "route_target": _default_route_target(
            code=normalized_code,
            category=resolved_category or str(entry.get("category") or ""),
            outcome_class=resolved_outcome_class or str(entry.get("outcome_class") or ""),
            tool_class=resolved_tool_class or str(entry.get("tool_class") or ""),
            policy_action=policy_action,
            failure_family=resolved_failure_family,
        ),
        "workflow_stage": _normalize_text(workflow_stage) or "unknown",
        "task_intent": _normalize_text(task_intent) or None,
        "tenant_id": _normalize_text(tenant_id) or None,
        "repo_profile_id": _normalize_text(repo_profile_id) or None,
        "provider": _normalize_text(provider) or None,
        "model": _normalize_text(model) or None,
        "matched_policy": matched_override,
        "retry_rules": list(entry.get("retry_rules") or []),
        "next_best_actions": list(entry.get("next_best_actions") or [])
        or _fallback_next_best_actions(resolved_failure_family),
    }


def build_policy_recovery_plan(
    *,
    code: str,
    targets: list[str] | None = None,
    fallback_targets: list[str] | None = None,
    summary: str | None = None,
) -> list[dict[str, Any]] | None:
    entry = outcome_registry_entry(code)
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
    if normalized == "PLANNER_PACKET_REQUIRED":
        return [
            {
                "step": 1,
                "action": "reread_symbol_context",
                "summary": "Re-read the owning symbol and bounded target so the planner packet can be rebuilt with one explicit target.",
                "targets": target_list,
            },
            {
                "step": 2,
                "action": "inject_semantic_anchors",
                "summary": "Restore the missing target symbol and anchor inputs required to shape the bounded planner packet.",
                "targets": target_list,
            },
            {
                "step": 3,
                "action": "retry_narrow_plan",
                "summary": "Retry only after the planner packet is rebuilt with one target file, one target symbol, and one validation command.",
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
    if normalized in {"WIRING_INCOMPLETE_ROUTER_THREADING", "NOOP_CODE_CHANGE"}:
        first_action = (
            "thread_missing_wiring"
            if normalized == "WIRING_INCOMPLETE_ROUTER_THREADING"
            else "perform_required_mutation"
        )
        first_summary = (
            "Expose the missing router parameter and thread it through the service callsite."
            if normalized == "WIRING_INCOMPLETE_ROUTER_THREADING"
            else "Produce at least one substantive code change in an allowed target file for this mutation-required task."
        )
        return [
            {
                "step": 1,
                "action": first_action,
                "summary": first_summary,
                "targets": target_list,
            },
            {
                "step": 2,
                "action": "rerun_targeted_validation",
                "summary": "Run the targeted validation commands after the corrective change is complete.",
                "targets": target_list,
            },
        ]
    if normalized in {"FEATURE_SLICE_INCOMPLETE", "CANDIDATE_VALIDATION_MISSING"}:
        return [
            {
                "step": 1,
                "action": "complete_declared_validation_slice",
                "summary": summary or "Resolve the missing verifier coverage for the declared slice.",
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


__all__ = ["build_policy_recovery_plan", "outcome_registry_entry", "resolve_recovery_policy"]
