from __future__ import annotations

from backend.services.router_v2_scoring_service import build_model_leaderboard


def test_build_model_leaderboard_ranks_higher_quality_model_first() -> None:
    rows = [
        {
            "task_type": "code_change",
            "risk_level": "medium",
            "decision_status": "selected",
            "decision_reason": None,
            "retry_count": 0,
            "latency_ms": 200,
            "estimated_cost": 0.01,
            "selected_provider": "onlineai",
            "selected_model_id": "model-good",
            "selected_model_name": "Good",
            "decision_payload_json": {"phase": "executor"},
        },
        {
            "task_type": "code_change",
            "risk_level": "medium",
            "decision_status": "selected",
            "decision_reason": None,
            "retry_count": 0,
            "latency_ms": 220,
            "estimated_cost": 0.01,
            "selected_provider": "onlineai",
            "selected_model_id": "model-good",
            "selected_model_name": "Good",
            "decision_payload_json": {"phase": "executor"},
        },
        {
            "task_type": "code_change",
            "risk_level": "medium",
            "decision_status": "blocked",
            "decision_reason": "tool_failure",
            "retry_count": 3,
            "latency_ms": 900,
            "estimated_cost": 0.09,
            "selected_provider": "onlineai",
            "selected_model_id": "model-bad",
            "selected_model_name": "Bad",
            "decision_payload_json": {"phase": "executor"},
        },
    ]
    leaderboard = build_model_leaderboard(
        decision_rows=rows,
        task_type="code_change",
        risk_level="medium",
        phase="executor",
    )
    assert leaderboard
    assert leaderboard[0]["model_id"] == "model-good"
    assert leaderboard[0]["score"] > leaderboard[-1]["score"]


def test_build_model_leaderboard_marks_promote_and_demote_trends() -> None:
    good_rows = [
        {
            "task_type": "analysis",
            "risk_level": "low",
            "decision_status": "selected",
            "decision_reason": None,
            "retry_count": 0,
            "latency_ms": 100,
            "estimated_cost": 0.005,
            "selected_provider": "slavx",
            "selected_model_id": "model-promote",
            "selected_model_name": "Promote",
            "decision_payload_json": {"phase": "planner"},
        }
        for _ in range(8)
    ]
    bad_rows = [
        {
            "task_type": "analysis",
            "risk_level": "low",
            "decision_status": "blocked",
            "decision_reason": "tool_error",
            "retry_count": 2,
            "latency_ms": 800,
            "estimated_cost": 0.05,
            "selected_provider": "slavx",
            "selected_model_id": "model-demote",
            "selected_model_name": "Demote",
            "decision_payload_json": {"phase": "planner"},
        }
        for _ in range(5)
    ]
    leaderboard = build_model_leaderboard(
        decision_rows=good_rows + bad_rows,
        task_type="analysis",
        risk_level="low",
        phase="planner",
    )
    trend_by_model = {row["model_id"]: row["trend"] for row in leaderboard}
    assert trend_by_model["model-promote"] == "promote"
    assert trend_by_model["model-demote"] == "demote"
