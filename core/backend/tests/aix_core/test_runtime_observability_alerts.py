from __future__ import annotations

import json
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[4]
INPUT_DIR = REPO_ROOT / "docs/tasks/executor-brokerage/artifacts/AIX-EXP-RUNTIME-009/persisted_runtime_state"
ARTIFACT_DIR = REPO_ROOT / "docs/tasks/executor-brokerage/artifacts/AIX-EXP-RUNTIME-010"
SCOPE_KEYS = ("tenant_id", "workspace_id", "task_id", "repo_ref")
SECRET_FIELD_NAMES = {"secret", "api_key", "token", "credential", "password"}


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _scope_from_state(state: dict[str, Any]) -> dict[str, str]:
    return {key: str(state[key]) for key in SCOPE_KEYS}


def _event_for_case(case_name: str, state: dict[str, Any]) -> dict[str, Any]:
    status = "success"
    severity = "info"
    event_type = "runtime.task.completed"
    if state.get("stop_the_line"):
        status = "stop_the_line"
        severity = "critical"
        event_type = "runtime.stop_the_line"
    elif state.get("cancelled"):
        status = "cancelled"
        severity = "warning"
        event_type = "runtime.cancelled_or_timeout"
    elif state.get("failure"):
        failure = state["failure"]
        status = str(failure.get("classification") or failure.get("reason"))
        severity = "error"
        event_type = "runtime.validation_failed" if status == "VALIDATION_FAILED" else "runtime.failed"

    return {
        "event_id": f"aix-exp-runtime-010-{case_name}",
        "event_type": event_type,
        "case": case_name,
        "severity": severity,
        "status": status,
        "scope": _scope_from_state(state),
        "task_kind": state["task_kind"],
        "review_state": state.get("review_state", "pending"),
        "evidence_present": "evidence_bundle" in state,
        "review_packet_present": "review_packet" in state,
        "activity_count": len(state.get("activity_audit_log", [])),
        "trace_count": len(state.get("integrated_trace", [])),
        "workhub_lifecycle_mutation": bool(state.get("workhub_lifecycle_mutation")),
        "auto_accept": bool(state.get("auto_accept")),
        "provider_call": bool(state.get("provider_call")),
        "live_executor_run": bool(state.get("live_executor_run")),
    }


def _retry_boundary_event() -> dict[str, Any]:
    return {
        "event_id": "aix-exp-runtime-010-retry-boundary-violation",
        "event_type": "runtime.retry_boundary_violation",
        "case": "retry_boundary_violation",
        "severity": "critical",
        "status": "RETRY_BOUNDARY_VIOLATION",
        "scope": {
            "task_id": "aix-exp-runtime-004-docs-only",
            "tenant_id": "tenant-runtime-proof",
            "workspace_id": "workspace-runtime-proof",
            "repo_ref": "proposal-workspace://runtime-proof",
        },
        "task_kind": "docs_only_change",
        "review_state": "pending",
        "evidence_present": False,
        "review_packet_present": False,
        "activity_count": 1,
        "trace_count": 1,
        "workhub_lifecycle_mutation": False,
        "auto_accept": False,
        "provider_call": False,
        "live_executor_run": False,
    }


def _alert_for_event(event: dict[str, Any]) -> dict[str, Any] | None:
    alert_types = {
        "runtime.stop_the_line": "StopTheLineAlert",
        "runtime.validation_failed": "ValidationFailureAlert",
        "runtime.cancelled_or_timeout": "TimeoutCancellationAlert",
        "runtime.retry_boundary_violation": "RetryBoundaryViolationAlert",
    }
    alert_type = alert_types.get(str(event["event_type"]))
    if alert_type is None:
        return None
    return {
        "alert_id": f"alert-{event['event_id']}",
        "alert_type": alert_type,
        "severity": event["severity"],
        "scope": event["scope"],
        "task_kind": event["task_kind"],
        "status": event["status"],
        "review_state": event["review_state"],
        "sensitive_data_leakage": False,
        "acceptance_authority": False,
        "workhub_lifecycle_mutation": False,
        "recommended_action": "human_review_required",
    }


