from __future__ import annotations

import shlex
from typing import Any

from backend.legacy.aix_orchestrator_v2.discipline import classify_failure_semantics
from backend.aix_orchestrator_v2.models import VerificationResult
from backend.services.aix_mvp_certification_harness_service import detect_executor_workhub_api_violation
from backend.services.verifier_decision_service import build_verifier_decision


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _infer_command_failure_classification(command_failures: list[dict[str, Any]]) -> tuple[str, str, str, str] | None:
    for item in command_failures:
        command_text = str(item.get("command_text") or item.get("command") or "").strip().lower()
        stderr_text = str(item.get("stderr") or "").strip().lower()
        stdout_text = str(item.get("stdout") or "").strip().lower()
        combined = f"{stderr_text}\n{stdout_text}"
        if "ruff" in command_text and (
            "no module named ruff" in combined
            or "command not found" in combined
            or "not found" in combined
            or "executable file not found" in combined
        ):
            return (
                "INFRA_FAIL",
                "LINT_TOOL_UNAVAILABLE",
                "bounded lint tool is unavailable in the runtime environment",
                "restore runtime lint tooling before retrying the run",
            )
    return None


def _normalized_command_text(value: Any) -> str:
    if isinstance(value, list):
        return " ".join(str(item).strip() for item in value if str(item).strip()).strip()
    return str(value or "").strip()


def _validation_trace_failure(
    *,
    plan: dict[str, Any],
    execution: dict[str, Any],
) -> tuple[str, str, str, str] | None:
    declared_validation = str(plan.get("declared_validation") or "").strip()
    if not declared_validation:
        return None
    run_result = _as_dict(execution.get("run_result"))
    run_status = str(run_result.get("status") or execution.get("status") or "").strip().lower()
    changed_files = list(execution.get("changed_files") or [])
    diff_refs = list(execution.get("diff_refs") or [])
    if run_status not in {"completed", "done"} or (not changed_files and not diff_refs):
        return None
    declared_tokens = shlex.split(declared_validation, posix=True)
    matching_entry: dict[str, Any] | None = None
    for item in list(execution.get("commands") or []):
        if not isinstance(item, dict):
            continue
        command_text = _normalized_command_text(item.get("command_text") or item.get("command"))
        if not command_text:
            continue
        if command_text == declared_validation:
            matching_entry = item
            break
        command = item.get("command")
        if isinstance(command, list) and [str(part) for part in command] == declared_tokens:
            matching_entry = item
            break
    if matching_entry is None:
        return (
            "INFRA_FAIL",
            "VALIDATION_TRACE_COMMAND_MISSING",
            "declared validation trace is missing the executed command record",
            "restore declared validation trace emission before verification/writeback",
        )
    missing_fields: list[str] = []
    if not _normalized_command_text(matching_entry.get("command_text") or matching_entry.get("command")):
        missing_fields.append("command")
    exit_code = matching_entry.get("exit_code")
    if exit_code is None:
        exit_code = matching_entry.get("returncode")
    if exit_code is None:
        missing_fields.append("exit_code")
    if not str(matching_entry.get("parsed_result") or "").strip():
        missing_fields.append("parsed_result")
    classification_mapping = _as_dict(matching_entry.get("classification_mapping"))
    if not classification_mapping:
        missing_fields.append("classification_mapping")
    elif not (
        str(classification_mapping.get("classification") or "").strip()
        and str(classification_mapping.get("reason_code") or "").strip()
    ):
        missing_fields.append("classification_mapping")
    if not missing_fields:
        return None
    return (
        "INFRA_FAIL",
        "VALIDATION_TRACE_INCOMPLETE",
        f"declared validation trace is incomplete: missing {', '.join(missing_fields)}",
        "restore complete validation trace fields before verification/writeback",
    )


