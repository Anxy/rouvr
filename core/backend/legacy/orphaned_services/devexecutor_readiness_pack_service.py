from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def build_internal_rollout_readiness_pack(
    *,
    project_id: str,
    scorecard: dict[str, Any],
    gate_result: dict[str, Any],
    telemetry_refs: dict[str, str],
    failure_paths: list[dict[str, Any]],
    runbook_refs: list[str],
) -> dict[str, Any]:
    return {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "project_id": project_id,
        "scorecard": scorecard,
        "gate_result": gate_result,
        "telemetry_refs": telemetry_refs,
        "failure_paths": failure_paths,
        "runbook_refs": runbook_refs,
        "checklist": {
            "dogfood_gate_passed": not bool(gate_result.get("blocked", True)),
            "failure_path_coverage": len(failure_paths) > 0,
            "telemetry_links_present": all(bool(v) for v in telemetry_refs.values()),
            "runbook_refs_present": len(runbook_refs) > 0,
        },
    }


def render_readiness_pack_markdown(pack: dict[str, Any]) -> str:
    checklist = _as_dict(pack.get("checklist"))
    gate = _as_dict(pack.get("gate_result"))
    scorecard = _as_dict(pack.get("scorecard"))
    lines = [
        "# CL-E Internal Rollout Readiness Pack",
        "",
        f"- Generated: {pack.get('generated_at_utc')}",
        f"- Project: {pack.get('project_id')}",
        "",
        "## Dogfood Gate",
        f"- Blocked: {gate.get('blocked')}",
        f"- Reason: {gate.get('reason_code')}",
        f"- Real tasks: {scorecard.get('real_tasks_count')}",
        f"- Verified-without-rework ratio: {scorecard.get('verified_without_rework_ratio')}",
        "",
        "## Checklist",
        f"- Dogfood gate passed: {checklist.get('dogfood_gate_passed')}",
        f"- Failure path coverage: {checklist.get('failure_path_coverage')}",
        f"- Telemetry links present: {checklist.get('telemetry_links_present')}",
        f"- Runbook refs present: {checklist.get('runbook_refs_present')}",
    ]
    return "\n".join(lines) + "\n"
