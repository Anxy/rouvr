"""AIX-REAL-EXECUTION-005 Workstream K: one concise, truthful, actionable outcome
report, built entirely from already-computed truth (`real_execute` payload,
`verification_engine` checks) -- never a second success computation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from backend.aix_core.framework.verification import (
    VerificationCheck,
    build_checks_from_completion_gates,
    build_checks_from_read_verification,
)


@dataclass(frozen=True)
class FinalExecutionReport:
    objective_id: str
    cycle_id: str
    trace_id: str
    objective_text: str
    final_status: str
    requested_operation: str
    target_resource: str | None
    plan_summary: str
    approvals: tuple[str, ...]
    real_operations: int
    target_files_read: int
    target_files_modified: int
    commands_executed: int
    tests_executed: int
    verification_checks: tuple[VerificationCheck, ...]
    administrative_artifacts: tuple[str, ...]
    warnings: tuple[str, ...] = ()
    blocking_stage: str | None = None
    reason_code: str | None = None
    recommended_next_action: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "objective_id": self.objective_id,
            "cycle_id": self.cycle_id,
            "trace_id": self.trace_id,
            "objective": self.objective_text,
            "final_status": self.final_status,
            "requested_operation": self.requested_operation,
            "target_resource": self.target_resource,
            "plan_summary": self.plan_summary,
            "approvals": list(self.approvals),
            "real_operations": {
                "real_operations": self.real_operations,
                "target_files_read": self.target_files_read,
                "target_files_modified": self.target_files_modified,
                "commands_executed": self.commands_executed,
                "tests_executed": self.tests_executed,
            },
            "verification_results": [c.to_dict() for c in self.verification_checks],
            "administrative_artifacts": list(self.administrative_artifacts),
            "warnings": list(self.warnings),
            "blocking_stage": self.blocking_stage,
            "reason_code": self.reason_code,
            "recommended_next_action": self.recommended_next_action,
        }

    def to_text(self) -> str:
        """Human-readable rendering matching this sprint's own worked examples --
        never calls an administrative artifact a user-work artifact."""
        lines = [f"Objective:\n{self.objective_text}", "", f"Final status:\n{self.final_status}"]
        if self.blocking_stage:
            lines += ["", f"Blocked at:\n{self.blocking_stage}"]
        if self.reason_code:
            lines += ["", f"Reason:\n{self.reason_code}"]
        lines += [
            "",
            "Real operations:",
            f"- Files read: {self.target_files_read}",
            f"- Files modified: {self.target_files_modified}",
            f"- Commands executed: {self.commands_executed}",
            f"- Tests executed: {self.tests_executed}",
        ]
        if self.target_resource:
            lines += ["", f"Target accessed:\n{self.target_resource}"]
        if self.verification_checks:
            lines += ["", "Verification:"]
            for check in self.verification_checks:
                mark = "✓" if check.status == "PASSED" else "✗"
                lines.append(f"{mark} {check.check_type}")
        if self.administrative_artifacts:
            lines += ["", "Administrative artifacts:"] + [f"- {a}" for a in self.administrative_artifacts]
        if self.warnings:
            lines += ["", "Warnings:"] + [f"- {w}" for w in self.warnings]
        if self.recommended_next_action:
            lines += ["", f"Next action:\n{self.recommended_next_action}"]
        return "\n".join(lines)


_NEXT_ACTION_BY_REASON: dict[str, str] = {
    "CAPABILITY_NOT_REGISTERED": "Register the missing tool capability, or restate the objective using a registered capability.",
    "WORKSPACE_NOT_BOUND": "Register the target repository's root, then retry the objective.",
    "INTENT_UNCLEAR": "Answer the pending clarification question, then resume the objective.",
    "EXPLORATION_REQUIRED": "Allow exploration to run (retry), or provide the missing target/symbol explicitly.",
    "NO_APPLICABLE_SOURCE_FILE": "Name the target file or symbol explicitly and retry.",
    "SYMBOL_NOT_FOUND": "Confirm the symbol name and its containing file, then retry.",
    "SYMBOL_AMBIGUOUS": "Disambiguate the symbol by naming its containing file.",
    "COMPLETION_GATES_FAILED": "Inspect the failed gate(s) and address the underlying content or test failure.",
    "NO_FILES_CHANGED": "Review the proposed patch and apply it explicitly if it is correct.",
}


def _next_action(reason_code: str | None) -> str | None:
    if not reason_code:
        return None
    return _NEXT_ACTION_BY_REASON.get(reason_code, "Review the reason code and retry once it is addressed.")


def build_final_report(
    *,
    objective_id: str,
    cycle_id: str,
    trace_id: str,
    objective_text: str,
    repository_id: str,
    real_execute: dict[str, Any] | None,
    approvals: tuple[str, ...] = (),
) -> FinalExecutionReport:
    """Build the report from the same `real_execute` payload
    `durable_api_backend.py` already persists on `_CycleRecord` -- no second
    success computation, only a truthful re-shaping of what was already decided."""
    payload = real_execute or {}
    objective_status = str(payload.get("objective_status") or "INCOMPLETE")
    reason_code = payload.get("reason_code")

    checks = build_checks_from_read_verification(payload.get("read_verification"), evidence_ref=cycle_id)
    if not checks:
        checks = build_checks_from_completion_gates(payload.get("completion_gates"), evidence_ref=cycle_id)

    artifacts = payload.get("artifacts")
    administrative_artifacts = tuple(sorted(artifacts.keys())) if isinstance(artifacts, dict) else ()

    target_resource = None
    read_file = payload.get("read_file")
    if isinstance(read_file, dict):
        target_resource = read_file.get("resource")
    elif payload.get("changed_source_files"):
        changed = payload["changed_source_files"]
        if isinstance(changed, list) and changed:
            target_resource = changed[0].get("path") if isinstance(changed[0], dict) else None

    blocking_stage = None
    if objective_status in {"BLOCKED", "FAILED"}:
        blocking_stage = str(payload.get("execution_status") or "execution")

    warnings: tuple[str, ...] = ()
    if objective_status == "COMPLETED_WITH_WARNINGS":
        warnings = ("Objective completed with non-blocking warnings.",)

    return FinalExecutionReport(
        objective_id=objective_id,
        cycle_id=cycle_id,
        trace_id=trace_id,
        objective_text=objective_text,
        final_status=objective_status,
        requested_operation=repository_id,
        target_resource=target_resource,
        plan_summary=str(payload.get("detail") or ""),
        approvals=approvals,
        real_operations=int(payload.get("real_operations") or 0),
        target_files_read=int(payload.get("target_files_read") or 0),
        target_files_modified=int(payload.get("target_files_modified") or 0),
        commands_executed=0,
        tests_executed=0,
        verification_checks=checks,
        administrative_artifacts=administrative_artifacts,
        warnings=warnings,
        blocking_stage=blocking_stage,
        reason_code=str(reason_code) if reason_code else None,
        recommended_next_action=_next_action(str(reason_code) if reason_code else None),
    )


__all__ = ["FinalExecutionReport", "build_final_report"]
