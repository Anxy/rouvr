from __future__ import annotations

import json
from pathlib import Path

from backend.services.aix_mvp_certification_harness_service import (
    detect_executor_workhub_api_violation,
    evaluate_benchmark_certification,
)


def _valid_benchmark_payload() -> dict:
    return {
        "benchmark_executed": True,
        "benchmark_task_count_required": 10,
        "benchmark_task_count_run": 10,
        "completed_count": 10,
        "exact_output_pass_count": 10,
        "direct_openrouter_call": False,
        "wall_time": {"average_wall_time_minutes": 1.2},
        "provider_reported_cost": {"measured": True, "average_cost_usd": 0.12},
        "human_review_time": {"measured": True, "estimated": False, "average_minutes": 3.4},
        "manual_baseline_comparison": {
            "estimated": False,
            "manual_average_minutes": 12.0,
            "aix_average_human_effort_minutes": 3.4,
        },
        "task_results": [
            {
                "benchmark_task_id": f"BM-{index:03d}",
                "status": "passed",
                "wall_time_ms": 1000,
                "cost_usd": 0.01,
            }
            for index in range(1, 11)
        ],
        "blocked_or_skipped_counted_as_success": False,
        "quality_failure_hidden": False,
    }


def test_benchmark_certification_passes_only_with_measured_threshold_evidence() -> None:
    result = evaluate_benchmark_certification(_valid_benchmark_payload())

    assert result["status"] == "passed"
    assert result["reason_code"] == "BENCHMARK_CERTIFICATION_PASSED"
    assert result["failures"] == []


def test_benchmark_certification_blocks_historical_estimated_manual_baseline_artifact() -> None:
    artifact_path = (
        Path(__file__).parents[3]
        / "docs"
        / "tasks"
        / "executor-brokerage"
        / "artifacts"
        / "AIX-FINAL-010M-R2"
        / "measured_benchmark_result_r2.json"
    )
    result = evaluate_benchmark_certification(json.loads(artifact_path.read_text(encoding="utf-8")))

    assert result["status"] == "blocked"
    codes = {item["code"] for item in result["failures"]}
    assert "BENCHMARK_HUMAN_REVIEW_TIME_UNMEASURED" in codes
    assert "BENCHMARK_MANUAL_BASELINE_ESTIMATED" in codes


def test_benchmark_certification_rejects_hidden_or_incomplete_success_claims() -> None:
    payload = _valid_benchmark_payload()
    payload["completed_count"] = 9
    payload["blocked_or_skipped_counted_as_success"] = True
    payload["quality_failure_hidden"] = True
    payload["task_results"][9]["status"] = "blocked"

    result = evaluate_benchmark_certification(payload)

    assert result["status"] == "blocked"
    codes = [item["code"] for item in result["failures"]]
    assert "BENCHMARK_COMPLETION_INCOMPLETE" in codes
    assert "BENCHMARK_QUALITY_THRESHOLD_FAILED" in codes
    assert "BENCHMARK_FALSE_SUCCESS_BLOCKED_OR_SKIPPED" in codes
    assert "BENCHMARK_QUALITY_FAILURE_HIDDEN" in codes


def test_executor_workhub_api_guard_allows_plain_evidence_references() -> None:
    result = detect_executor_workhub_api_violation(
        {
            "status": "completed",
            "run_result": {"evidence_refs": ["workhub-read-contract://task-1/context"]},
            "commands": [{"command_text": "python -m pytest backend/tests/test_example.py -q", "ok": True}],
        }
    )

    assert result["passed"] is True
    assert result["matches"] == []


def test_executor_workhub_api_guard_detects_direct_network_call() -> None:
    result = detect_executor_workhub_api_violation(
        {
            "commands": [
                {
                    "ok": True,
                    "command_text": "python - <<'PY'\nimport requests\nrequests.post('/api/workhub/projects/p/tasks/t/closeout')\nPY",
                }
            ]
        }
    )

    assert result["passed"] is False
    assert result["reason_code"] == "EXECUTOR_WORKHUB_API_CALL_FORBIDDEN"
    assert result["matches"][0]["path"].endswith(".command_text")