def verify_run(
    *,
    plan: dict[str, Any],
    execution: dict[str, Any],
    failure: dict[str, Any] | None = None,
) -> dict[str, Any]:
    run_result = _as_dict(execution.get("run_result"))
    tests = _as_dict(execution.get("tests"))
    commands = list(execution.get("commands") or [])
    command_failures = [item for item in commands if isinstance(item, dict) and not bool(item.get("ok", True))]
    changed_files = [str(item).strip() for item in list(execution.get("changed_files") or []) if str(item).strip()]
    diff_refs = [str(item).strip() for item in list(execution.get("diff_refs") or []) if str(item).strip()]
    run_status = str(run_result.get("status") or execution.get("status") or "").strip().lower()
    classification = "CODE_FAIL"
    phase = "verification"
    reason_code = "EXECUTION_FAILED"
    summary = "execution failed"
    retry_hint: str | None = "inspect execution evidence and change executable logic instead of retrying blindly"
    reasons: list[dict[str, Any]] = []
    inferred_command_failure = _infer_command_failure_classification(command_failures)
    validation_trace_failure = _validation_trace_failure(plan=plan, execution=execution)
    workhub_api_violation = detect_executor_workhub_api_violation(execution)
    if str(plan.get("status") or "").strip() == "blocked":
        phase = "planner"
        summary = str(plan.get("blocked_reason") or "planner blocked").strip()
        reason_code = summary.split(":", 1)[-1].strip().upper().replace(" ", "_")
        classification = classify_failure_semantics(
            phase=phase,
            reason_code=reason_code,
            message=summary,
            execution_attempted=False,
        )
        retry_hint = "correct task shaping or policy inputs before invoking planner again"
    elif inferred_command_failure:
        classification, reason_code, summary, retry_hint = inferred_command_failure
        phase = "execution"
    elif run_status in {"completed", "done"} and not command_failures and not changed_files and not diff_refs:
        classification = "CODE_FAIL"
        summary = "execution completed without producing a code change"
        reason_code = "NO_CHANGE"
        retry_hint = "apply a behavior-bearing mutation; no-op or style-only runs are invalid"
    elif validation_trace_failure:
        classification, reason_code, summary, retry_hint = validation_trace_failure
        phase = "execution"
    elif not bool(workhub_api_violation.get("passed")):
        classification = "POLICY_FAIL"
        phase = "execution"
        reason_code = "EXECUTOR_WORKHUB_API_CALL_FORBIDDEN"
        summary = "executor evidence contains a direct WorkHub API call"
        retry_hint = "route WorkHub reads/writeback through the trusted orchestrator boundary"
    elif run_status in {"completed", "done"} and not command_failures:
        classification = "success"
        summary = "declared validation completed successfully"
        reason_code = "VALIDATION_PASSED"
        retry_hint = None
    elif failure:
        failure_code = str(failure.get("code") or "").strip().upper()
        phase = str(failure.get("phase") or execution.get("status") or "execution").strip().lower() or "execution"
        reason_code = failure_code or "EXECUTION_FAILED"
        classification = classify_failure_semantics(
            phase=phase,
            reason_code=reason_code,
            message=str(failure.get("message") or "execution failed"),
            execution_attempted=bool(execution),
        )
        summary = str(failure.get("message") or "execution failed").strip()
        retry_hint = "follow the classified failure route; do not treat infra or policy failures as code failures"
    elif run_status in {"running"}:
        classification = "INFRA_FAIL"
        summary = "execution did not reach a terminal state during this pass"
        reason_code = "EXECUTION_NOT_TERMINAL"
        retry_hint = "inspect runtime orchestration and worker state before retrying"
    elif tests.get("detected") and command_failures:
        classification = "VERIFICATION_FAIL"
        summary = "declared validation command failed"
        reason_code = "VALIDATION_FAILED"
        retry_hint = "fix the touched code or tests until the declared validation passes"
    else:
        classification = "INFRA_FAIL"
        summary = "execution completed without enough clean evidence to validate"
        reason_code = "INFRA_BLOCKED_BEFORE_VALIDATION"
        retry_hint = "restore execution evidence or runtime health before retrying validation"
    if command_failures:
        for item in command_failures[:4]:
            reasons.append(
                {
                    "kind": "acceptance_gap",
                    "message": str(item.get("stderr") or item.get("stdout") or "validation failed").strip()[:400],
                    "acceptance_id": "validation",
                }
            )
    elif classification != "success":
        reasons.append(
            {
                "kind": "risk" if classification == "INFRA_FAIL" else "acceptance_gap",
                "message": summary,
                "acceptance_id": "validation",
            }
        )
    verifier_decision = build_verifier_decision(
        decision="verified" if classification == "success" else "needs_fix",
        summary=summary,
        reasons=reasons,
        acceptance_mapping=[
            {"acceptance_id": f"ac-{index}", "summary": item, "met": classification == "success"}
            for index, item in enumerate(list(plan.get("planner_packet", {}).get("acceptance_criteria") or []), start=1)
        ],
        test_refs=[str(item) for item in list(execution.get("diff_refs") or []) if str(item).strip()],
        evidence_refs=[str(item) for item in list(execution.get("diff_refs") or []) if str(item).strip()],
        reason_code=reason_code,
        metadata={
            "require_acceptance_mapping": False,
            "classification": classification,
            "allow_unlocated_reasons": classification != "success",
            "executor_workhub_api_guard": workhub_api_violation,
        },
    )
    return VerificationResult(
        classification=classification,
        phase=phase,
        reason_code=reason_code,
        summary=summary,
        retry_hint=retry_hint,
        checks=[
            {
                "name": "declared_validation",
                "command": str(plan.get("declared_validation") or "").strip() or None,
                "passed": classification == "success",
                "execution_status": "execution_not_attempted"
                if not execution
                else "execution_attempted_but_failed"
                if classification in {"CODE_FAIL", "INFRA_FAIL"} and run_status not in {"completed", "done"}
                else "validation_failed"
                if classification == "VERIFICATION_FAIL"
                else "validated",
            }
        ],
        reasons=reasons,
        verifier_decision=verifier_decision,
    ).model_dump(mode="python")
