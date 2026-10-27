from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any


BENCHMARK_REQUIRED_TASK_COUNT = 10
BENCHMARK_MAX_AVERAGE_WALL_TIME_MINUTES = 15.0
BENCHMARK_MAX_AVERAGE_COST_USD = 0.50
BENCHMARK_MAX_AVERAGE_HUMAN_REVIEW_MINUTES = 5.0

EXECUTOR_WORKHUB_API_REASON_CODE = "EXECUTOR_WORKHUB_API_CALL_FORBIDDEN"

_WORKHUB_API_PATTERN = re.compile(r"(?i)(?:/api/workhub/|https?://[^\s'\"]*workhub[^\s'\"]*)")
_NETWORK_TOOL_PATTERN = re.compile(r"(?i)\b(curl|wget|httpx|requests|fetch|axios|Invoke-WebRequest|iwr)\b")


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "passed"}
    return bool(value)


def _add_failure(failures: list[dict[str, Any]], *, code: str, message: str, field: str) -> None:
    failures.append({"code": code, "message": message, "field": field})


def build_benchmark_certification_payload_from_measured_protocol(
    *,
    protocol_packet: dict[str, Any],
    provider_run_result: dict[str, Any],
    protocol_evaluation: dict[str, Any],
) -> dict[str, Any]:
    """Convert measured DEV-019 protocol evidence into the benchmark certification contract."""

    benchmark_tasks = _as_list(protocol_packet.get("benchmark_tasks"))
    provider_tasks = _as_list(provider_run_result.get("tasks"))
    task_count = len(benchmark_tasks) or BENCHMARK_REQUIRED_TASK_COUNT
    provider_cost_records = _as_list(protocol_packet.get("provider_cost_records"))
    review_events = _as_list(protocol_packet.get("human_review_events"))
    baseline_trials = _as_list(protocol_packet.get("manual_baseline_trials"))
    wall_time = _as_dict(protocol_packet.get("wall_time_measurement"))

    task_results: list[dict[str, Any]] = []
    for item in provider_tasks:
        task = _as_dict(item)
        passed = _bool(task.get("exact_output_match")) and str(task.get("provider_status") or "") == "completed"
        task_results.append(
            {
                "benchmark_task_id": task.get("benchmark_task_id"),
                "status": "passed" if passed else "failed",
                "provider_status": task.get("provider_status"),
                "exact_output_match": _bool(task.get("exact_output_match")),
                "job_id": task.get("job_id"),
            }
        )

    total_wall_time_ms = _number(wall_time.get("total_wall_time_ms"))
    average_wall_time_minutes = None
    if total_wall_time_ms is not None and task_count:
        average_wall_time_minutes = total_wall_time_ms / task_count / 60_000

    costs = [_number(_as_dict(record).get("cost_usd")) for record in provider_cost_records]
    measured_costs = [cost for cost in costs if cost is not None]
    average_cost = sum(measured_costs) / len(measured_costs) if measured_costs else None

    review_durations = [_number(_as_dict(event).get("duration_seconds")) for event in review_events]
    measured_reviews = [duration for duration in review_durations if duration is not None]
    average_review_minutes = (sum(measured_reviews) / len(measured_reviews) / 60) if measured_reviews else None

    baseline_durations = [_number(_as_dict(trial).get("duration_seconds")) for trial in baseline_trials]
    measured_baselines = [duration for duration in baseline_durations if duration is not None]
    average_baseline_minutes = (
        sum(measured_baselines) / len(measured_baselines) / 60 if measured_baselines else None
    )

    protocol_ready = _bool(protocol_evaluation.get("passed"))
    caveats = [
        str(caveat).strip()
        for caveat in _as_list(protocol_packet.get("certification_caveats"))
        if str(caveat).strip()
    ]
    caveats.extend(
        str(warning.get("code") or "").strip()
        for warning in _as_list(protocol_evaluation.get("warnings"))
        if isinstance(warning, dict) and str(warning.get("code") or "").strip()
    )

    return {
        "benchmark_executed": protocol_ready and str(provider_run_result.get("status") or "") == "passed",
        "benchmark_task_count_required": BENCHMARK_REQUIRED_TASK_COUNT,
        "benchmark_task_count_run": len(provider_tasks),
        "completed_count": sum(1 for item in provider_tasks if str(_as_dict(item).get("provider_status") or "") == "completed"),
        "exact_output_pass_count": sum(1 for item in task_results if item["status"] == "passed"),
        "direct_openrouter_call": _bool(protocol_packet.get("direct_provider_call")),
        "wall_time": {"average_wall_time_minutes": average_wall_time_minutes},
        "provider_reported_cost": {
            "measured": len(measured_costs) == BENCHMARK_REQUIRED_TASK_COUNT
            and not any(_bool(_as_dict(record).get("estimated")) for record in provider_cost_records),
            "average_cost_usd": average_cost,
        },
        "human_review_time": {
            "measured": len(measured_reviews) == BENCHMARK_REQUIRED_TASK_COUNT
            and not any(_bool(_as_dict(event).get("estimated")) for event in review_events),
            "estimated": any(_bool(_as_dict(event).get("estimated")) for event in review_events),
            "average_minutes": average_review_minutes,
        },
        "manual_baseline_comparison": {
            "estimated": any(_bool(_as_dict(trial).get("estimated")) for trial in baseline_trials),
            "manual_average_minutes": average_baseline_minutes,
            "aix_average_human_effort_minutes": average_review_minutes,
        },
        "task_results": task_results,
        "blocked_or_skipped_counted_as_success": False,
        "quality_failure_hidden": False,
        "certification_caveats": caveats,
    }


