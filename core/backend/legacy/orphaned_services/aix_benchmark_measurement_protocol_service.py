from __future__ import annotations

from datetime import datetime
from typing import Any


MEASURED_BENCHMARK_REQUIRED_TASK_COUNT = 10
REQUIRED_EVIDENCE_LEVELS = {"local", "vps_staging", "live_vps"}
NON_PRODUCTION_ENVIRONMENTS = {"local", "vps_staging", "staging", "non_production", "pilot"}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "passed"}
    return bool(value)


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _add_failure(failures: list[dict[str, Any]], *, code: str, field: str, message: str) -> None:
    failures.append({"code": code, "field": field, "message": message})


def evaluate_measured_benchmark_protocol(packet: dict[str, Any]) -> dict[str, Any]:
    """Validate that a future benchmark run is measurable before it can feed certification."""

    failures: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    if not _bool(packet.get("preregistered")):
        _add_failure(
            failures,
            code="BENCHMARK_PROTOCOL_NOT_PREREGISTERED",
            field="preregistered",
            message="Measured benchmark runs must be preregistered before execution.",
        )

    environment = str(packet.get("environment") or "").strip()
    if environment not in NON_PRODUCTION_ENVIRONMENTS:
        _add_failure(
            failures,
            code="BENCHMARK_ENVIRONMENT_UNSUPPORTED",
            field="environment",
            message="Measured benchmark protocol is restricted to non-production environments.",
        )

    source_identity = _as_dict(packet.get("source_identity"))
    for field in ("local_commit", "vps_commit", "image_id", "compose_file", "env_file"):
        if not str(source_identity.get(field) or "").strip():
            _add_failure(
                failures,
                code="BENCHMARK_SOURCE_IDENTITY_MISSING",
                field=f"source_identity.{field}",
                message="Benchmark evidence must identify the exact source and image under test.",
            )

    evidence_levels = {
        str(item).strip()
        for item in _as_list(packet.get("evidence_levels"))
        if str(item).strip()
    }
    missing_levels = REQUIRED_EVIDENCE_LEVELS - evidence_levels
    if missing_levels:
        _add_failure(
            failures,
            code="BENCHMARK_EVIDENCE_LEVELS_INCOMPLETE",
            field="evidence_levels",
            message="Benchmark evidence must label local, VPS staging, and live VPS evidence levels.",
        )

    tasks = _as_list(packet.get("benchmark_tasks"))
    task_ids = [str(_as_dict(item).get("benchmark_task_id") or "").strip() for item in tasks]
    if len(tasks) != MEASURED_BENCHMARK_REQUIRED_TASK_COUNT:
        _add_failure(
            failures,
            code="BENCHMARK_TASK_COUNT_INVALID",
            field="benchmark_tasks",
            message="The measured benchmark protocol requires exactly ten tasks.",
        )
    if any(not task_id for task_id in task_ids):
        _add_failure(
            failures,
            code="BENCHMARK_TASK_ID_MISSING",
            field="benchmark_tasks[].benchmark_task_id",
            message="Every benchmark task requires a stable benchmark_task_id.",
        )
    if len(set(task_ids)) != len(task_ids):
        _add_failure(
            failures,
            code="BENCHMARK_TASK_ID_DUPLICATE",
            field="benchmark_tasks[].benchmark_task_id",
            message="Benchmark task ids must be unique.",
        )
    for index, task in enumerate(tasks):
        item = _as_dict(task)
        if not str(item.get("task_kind") or "").strip():
            _add_failure(
                failures,
                code="BENCHMARK_TASK_KIND_MISSING",
                field=f"benchmark_tasks[{index}].task_kind",
                message="Every benchmark task requires a task kind.",
            )
        if not _as_list(item.get("expected_outputs")):
            _add_failure(
                failures,
                code="BENCHMARK_EXPECTED_OUTPUTS_MISSING",
                field=f"benchmark_tasks[{index}].expected_outputs",
                message="Every benchmark task requires expected-output checks.",
            )

    timing = _as_dict(packet.get("wall_time_measurement"))
    if not _bool(timing.get("measured")) or _bool(timing.get("estimated")):
        _add_failure(
            failures,
            code="BENCHMARK_WALL_TIME_NOT_MEASURED",
            field="wall_time_measurement",
            message="Wall time must be measured and cannot be estimated.",
        )
    for field in ("started_at", "ended_at"):
        if _parse_datetime(timing.get(field)) is None:
            _add_failure(
                failures,
                code="BENCHMARK_WALL_TIME_TIMESTAMP_INVALID",
                field=f"wall_time_measurement.{field}",
                message="Wall time timestamps must be ISO-8601 datetimes.",
            )
    start = _parse_datetime(timing.get("started_at"))
    end = _parse_datetime(timing.get("ended_at"))
    if start and end and end <= start:
        _add_failure(
            failures,
            code="BENCHMARK_WALL_TIME_ORDER_INVALID",
            field="wall_time_measurement",
            message="Benchmark end time must be after start time.",
        )

    provider_costs = _as_list(packet.get("provider_cost_records"))
    if len(provider_costs) != MEASURED_BENCHMARK_REQUIRED_TASK_COUNT:
        _add_failure(
            failures,
            code="BENCHMARK_PROVIDER_COST_RECORD_COUNT_INVALID",
            field="provider_cost_records",
            message="Every benchmark task requires a provider cost record.",
        )
    for index, record in enumerate(provider_costs):
        item = _as_dict(record)
        if _number(item.get("cost_usd")) is None:
            _add_failure(
                failures,
                code="BENCHMARK_PROVIDER_COST_MISSING",
                field=f"provider_cost_records[{index}].cost_usd",
                message="Provider-reported cost must be recorded as a number.",
            )
        if _bool(item.get("estimated")):
            _add_failure(
                failures,
                code="BENCHMARK_PROVIDER_COST_ESTIMATED",
                field=f"provider_cost_records[{index}].estimated",
                message="Estimated provider cost cannot certify a measured benchmark.",
            )

    review_events = _as_list(packet.get("human_review_events"))
    if len(review_events) != MEASURED_BENCHMARK_REQUIRED_TASK_COUNT:
        _add_failure(
            failures,
            code="BENCHMARK_HUMAN_REVIEW_EVENT_COUNT_INVALID",
            field="human_review_events",
            message="Every benchmark task requires a measured human review event.",
        )
    for index, event in enumerate(review_events):
        item = _as_dict(event)
        if not str(item.get("human_actor_id") or "").strip():
            _add_failure(
                failures,
                code="BENCHMARK_HUMAN_REVIEW_ACTOR_MISSING",
                field=f"human_review_events[{index}].human_actor_id",
                message="Human review events must identify the human reviewer.",
            )
        if _bool(item.get("estimated")) or _number(item.get("duration_seconds")) is None:
            _add_failure(
                failures,
                code="BENCHMARK_HUMAN_REVIEW_NOT_MEASURED",
                field=f"human_review_events[{index}]",
                message="Human review duration must be measured and cannot be estimated.",
            )

    baseline_trials = _as_list(packet.get("manual_baseline_trials"))
    if len(baseline_trials) < MEASURED_BENCHMARK_REQUIRED_TASK_COUNT:
        _add_failure(
            failures,
            code="BENCHMARK_MANUAL_BASELINE_TRIALS_INCOMPLETE",
            field="manual_baseline_trials",
            message="Manual baseline must include at least one measured trial for each benchmark task.",
        )
    baseline_task_ids = {
        str(_as_dict(trial).get("benchmark_task_id") or "").strip()
        for trial in baseline_trials
        if str(_as_dict(trial).get("benchmark_task_id") or "").strip()
    }
    if set(task_ids) and set(task_ids) - baseline_task_ids:
        _add_failure(
            failures,
            code="BENCHMARK_MANUAL_BASELINE_TASK_COVERAGE_MISSING",
            field="manual_baseline_trials[].benchmark_task_id",
            message="Manual baseline trials must cover every benchmark task id.",
        )
    for index, trial in enumerate(baseline_trials):
        item = _as_dict(trial)
        if _bool(item.get("estimated")) or _number(item.get("duration_seconds")) is None:
            _add_failure(
                failures,
                code="BENCHMARK_MANUAL_BASELINE_NOT_MEASURED",
                field=f"manual_baseline_trials[{index}]",
                message="Manual baseline duration must be measured and cannot be estimated.",
            )
        if not str(item.get("human_actor_id") or "").strip():
            _add_failure(
                failures,
                code="BENCHMARK_MANUAL_BASELINE_ACTOR_MISSING",
                field=f"manual_baseline_trials[{index}].human_actor_id",
                message="Manual baseline trials must identify the human operator.",
            )

    forbidden_flags = _as_dict(packet.get("forbidden_flags"))
    for field in (
        "direct_openrouter_call",
        "blocked_or_skipped_counted_as_success",
        "quality_failure_hidden",
        "workhub_lifecycle_mutation",
        "auto_accept",
    ):
        if _bool(forbidden_flags.get(field)):
            _add_failure(
                failures,
                code="BENCHMARK_FORBIDDEN_FLAG_PRESENT",
                field=f"forbidden_flags.{field}",
                message="Measured benchmark protocol contains a forbidden certification shortcut.",
            )

    if _as_list(packet.get("certification_caveats")):
        warnings.append({"code": "BENCHMARK_PROTOCOL_CAVEATS_RECORDED", "field": "certification_caveats"})

    passed = not failures
    return {
        "schema": "aix.benchmark.measurement_protocol_evaluation.v1",
        "status": "ready_for_measured_run" if passed else "blocked",
        "passed": passed,
        "reason_code": "BENCHMARK_MEASUREMENT_PROTOCOL_READY" if passed else failures[0]["code"],
        "required_task_count": MEASURED_BENCHMARK_REQUIRED_TASK_COUNT,
        "required_evidence_levels": sorted(REQUIRED_EVIDENCE_LEVELS),
        "failures": failures,
        "warnings": warnings,
    }
