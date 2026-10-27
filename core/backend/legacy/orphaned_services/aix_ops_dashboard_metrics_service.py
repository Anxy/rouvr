from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any


REQUIRED_OPS_DASHBOARD_METRICS = (
    "tasks_received",
    "tasks_admitted",
    "tasks_rejected",
    "tasks_blocked",
    "tasks_executed",
    "success_rate",
    "failure_rate",
    "average_runtime",
    "average_cost",
    "token_usage",
    "validation_failures",
    "StopTheLine_events",
    "review_pending_queue",
    "evidence_completeness",
    "replay_success_failure",
    "tenant_level_task_counts",
    "executor_performance",
    "benchmark_gap_visibility",
    "provider_key_routing_status",
)

TERMINAL_EXECUTION_STATUSES = {"completed", "success", "failed", "failure"}
SUCCESS_STATUSES = {"completed", "success", "passed"}
FAILURE_STATUSES = {"failed", "failure"}
BLOCKED_STATUSES = {"blocked", "skipped"}


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
        return value.strip().lower() in {"1", "true", "yes", "y", "passed", "success"}
    return bool(value)


def _status(record: dict[str, Any]) -> str:
    return str(record.get("status") or record.get("execution_status") or "").strip().lower()


def _metric(value: Any, *, data_status: str = "measured", extra: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = {"value": value, "data_status": data_status}
    if extra:
        payload.update(extra)
    return payload


def _has_evidence(record: dict[str, Any]) -> bool:
    if _bool(record.get("evidence_complete")):
        return True
    evidence = _as_dict(record.get("evidence"))
    if evidence.get("evidence_ref") or evidence.get("bundle_id") or evidence.get("evidence_bundle_v2"):
        return True
    if record.get("evidence_ref") or record.get("evidence_bundle_v2"):
        return True
    return False


def _replay_state(record: dict[str, Any]) -> str | None:
    replay = _as_dict(record.get("replay") or record.get("clean_replay"))
    if not replay:
        bundle = _as_dict(record.get("evidence_bundle_v2"))
        replay = _as_dict(bundle.get("clean_replay"))
    if not replay:
        return None
    if _bool(replay.get("passed")) or str(replay.get("status") or "").lower() in {"passed", "success"}:
        return "success"
    return "failure"


def build_ops_dashboard_metrics_read_model(
    *,
    records: list[dict[str, Any]],
    benchmark_evaluation: dict[str, Any] | None = None,
    provider_key_routing_proof: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a read-only dashboard metrics projection from current-source evidence records."""

    failures: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    status_counts: Counter[str] = Counter()
    tenant_counts: Counter[str] = Counter()
    executor_totals: defaultdict[str, Counter[str]] = defaultdict(Counter)
    runtimes: list[float] = []
    costs: list[float] = []
    missing_runtime_count = 0
    missing_cost_count = 0
    token_usage = {"input": 0, "output": 0, "total": 0}
    evidence_complete_count = 0
    replay_counts: Counter[str] = Counter()
    validation_failures = 0
    stop_the_line_events = 0
    review_pending_queue = 0

    seen_run_ids: set[str] = set()
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            failures.append({"code": "DASHBOARD_RECORD_INVALID", "field": f"records[{index}]"})
            continue

        tenant_id = str(record.get("tenant_id") or "").strip()
        if not tenant_id:
            failures.append({"code": "DASHBOARD_TENANT_ID_MISSING", "field": f"records[{index}].tenant_id"})
        else:
            tenant_counts[tenant_id] += 1

        run_id = str(record.get("run_id") or record.get("task_id") or "").strip()
        if not run_id:
            failures.append({"code": "DASHBOARD_RUN_ID_MISSING", "field": f"records[{index}].run_id"})
        elif run_id in seen_run_ids:
            failures.append({"code": "DASHBOARD_RUN_ID_DUPLICATE", "field": f"records[{index}].run_id"})
        seen_run_ids.add(run_id)

        status = _status(record)
        if not status:
            failures.append({"code": "DASHBOARD_STATUS_MISSING", "field": f"records[{index}].status"})
        status_counts[status] += 1

        executor_id = str(record.get("executor_id") or "unknown").strip() or "unknown"
        executor_totals[executor_id]["total"] += 1
        executor_totals[executor_id][status] += 1

        runtime_ms = _number(record.get("runtime_ms") or record.get("duration_ms"))
        if runtime_ms is None and status in TERMINAL_EXECUTION_STATUSES:
            missing_runtime_count += 1
        elif runtime_ms is not None:
            runtimes.append(runtime_ms)

        cost_usd = _number(record.get("cost_usd") or record.get("provider_reported_cost_usd"))
        if cost_usd is None and status in TERMINAL_EXECUTION_STATUSES:
            missing_cost_count += 1
        elif cost_usd is not None:
            costs.append(cost_usd)

        tokens = _as_dict(record.get("token_usage"))
        input_tokens = int(_number(tokens.get("input") or tokens.get("tokens_in")) or 0)
        output_tokens = int(_number(tokens.get("output") or tokens.get("tokens_out")) or 0)
        total_tokens = int(_number(tokens.get("total")) or (input_tokens + output_tokens))
        token_usage["input"] += input_tokens
        token_usage["output"] += output_tokens
        token_usage["total"] += total_tokens

        if _bool(record.get("validation_failed")) or status in FAILURE_STATUSES:
            validation_failures += 1
        if _bool(record.get("stop_the_line")) or str(record.get("failure_class") or "").upper() == "STOP_THE_LINE":
            stop_the_line_events += 1
        if str(record.get("review_state") or "").lower() == "review_pending":
            review_pending_queue += 1
        if _has_evidence(record):
            evidence_complete_count += 1
        replay_state = _replay_state(record)
        if replay_state:
            replay_counts[replay_state] += 1

    success_denominator = sum(status_counts[state] for state in TERMINAL_EXECUTION_STATUSES)
    success_count = sum(status_counts[state] for state in SUCCESS_STATUSES)
    failure_count = sum(status_counts[state] for state in FAILURE_STATUSES)

    benchmark = _as_dict(benchmark_evaluation)
    benchmark_gap_visible = bool(benchmark) and not _bool(benchmark.get("passed"))
    if benchmark and not benchmark_gap_visible:
        benchmark_gap_status = "no_gap"
    elif benchmark_gap_visible:
        benchmark_gap_status = "visible"
    else:
        benchmark_gap_status = "not_supplied"
        warnings.append({"code": "BENCHMARK_EVALUATION_NOT_SUPPLIED", "metric": "benchmark_gap_visibility"})

    provider_proof = _as_dict(provider_key_routing_proof)
    provider_key_routing_status = "verified" if _bool(provider_proof.get("verified")) else "restricted_not_proven"
    if provider_key_routing_status != "verified":
        warnings.append({"code": "PROVIDER_KEY_ROUTING_NOT_PROVEN", "metric": "provider_key_routing_status"})

    metrics = {
        "tasks_received": _metric(len(records)),
        "tasks_admitted": _metric(status_counts["admitted"]),
        "tasks_rejected": _metric(status_counts["rejected"]),
        "tasks_blocked": _metric(sum(status_counts[state] for state in BLOCKED_STATUSES)),
        "tasks_executed": _metric(success_denominator),
        "success_rate": _metric(
            round(success_count / success_denominator, 4) if success_denominator else None,
            data_status="measured" if success_denominator else "unknown_not_overclaimed",
            extra={"numerator": success_count, "denominator": success_denominator, "blocked_or_skipped_excluded": True},
        ),
        "failure_rate": _metric(
            round(failure_count / success_denominator, 4) if success_denominator else None,
            data_status="measured" if success_denominator else "unknown_not_overclaimed",
            extra={"numerator": failure_count, "denominator": success_denominator},
        ),
        "average_runtime": _metric(
            round(sum(runtimes) / len(runtimes), 2) if runtimes else None,
            data_status="measured" if runtimes and not missing_runtime_count else "unknown_not_overclaimed",
            extra={"unit": "ms", "missing_count": missing_runtime_count},
        ),
        "average_cost": _metric(
            round(sum(costs) / len(costs), 6) if costs else None,
            data_status="measured" if costs and not missing_cost_count else "unknown_not_overclaimed",
            extra={"unit": "usd", "missing_count": missing_cost_count},
        ),
        "token_usage": _metric(token_usage),
        "validation_failures": _metric(validation_failures),
        "StopTheLine_events": _metric(stop_the_line_events),
        "review_pending_queue": _metric(review_pending_queue),
        "evidence_completeness": _metric(
            round(evidence_complete_count / len(records), 4) if records else None,
            data_status="measured" if records else "unknown_not_overclaimed",
            extra={"complete_count": evidence_complete_count, "total_count": len(records)},
        ),
        "replay_success_failure": _metric({"success": replay_counts["success"], "failure": replay_counts["failure"]}),
        "tenant_level_task_counts": _metric(dict(sorted(tenant_counts.items()))),
        "executor_performance": _metric(
            {
                executor_id: {
                    "total": counts["total"],
                    "success": sum(counts[state] for state in SUCCESS_STATUSES),
                    "failure": sum(counts[state] for state in FAILURE_STATUSES),
                    "blocked": sum(counts[state] for state in BLOCKED_STATUSES),
                }
                for executor_id, counts in sorted(executor_totals.items())
            }
        ),
        "benchmark_gap_visibility": _metric(benchmark_gap_status),
        "provider_key_routing_status": _metric(provider_key_routing_status),
    }

    unknown_metrics = [
        name
        for name in ("success_rate", "failure_rate", "average_runtime", "average_cost", "evidence_completeness")
        if metrics[name]["data_status"] == "unknown_not_overclaimed"
    ]
    for name in unknown_metrics:
        warnings.append({"code": "METRIC_UNKNOWN_NOT_OVERCLAIMED", "metric": name})

    return {
        "schema": "aix.ops_dashboard.metrics_read_model.v1",
        "authority": {
            "read_only": True,
            "acceptance_authority": False,
            "workhub_lifecycle_mutation": False,
            "review_bypass_allowed": False,
        },
        "required_metrics": list(REQUIRED_OPS_DASHBOARD_METRICS),
        "metrics": metrics,
        "failures": failures,
        "warnings": warnings,
    }


def evaluate_ops_dashboard_metrics_certification(read_model: dict[str, Any]) -> dict[str, Any]:
    failures: list[dict[str, Any]] = []
    warnings = list(_as_list(read_model.get("warnings")))

    authority = _as_dict(read_model.get("authority"))
    if authority.get("read_only") is not True:
        failures.append({"code": "DASHBOARD_READ_ONLY_AUTHORITY_REQUIRED", "field": "authority.read_only"})
    for field in ("acceptance_authority", "workhub_lifecycle_mutation", "review_bypass_allowed"):
        if authority.get(field) is not False:
            failures.append({"code": "DASHBOARD_FORBIDDEN_AUTHORITY", "field": f"authority.{field}"})

    metrics = _as_dict(read_model.get("metrics"))
    missing = [name for name in REQUIRED_OPS_DASHBOARD_METRICS if name not in metrics]
    for name in missing:
        failures.append({"code": "DASHBOARD_REQUIRED_METRIC_MISSING", "metric": name})

    for failure in _as_list(read_model.get("failures")):
        if isinstance(failure, dict):
            failures.append(failure)

    for name, metric in metrics.items():
        metric_payload = _as_dict(metric)
        if metric_payload.get("data_status") == "estimated":
            failures.append({"code": "DASHBOARD_ESTIMATED_METRIC_FORBIDDEN", "metric": name})

    has_unknown_visible = any(
        _as_dict(metric).get("data_status") == "unknown_not_overclaimed" for metric in metrics.values()
    )
    if has_unknown_visible:
        warnings.append({"code": "DASHBOARD_HAS_VISIBLE_UNKNOWN_METRICS"})

    passed = not failures
    status = "passed_with_visible_gaps" if passed and warnings else "passed" if passed else "blocked"
    return {
        "schema": "aix.ops_dashboard.metrics_certification.v1",
        "status": status,
        "passed": passed,
        "certification_allowed": passed,
        "failures": failures,
        "warnings": warnings,
    }