def evaluate_benchmark_certification(payload: dict[str, Any]) -> dict[str, Any]:
    """Evaluate benchmark evidence without accepting estimates as certification proof."""

    failures: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    if not _bool(payload.get("benchmark_executed")):
        _add_failure(
            failures,
            code="BENCHMARK_NOT_EXECUTED",
            message="benchmark evidence must come from an executed current-source run",
            field="benchmark_executed",
        )

    required_count = int(_number(payload.get("benchmark_task_count_required")) or BENCHMARK_REQUIRED_TASK_COUNT)
    run_count = int(_number(payload.get("benchmark_task_count_run")) or 0)
    completed_count = int(_number(payload.get("completed_count")) or 0)
    task_results = _as_list(payload.get("task_results"))
    if required_count != BENCHMARK_REQUIRED_TASK_COUNT:
        _add_failure(
            failures,
            code="BENCHMARK_TASK_COUNT_CONTRACT_CHANGED",
            message="MVP certification requires the fixed 10-task benchmark contract",
            field="benchmark_task_count_required",
        )
    if run_count != BENCHMARK_REQUIRED_TASK_COUNT or len(task_results) != BENCHMARK_REQUIRED_TASK_COUNT:
        _add_failure(
            failures,
            code="BENCHMARK_TASK_COUNT_INCOMPLETE",
            message="benchmark evidence must include exactly 10 executed task results",
            field="task_results",
        )
    if completed_count != BENCHMARK_REQUIRED_TASK_COUNT:
        _add_failure(
            failures,
            code="BENCHMARK_COMPLETION_INCOMPLETE",
            message="all 10 benchmark tasks must complete",
            field="completed_count",
        )

    passed_tasks = [
        item for item in task_results if isinstance(item, dict) and str(item.get("status") or "").lower() == "passed"
    ]
    exact_pass_count = int(_number(payload.get("exact_output_pass_count")) or len(passed_tasks))
    if exact_pass_count != BENCHMARK_REQUIRED_TASK_COUNT or len(passed_tasks) != BENCHMARK_REQUIRED_TASK_COUNT:
        _add_failure(
            failures,
            code="BENCHMARK_QUALITY_THRESHOLD_FAILED",
            message="all 10 benchmark tasks must pass exact-output quality checks",
            field="exact_output_pass_count",
        )

    if _bool(payload.get("blocked_or_skipped_counted_as_success")):
        _add_failure(
            failures,
            code="BENCHMARK_FALSE_SUCCESS_BLOCKED_OR_SKIPPED",
            message="blocked or skipped tasks cannot be counted as successful benchmark tasks",
            field="blocked_or_skipped_counted_as_success",
        )
    if _bool(payload.get("quality_failure_hidden")):
        _add_failure(
            failures,
            code="BENCHMARK_QUALITY_FAILURE_HIDDEN",
            message="quality failures must not be hidden by aggregate status fields",
            field="quality_failure_hidden",
        )
    if _bool(payload.get("direct_openrouter_call")):
        _add_failure(
            failures,
            code="BENCHMARK_DIRECT_PROVIDER_CALL_FORBIDDEN",
            message="benchmark must use the Hyeve integration route, not direct provider calls",
            field="direct_openrouter_call",
        )

    wall_time = _as_dict(payload.get("wall_time"))
    average_wall_time = _number(wall_time.get("average_wall_time_minutes"))
    if average_wall_time is None or average_wall_time > BENCHMARK_MAX_AVERAGE_WALL_TIME_MINUTES:
        _add_failure(
            failures,
            code="BENCHMARK_WALL_TIME_THRESHOLD_FAILED",
            message="average wall time must be measured and <= 15 minutes",
            field="wall_time.average_wall_time_minutes",
        )

    cost = _as_dict(payload.get("provider_reported_cost"))
    average_cost = _number(cost.get("average_cost_usd"))
    if not _bool(cost.get("measured")) or average_cost is None:
        _add_failure(
            failures,
            code="BENCHMARK_COST_UNMEASURED",
            message="provider-reported cost must be measured for the benchmark",
            field="provider_reported_cost.measured",
        )
    elif average_cost > BENCHMARK_MAX_AVERAGE_COST_USD:
        _add_failure(
            failures,
            code="BENCHMARK_COST_THRESHOLD_FAILED",
            message="average provider cost must be <= 0.50 USD",
            field="provider_reported_cost.average_cost_usd",
        )

    human_review = _as_dict(payload.get("human_review_time"))
    human_review_minutes = _number(human_review.get("average_minutes"))
    if not _bool(human_review.get("measured")) or _bool(human_review.get("estimated")):
        _add_failure(
            failures,
            code="BENCHMARK_HUMAN_REVIEW_TIME_UNMEASURED",
            message="human review time must be measured; estimates are not certification proof",
            field="human_review_time.measured",
        )
    elif human_review_minutes is None or human_review_minutes > BENCHMARK_MAX_AVERAGE_HUMAN_REVIEW_MINUTES:
        _add_failure(
            failures,
            code="BENCHMARK_HUMAN_REVIEW_THRESHOLD_FAILED",
            message="average human review time must be <= 5 minutes",
            field="human_review_time.average_minutes",
        )

    manual_baseline = _as_dict(payload.get("manual_baseline_comparison"))
    manual_average = _number(manual_baseline.get("manual_average_minutes"))
    aix_effort = _number(manual_baseline.get("aix_average_human_effort_minutes"))
    if not manual_baseline:
        _add_failure(
            failures,
            code="BENCHMARK_MANUAL_BASELINE_MISSING",
            message="manual baseline comparison is required",
            field="manual_baseline_comparison",
        )
    elif _bool(manual_baseline.get("estimated")):
        _add_failure(
            failures,
            code="BENCHMARK_MANUAL_BASELINE_ESTIMATED",
            message="manual baseline comparison must be independently measured, not estimated",
            field="manual_baseline_comparison.estimated",
        )
    elif manual_average is None or aix_effort is None or aix_effort >= manual_average:
        _add_failure(
            failures,
            code="BENCHMARK_MANUAL_BASELINE_NOT_BEATEN",
            message="AIX measured human effort must beat the measured manual baseline",
            field="manual_baseline_comparison",
        )

    caveats = [str(item) for item in _as_list(payload.get("certification_caveats")) if str(item).strip()]
    if caveats:
        warnings.append({"code": "BENCHMARK_CAVEATS_PRESENT", "message": "; ".join(caveats)})

    return {
        "schema": "aix.mvp_certification.benchmark_evaluation.v1",
        "status": "passed" if not failures else "blocked",
        "passed": not failures,
        "reason_code": "BENCHMARK_CERTIFICATION_PASSED" if not failures else failures[0]["code"],
        "failures": failures,
        "warnings": warnings,
        "thresholds": {
            "task_count": BENCHMARK_REQUIRED_TASK_COUNT,
            "average_wall_time_minutes_lte": BENCHMARK_MAX_AVERAGE_WALL_TIME_MINUTES,
            "average_cost_usd_lte": BENCHMARK_MAX_AVERAGE_COST_USD,
            "average_human_review_minutes_lte": BENCHMARK_MAX_AVERAGE_HUMAN_REVIEW_MINUTES,
        },
    }


def _iter_text(value: Any, *, path: str = "$") -> Iterable[tuple[str, str]]:
    if isinstance(value, dict):
        for key, child in value.items():
            yield from _iter_text(child, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _iter_text(child, path=f"{path}[{index}]")
    elif isinstance(value, (str, int, float, bool)) and value is not None:
        yield path, str(value)


def detect_executor_workhub_api_violation(execution: dict[str, Any]) -> dict[str, Any]:
    matches: list[dict[str, str]] = []
    for path, text in _iter_text(execution):
        if not _WORKHUB_API_PATTERN.search(text):
            continue
        if _NETWORK_TOOL_PATTERN.search(text) or path.endswith(
            (
                ".command",
                ".command_text",
                ".stdout",
                ".stderr",
                ".transcript",
                ".tool",
                ".url",
                ".endpoint",
            )
        ):
            matches.append({"path": path, "excerpt": text[:240]})
    return {
        "schema": "aix.executor_boundary.workhub_api_guard.v1",
        "passed": not matches,
        "reason_code": None if not matches else EXECUTOR_WORKHUB_API_REASON_CODE,
        "matches": matches[:8],
    }
