"""
Feedback-driven router v2 scoring helpers.

This module intentionally uses lightweight heuristics over existing decision logs so
v2 can run without schema migrations.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RouterV2Weights:
    success: float = 0.35
    json_ok: float = 0.2
    tool_ok: float = 0.2
    retries_penalty: float = 0.1
    latency_penalty: float = 0.1
    cost_penalty: float = 0.05


def _to_float(value: Any) -> float:
    try:
        if value is None:
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _to_int(value: Any) -> int:
    try:
        if value is None:
            return 0
        return int(value)
    except (TypeError, ValueError):
        return 0


def _norm(value: Any) -> str:
    return str(value or "").strip().lower()


def _is_success(row: dict[str, Any]) -> bool:
    return _norm(row.get("decision_status")) in {"selected", "escalated"}


def _is_json_ok(row: dict[str, Any]) -> bool:
    reason = _norm(row.get("decision_reason"))
    return "json" not in reason and "schema" not in reason and "parse" not in reason


def _is_tool_ok(row: dict[str, Any]) -> bool:
    reason = _norm(row.get("decision_reason"))
    return "tool" not in reason and "action_map" not in reason


def _decision_phase(row: dict[str, Any]) -> str:
    payload = row.get("decision_payload_json") if isinstance(row.get("decision_payload_json"), dict) else {}
    return _norm(payload.get("phase") or "")


def build_model_leaderboard(
    *,
    decision_rows: list[dict[str, Any]],
    task_type: str | None = None,
    risk_level: str | None = None,
    phase: str | None = None,
    weights: RouterV2Weights | None = None,
) -> list[dict[str, Any]]:
    score_weights = weights or RouterV2Weights()
    task_type_norm = _norm(task_type)
    risk_level_norm = _norm(risk_level)
    phase_norm = _norm(phase)
    scoped: list[dict[str, Any]] = []
    for raw in decision_rows:
        row = raw if isinstance(raw, dict) else {}
        if task_type_norm and _norm(row.get("task_type")) != task_type_norm:
            continue
        if risk_level_norm and _norm(row.get("risk_level")) != risk_level_norm:
            continue
        if phase_norm and _decision_phase(row) != phase_norm:
            continue
        if not row.get("selected_model_id"):
            continue
        scoped.append(row)

    if not scoped:
        return []

    max_latency = max(_to_int(row.get("latency_ms")) for row in scoped) or 1
    max_cost = max(_to_float(row.get("estimated_cost")) for row in scoped) or 1.0
    model_rollup: dict[str, dict[str, Any]] = {}

    for row in scoped:
        provider = str(row.get("selected_provider") or "unknown")
        model_id = str(row.get("selected_model_id") or "unknown")
        model_name = str(row.get("selected_model_name") or model_id)
        model_key = f"{provider}:{model_id}"
        if model_key not in model_rollup:
            model_rollup[model_key] = {
                "provider": provider,
                "model_id": model_id,
                "model_name": model_name,
                "samples": 0,
                "success_count": 0,
                "json_ok_count": 0,
                "tool_ok_count": 0,
                "retry_total": 0,
                "latency_total": 0,
                "cost_total": 0.0,
            }
        slot = model_rollup[model_key]
        slot["samples"] += 1
        slot["success_count"] += 1 if _is_success(row) else 0
        slot["json_ok_count"] += 1 if _is_json_ok(row) else 0
        slot["tool_ok_count"] += 1 if _is_tool_ok(row) else 0
        slot["retry_total"] += _to_int(row.get("retry_count"))
        slot["latency_total"] += _to_int(row.get("latency_ms"))
        slot["cost_total"] += _to_float(row.get("estimated_cost"))

    ranked: list[dict[str, Any]] = []
    for _, slot in model_rollup.items():
        samples = max(1, int(slot["samples"]))
        success_rate = slot["success_count"] / samples
        json_ok_rate = slot["json_ok_count"] / samples
        tool_ok_rate = slot["tool_ok_count"] / samples
        retries_avg = slot["retry_total"] / samples
        latency_avg = slot["latency_total"] / samples
        cost_avg = slot["cost_total"] / samples
        retries_penalty = min(1.0, retries_avg / 3.0)
        latency_penalty = min(1.0, latency_avg / max_latency)
        cost_penalty = min(1.0, cost_avg / max_cost)
        score = (
            (score_weights.success * success_rate)
            + (score_weights.json_ok * json_ok_rate)
            + (score_weights.tool_ok * tool_ok_rate)
            - (score_weights.retries_penalty * retries_penalty)
            - (score_weights.latency_penalty * latency_penalty)
            - (score_weights.cost_penalty * cost_penalty)
        )
        tool_error_rate = 1.0 - tool_ok_rate
        trend = "hold"
        if samples >= 8 and success_rate >= 0.85 and tool_error_rate <= 0.05:
            trend = "promote"
        elif samples >= 5 and (success_rate < 0.4 or tool_error_rate >= 0.25):
            trend = "demote"
        ranked.append(
            {
                "provider": slot["provider"],
                "model_id": slot["model_id"],
                "model_name": slot["model_name"],
                "samples": samples,
                "success_rate": round(success_rate, 4),
                "json_ok_rate": round(json_ok_rate, 4),
                "tool_ok_rate": round(tool_ok_rate, 4),
                "tool_error_rate": round(tool_error_rate, 4),
                "avg_retries": round(retries_avg, 4),
                "avg_latency_ms": round(latency_avg, 2),
                "avg_cost": round(cost_avg, 8),
                "score": round(score, 6),
                "trend": trend,
            }
        )

    return sorted(
        ranked,
        key=lambda item: (
            -float(item.get("score", 0.0)),
            -int(item.get("samples", 0)),
            str(item.get("provider", "")),
            str(item.get("model_id", "")),
        ),
    )
