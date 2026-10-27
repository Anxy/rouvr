from __future__ import annotations

from typing import Any


def evaluate_mvp7_gate(
    *,
    scorecard: dict[str, Any],
    scope: str = "mvp7_plus",
) -> dict[str, Any]:
    gate_passed = bool(scorecard.get("gate_passed", False))
    real_tasks_count = int(scorecard.get("real_tasks_count", 0) or 0)
    ratio = float(scorecard.get("verified_without_rework_ratio", 0.0) or 0.0)
    minimum = int(scorecard.get("minimum_real_tasks", 10) or 10)
    threshold = float(scorecard.get("verified_without_rework_threshold", 0.9) or 0.9)

    if gate_passed:
        return {
            "scope": scope,
            "blocked": False,
            "reason_code": "DOGFOOD_GATE_PASSED",
            "requirements": {
                "minimum_real_tasks": minimum,
                "verified_without_rework_threshold": threshold,
            },
            "current": {
                "real_tasks_count": real_tasks_count,
                "verified_without_rework_ratio": ratio,
            },
        }
    reasons: list[str] = []
    if real_tasks_count < minimum:
        reasons.append(f"REAL_TASK_THRESHOLD_UNMET current={real_tasks_count} required={minimum}")
    if ratio < threshold:
        reasons.append(f"VERIFIED_RATIO_THRESHOLD_UNMET current={ratio} required={threshold}")
    return {
        "scope": scope,
        "blocked": True,
        "reason_code": "DOGFOOD_GATE_BLOCKED",
        "reasons": reasons or ["DOGFOOD_GATE_BLOCKED_UNKNOWN"],
        "requirements": {
            "minimum_real_tasks": minimum,
            "verified_without_rework_threshold": threshold,
        },
        "current": {
            "real_tasks_count": real_tasks_count,
            "verified_without_rework_ratio": ratio,
        },
    }
