from __future__ import annotations

from typing import Any


def _decision(task: dict[str, Any]) -> str:
    metadata = task.get("metadata_json") if isinstance(task.get("metadata_json"), dict) else {}
    value = str(metadata.get("verifier_decision") or "").strip().lower()
    if value:
        return value
    return str(task.get("status") or "").strip().lower()


def _rework_rounds(task: dict[str, Any]) -> int:
    metadata = task.get("metadata_json") if isinstance(task.get("metadata_json"), dict) else {}
    for key in ("rework_rounds", "loopback_cycles", "needs_fix_cycles"):
        value = metadata.get(key)
        if isinstance(value, int):
            return max(0, value)
    return 0


def build_dogfood_scorecard(
    *,
    project_id: str,
    tasks: list[dict[str, Any]],
    min_real_tasks: int = 10,
    verified_without_rework_threshold: float = 0.9,
) -> dict[str, Any]:
    real_tasks = [task for task in tasks if int(task.get("task_number", 0) or 0) > 0]
    verified = [task for task in real_tasks if _decision(task) in {"verified", "done"}]
    verified_without_rework = [task for task in verified if _rework_rounds(task) == 0]
    ratio = (len(verified_without_rework) / len(real_tasks)) if real_tasks else 0.0
    gate = len(real_tasks) >= min_real_tasks and ratio >= verified_without_rework_threshold

    audit = [
        {
            "task_number": int(task.get("task_number") or 0),
            "status": str(task.get("status") or ""),
            "decision": _decision(task),
            "rework_rounds": _rework_rounds(task),
        }
        for task in sorted(real_tasks, key=lambda row: int(row.get("task_number", 0) or 0))
    ]
    return {
        "project_id": project_id,
        "minimum_real_tasks": min_real_tasks,
        "verified_without_rework_threshold": verified_without_rework_threshold,
        "real_tasks_count": len(real_tasks),
        "verified_count": len(verified),
        "verified_without_rework_count": len(verified_without_rework),
        "verified_without_rework_ratio": round(ratio, 4),
        "gate_passed": gate,
        "audit": audit,
    }