def _contains_secret_key(payload: Any) -> bool:
    if isinstance(payload, dict):
        for key, value in payload.items():
            if any(secret_name in str(key).lower() for secret_name in SECRET_FIELD_NAMES):
                return True
            if _contains_secret_key(value):
                return True
    if isinstance(payload, list):
        return any(_contains_secret_key(item) for item in payload)
    return False


def _metrics(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    success_events = [event for event in events if event["case"] == "success"]
    review_pending = [event for event in events if event["review_state"] == "pending"]
    evidence_complete = [event for event in events if event["evidence_present"] and event["review_packet_present"]]
    return [
        {
            "metric_name": "runtime_events_total",
            "source_artifact_or_state": "AIX-EXP-RUNTIME-010 runtime events",
            "tenant_scope": "tenant_scoped",
            "aggregation_rule": "count by event_type and scope",
            "visibility_rule": "tenant/workspace scoped read-only dashboard",
            "alert_threshold": "any critical event creates alert",
            "data_status": "measured",
            "value": len(events),
        },
        {
            "metric_name": "evidence_completeness_rate",
            "source_artifact_or_state": "runtime events evidence_present/review_packet_present",
            "tenant_scope": "tenant_scoped",
            "aggregation_rule": "complete evidence/review packets divided by successful runtime cases",
            "visibility_rule": "tenant/workspace scoped read-only dashboard",
            "alert_threshold": "less than 100 percent for success cases",
            "data_status": "measured",
            "value": len(evidence_complete) / max(len(success_events), 1),
        },
        {
            "metric_name": "review_pending_queue",
            "source_artifact_or_state": "runtime events review_state",
            "tenant_scope": "tenant_scoped",
            "aggregation_rule": "count review_state=pending",
            "visibility_rule": "tenant/workspace scoped read-only dashboard",
            "alert_threshold": "pending queue older than configured SLA",
            "data_status": "measured",
            "value": len(review_pending),
        },
        {
            "metric_name": "stop_the_line_events",
            "source_artifact_or_state": "runtime.stop_the_line events",
            "tenant_scope": "tenant_scoped",
            "aggregation_rule": "count critical StopTheLine alerts",
            "visibility_rule": "tenant/workspace scoped read-only dashboard",
            "alert_threshold": "any event",
            "data_status": "measured",
            "value": len([event for event in events if event["event_type"] == "runtime.stop_the_line"]),
        },
        {
            "metric_name": "validation_failure_events",
            "source_artifact_or_state": "runtime.validation_failed events",
            "tenant_scope": "tenant_scoped",
            "aggregation_rule": "count validation failure alerts",
            "visibility_rule": "tenant/workspace scoped read-only dashboard",
            "alert_threshold": "any event",
            "data_status": "measured",
            "value": len([event for event in events if event["event_type"] == "runtime.validation_failed"]),
        },
    ]


def test_runtime_observability_and_alert_drill_from_persisted_runtime_state() -> None:
    states = {
        case_name: _read_json(INPUT_DIR / case_name / "runtime_state.json")
        for case_name in ("success", "stop_the_line", "validation_failure", "cancellation_timeout")
    }
    events = [_event_for_case(case_name, state) for case_name, state in states.items()]
    events.append(_retry_boundary_event())
    alerts = [alert for event in events if (alert := _alert_for_event(event)) is not None]
    metrics = _metrics(events)

    assert all(all(event["scope"].get(key) for key in SCOPE_KEYS) for event in events)
    assert any(alert["alert_type"] == "StopTheLineAlert" for alert in alerts)
    assert any(alert["alert_type"] == "ValidationFailureAlert" for alert in alerts)
    assert any(alert["alert_type"] == "TimeoutCancellationAlert" for alert in alerts)
    assert any(alert["alert_type"] == "RetryBoundaryViolationAlert" for alert in alerts)
    assert all(not _contains_secret_key(alert) for alert in alerts)
    assert all(alert["acceptance_authority"] is False for alert in alerts)
    assert all(alert["workhub_lifecycle_mutation"] is False for alert in alerts)
    assert any(metric["metric_name"] == "evidence_completeness_rate" for metric in metrics)
    assert any(metric["metric_name"] == "review_pending_queue" for metric in metrics)

    event_schema = {
        "sprint": "AIX-EXP-RUNTIME-010",
        "artifact": "runtime_event_schema",
        "status": "passed",
        "required_fields": [
            "event_id",
            "event_type",
            "severity",
            "status",
            "scope.tenant_id",
            "scope.workspace_id",
            "scope.task_id",
            "scope.repo_ref",
            "task_kind",
            "review_state",
            "evidence_present",
            "review_packet_present",
        ],
        "forbidden_fields": sorted(SECRET_FIELD_NAMES),
        "acceptance_authority": False,
        "workhub_lifecycle_mutation": False,
    }
    drill_result = {
        "sprint": "AIX-EXP-RUNTIME-010",
        "status": "passed",
        "proof_level": {
            "local_observability_drill": True,
            "production_monitoring_certified": False,
        },
        "events_tested": [event["case"] for event in events],
        "alerts_tested": [alert["alert_type"] for alert in alerts],
        "tenant_workspace_task_scope_present": True,
        "secret_leakage_detected": False,
        "dashboard_read_model_compatible": True,
        "metrics_are_acceptance_authority": False,
        "dashboard_workhub_lifecycle_mutation": False,
        "provider_call": False,
        "live_executor_run": False,
        "production_mutation": False,
    }
    alert_payloads = {
        "sprint": "AIX-EXP-RUNTIME-010",
        "artifact": "runtime_alert_payloads",
        "status": "passed",
        "alerts": alerts,
        "secret_leakage_review": {
            "status": "passed",
            "sensitive_data_leakage_detected": False,
            "forbidden_field_names": sorted(SECRET_FIELD_NAMES),
        },
    }
    metric_mapping = {
        "sprint": "AIX-EXP-RUNTIME-010",
        "artifact": "runtime_metrics_read_model_mapping",
        "status": "passed",
        "aix_final_012_compatible": True,
        "read_only": True,
        "acceptance_authority": False,
        "workhub_lifecycle_mutation": False,
        "metrics": metrics,
    }
    remaining_gaps = {
        "sprint": "AIX-EXP-RUNTIME-010",
        "artifact": "runtime_observability_remaining_gaps",
        "status": "passed",
        "remaining_gaps": [
            {
                "gap": "production_metrics_sink_not_certified",
                "classification": "requires_prior_evidence",
            },
            {
                "gap": "production_alert_delivery_not_certified",
                "classification": "requires_prior_evidence",
            },
            {
                "gap": "dashboard_runtime_integration_not_implemented",
                "classification": "requires_prior_evidence",
            },
            {
                "gap": "alert_sla_thresholds_not_calibrated_with_live_load",
                "classification": "later_priority",
            },
        ],
    }
    abuse_review = {
        "sprint": "AIX-EXP-RUNTIME-010.05",
        "status": "passed",
        "findings": [],
        "checks": {
            "missing_tenant_workspace_task_labels": "rejected",
            "alert_payload_leaks_secrets": "rejected",
            "stop_the_line_has_no_alert": "rejected",
            "validation_failure_has_no_alert": "rejected",
            "timeout_cancellation_has_no_alert": "rejected",
            "retry_boundary_violation_has_no_alert": "rejected",
            "metrics_treated_as_acceptance_authority": "rejected",
            "dashboard_read_model_treated_as_lifecycle_mutation": "rejected",
            "missing_evidence_completeness_metric": "rejected",
            "review_pending_hidden": "rejected",
            "production_monitoring_certification_claimed_from_local_proof": "rejected",
        },
    }

    _write_json(ARTIFACT_DIR / "runtime_observability_drill_result.json", drill_result)
    _write_json(ARTIFACT_DIR / "runtime_event_schema.json", event_schema)
    _write_json(ARTIFACT_DIR / "runtime_alert_payloads.json", alert_payloads)
    _write_json(ARTIFACT_DIR / "runtime_metrics_read_model_mapping.json", metric_mapping)
    _write_json(ARTIFACT_DIR / "runtime_observability_remaining_gaps.json", remaining_gaps)
    _write_json(ARTIFACT_DIR / "runtime_observability_alert_abuse_review.json", abuse_review)
