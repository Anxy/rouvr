"""P30: Agent Feedback Loop — collector, summarizer, and builder for FailureReportV1.

Every agent that encounters a failure produces a ``FailureReportV1`` (defined in
``contracts.py``). This module provides:

- ``AgentFeedbackCollector``: per-objective append-only store of all failure reports
- ``summarize_failure_chain``: condenses a chain into a root-cause narrative
- ``build_failure_report``: convenience builder with validation
"""

from __future__ import annotations

from typing import Any

from backend.aix_core.runtime.objective.contracts import FailureReportV1


class AgentFeedbackCollector:
    """Per-objective collector of failure reports from all agents.

    Append-only. Thread-safe for single-writer (the DurableApiBackend).
    """

    def __init__(self) -> None:
        self._reports: dict[str, list[FailureReportV1]] = {}

    def record(self, report: FailureReportV1) -> None:
        self._reports.setdefault(report.objective_id, []).append(report)

    def get_reports(self, objective_id: str) -> tuple[FailureReportV1, ...]:
        return tuple(self._reports.get(objective_id, ()))

    def get_blocking_reports(self, objective_id: str) -> tuple[FailureReportV1, ...]:
        return tuple(r for r in self._reports.get(objective_id, ()) if r.severity == "BLOCKING")

    def latest_report(self, objective_id: str) -> FailureReportV1 | None:
        reports = self._reports.get(objective_id)
        return reports[-1] if reports else None

    def latest_by_agent(self, objective_id: str, agent_role: str) -> FailureReportV1 | None:
        for r in reversed(self._reports.get(objective_id, ())):
            if r.agent_role == agent_role:
                return r
        return None

    def has_failure(self, objective_id: str, failure_code: str) -> bool:
        return any(
            r.failure_code == failure_code
            for r in self._reports.get(objective_id, ())
        )

    def failure_chain(self, objective_id: str) -> list[dict[str, Any]]:
        """Condensed chain for API responses / diagnosis."""
        return [
            {
                "agent": r.agent_role,
                "component": r.agent_component,
                "code": r.failure_code,
                "detail": r.failure_detail[:200],
                "severity": r.severity,
                "recovery": r.suggested_recovery[:200],
                "upstream": r.upstream_dependency,
                "timestamp": r.timestamp,
            }
            for r in self._reports.get(objective_id, ())
        ]

    def to_dict(self, objective_id: str) -> dict[str, Any]:
        reports = self._reports.get(objective_id, ())
        return {
            "objective_id": objective_id,
            "total_reports": len(reports),
            "blocking_count": sum(1 for r in reports if r.severity == "BLOCKING"),
            "agents_involved": sorted(set(r.agent_role for r in reports)),
            "failure_codes": [r.failure_code for r in reports],
            "chain": self.failure_chain(objective_id),
        }

    def clear(self, objective_id: str) -> None:
        self._reports.pop(objective_id, None)


def summarize_failure_chain(reports: tuple[FailureReportV1, ...]) -> str:
    """Produce a human-readable root-cause narrative from a failure chain."""
    if not reports:
        return "No failures recorded."

    blocking = [r for r in reports if r.severity == "BLOCKING"]
    if not blocking:
        return f"{len(reports)} informational/degraded issues logged, none blocking."

    lines = [f"Pipeline blocked after {len(blocking)} failure(s):"]
    for i, r in enumerate(blocking, 1):
        lines.append(
            f"  {i}. [{r.agent_role}:{r.agent_component}] {r.failure_code}"
            f" — {r.failure_detail[:150]}"
        )
        if r.suggested_recovery:
            lines.append(f"     Recovery: {r.suggested_recovery[:150]}")
        if r.upstream_dependency:
            lines.append(f"     Caused by: {r.upstream_dependency}")

    root = blocking[0]
    lines.append(f"\nRoot cause: {root.agent_role} → {root.failure_code}")
    if root.evidence_missing:
        lines.append(f"Missing evidence: {', '.join(root.evidence_missing[:5])}")

    return "\n".join(lines)


def build_failure_report(
    *,
    agent_role: str,
    agent_component: str,
    objective_id: str,
    failure_code: str,
    phase: str = "EXECUTING",
    failure_detail: str = "",
    cycle_id: str = "",
    step_id: str = "",
    attempt_number: int = 0,
    severity: str = "BLOCKING",
    attempted_action: str = "",
    evidence_available: tuple[str, ...] = (),
    evidence_missing: tuple[str, ...] = (),
    suggested_recovery: str = "",
    upstream_dependency: str = "",
    retryable: bool = False,
    retry_after_condition: str = "",
    duration_ms: int | None = None,
) -> FailureReportV1:
    """Convenience builder — validates all fields upfront."""
    return FailureReportV1(
        agent_id=f"{agent_role}:{agent_component}",
        agent_role=agent_role,
        agent_component=agent_component,
        objective_id=objective_id,
        cycle_id=cycle_id,
        step_id=step_id,
        attempt_number=attempt_number,
        phase=phase,
        severity=severity,
        attempted_action=attempted_action or f"{agent_component} invocation",
        failure_code=failure_code,
        failure_detail=failure_detail,
        evidence_available=evidence_available,
        evidence_missing=evidence_missing,
        suggested_recovery=suggested_recovery,
        upstream_dependency=upstream_dependency,
        retryable=retryable,
        retry_after_condition=retry_after_condition,
        duration_ms=duration_ms,
    )


__all__ = [
    "AgentFeedbackCollector",
    "build_failure_report",
    "summarize_failure_chain",
]
