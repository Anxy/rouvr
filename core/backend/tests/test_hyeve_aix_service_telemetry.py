from __future__ import annotations

from prometheus_client import REGISTRY

from backend.services.aix_run_ledger_service import build_run_ledger_record
from backend.infrastructure.telemetry.hyeve_aix_service_telemetry import (
    observe_planner_packet_path,
    observe_runner_outcome,
)


def _metric_value(metric_name: str, labels: dict[str, str]) -> float:
    total = 0.0
    for family in REGISTRY.collect():
        for sample in family.samples:
            if sample.name == metric_name and sample.labels == labels:
                total += float(sample.value)
    return total


def _sample_payload() -> dict:
    return {
        "ok": False,
        "project_id": "bh-migrated-7-aix",
        "task_number": 2160,
        "task_id": "task-2160",
        "task_intent": "code_change_required",
        "final_status": "blocked",
        "result": {
            "run_id": "run-2160",
            "correlation_id": "corr-2160",
            "project_id": "bh-migrated-7-aix",
            "task_id": "task-2160",
            "task_number": 2160,
            "status": "failed",
            "evidence": {
                "commands": [],
                "tests": {"commands": [], "detected": False},
                "transcript": [],
            },
            "telemetry": {
                "model": {
                    "role": "coder.current",
                    "model": "openrouter/openai/gpt-4.1",
                },
                "runtime": {
                    "step_count": 4,
                    "elapsed_ms": 3250,
                },
            },
            "error": {"code": "QUALITY_WARN", "message": "candidate syntax invalid"},
        },
        "control_plane_outcome": {
            "status": "blocked",
            "substate": "quality_gate_rejected",
            "reason_code": "CANDIDATE_SYNTAX_INVALID",
            "reason_summary": "candidate syntax invalid",
            "handoff_status": "blocked",
            "failure_category": "validation_failed",
            "phase": "quality_gate",
        },
        "planner": {
            "planner_used": True,
            "attempts": 3,
            "model": "openrouter/openai/gpt-4.1",
            "model_lane_role": "coder.current",
            "repair_retry_history": [
                {"stage": "validation", "reason_code": "CANDIDATE_SYNTAX_INVALID", "budget_key": "parse_failure"},
                {"stage": "quality_gate", "reason_code": "QUALITY_WARN", "budget_key": "parse_failure"},
            ],
        },
        "quality_gate": {"passed": False, "code": "CANDIDATE_SYNTAX_INVALID", "message": "candidate syntax invalid"},
        "task_packet": {
            "task_number": 2160,
            "metadata": {
                "persona_id": "coder.current",
                "ticket_contract_current": {},
            },
        },
        "validation_contract": {
            "code_change_required": True,
            "target_files": ["backend/aix_core/bootstrap/task_kind.py"],
        },
        "repo_profile": {"profile_id": "RP-HYEVE-AIX-PY-FASTAPI-CURRENT"},
    }


def test_observe_runner_outcome_emits_counters_and_duration() -> None:
    payload = _sample_payload()
    record = build_run_ledger_record(payload)

    outcome_labels = {
        "task_intent": "code_change_required",
        "execution_state": "failed",
        "final_status": "blocked",
    }
    model_labels = {
        "persona": "coder.current",
        "model": "openrouter_openai_gpt-4.1",
        "quality_gate_outcome": "QUALITY_WARN",
    }
    gate_labels = {
        "quality_gate_outcome": "QUALITY_WARN",
        "quality_gate_code": "CANDIDATE_SYNTAX_INVALID",
    }
    attempt_labels = {
        "persona": "coder.current",
        "model": "openrouter_openai_gpt-4.1",
    }
    retried_labels = outcome_labels
    retry_validation_labels = {"retry_kind": "planner_retry"}
    retry_quality_labels = {"retry_kind": "quality_gate_retry"}
    duration_labels = {
        "task_intent": "code_change_required",
        "final_status": "blocked",
    }
    stage_labels = {
        "current_stage": "quality_gate",
        "handoff_status": "blocked",
        "final_status": "blocked",
    }
    bottleneck_labels = {
        "current_stage": "quality_gate",
        "reason_code": "CANDIDATE_SYNTAX_INVALID",
        "failure_category": "validation_failed",
    }
    failing_tool_labels = {
        "current_stage": "quality_gate",
        "failing_tool": "none",
        "reason_code": "CANDIDATE_SYNTAX_INVALID",
    }

    before_outcomes = _metric_value("hyeve_aix_runner_outcomes_total", outcome_labels)
    before_model = _metric_value("hyeve_aix_runner_model_outcomes_total", model_labels)
    before_gate = _metric_value("hyeve_aix_runner_quality_gate_total", gate_labels)
    before_attempts = _metric_value("hyeve_aix_runner_planner_attempts_total", attempt_labels)
    before_retried = _metric_value("hyeve_aix_runner_retried_runs_total", retried_labels)
    before_retry_validation = _metric_value("hyeve_aix_runner_retry_actions_total", retry_validation_labels)
    before_retry_quality = _metric_value("hyeve_aix_runner_retry_actions_total", retry_quality_labels)
    before_duration_count = _metric_value("hyeve_aix_runner_execution_duration_seconds_count", duration_labels)
    before_duration_sum = _metric_value("hyeve_aix_runner_execution_duration_seconds_sum", duration_labels)
    before_stage = _metric_value("hyeve_aix_runner_stage_total", stage_labels)
    before_bottleneck = _metric_value("hyeve_aix_runner_bottlenecks_total", bottleneck_labels)
    before_failing_tool = _metric_value("hyeve_aix_runner_failing_tools_total", failing_tool_labels)

    observe_runner_outcome(payload=payload, record=record)

    assert _metric_value("hyeve_aix_runner_outcomes_total", outcome_labels) == before_outcomes + 1.0
    assert _metric_value("hyeve_aix_runner_model_outcomes_total", model_labels) == before_model + 1.0
    assert _metric_value("hyeve_aix_runner_quality_gate_total", gate_labels) == before_gate + 1.0
    assert _metric_value("hyeve_aix_runner_planner_attempts_total", attempt_labels) == before_attempts + 3.0
    assert _metric_value("hyeve_aix_runner_retried_runs_total", retried_labels) == before_retried + 1.0
    assert (
        _metric_value("hyeve_aix_runner_retry_actions_total", retry_validation_labels) == before_retry_validation + 1.0
    )
    assert _metric_value("hyeve_aix_runner_retry_actions_total", retry_quality_labels) == before_retry_quality + 1.0
    assert (
        _metric_value("hyeve_aix_runner_execution_duration_seconds_count", duration_labels)
        == before_duration_count + 1.0
    )
    assert (
        _metric_value("hyeve_aix_runner_execution_duration_seconds_sum", duration_labels) == before_duration_sum + 3.25
    )
    assert _metric_value("hyeve_aix_runner_stage_total", stage_labels) == before_stage + 1.0
    assert _metric_value("hyeve_aix_runner_bottlenecks_total", bottleneck_labels) == before_bottleneck + 1.0
    assert _metric_value("hyeve_aix_runner_failing_tools_total", failing_tool_labels) == before_failing_tool + 1.0


def test_observe_runner_outcome_handles_missing_runtime_and_defaults_quality_outcome() -> None:
    payload = _sample_payload()
    payload["quality_gate"] = {}
    payload["planner"]["attempts"] = 1
    payload["planner"]["repair_retry_history"] = []
    payload["result"]["telemetry"]["runtime"]["elapsed_ms"] = None
    record = build_run_ledger_record(payload)

    gate_labels = {
        "quality_gate_outcome": "UNKNOWN",
        "quality_gate_code": "none",
    }
    retried_labels = {
        "task_intent": "code_change_required",
        "execution_state": "failed",
        "final_status": "blocked",
    }
    duration_labels = {
        "task_intent": "code_change_required",
        "final_status": "blocked",
    }
    stage_labels = {
        "current_stage": "quality_gate",
        "handoff_status": "blocked",
        "final_status": "blocked",
    }
    bottleneck_labels = {
        "current_stage": "quality_gate",
        "reason_code": "CANDIDATE_SYNTAX_INVALID",
        "failure_category": "validation_failed",
    }

    before_gate = _metric_value("hyeve_aix_runner_quality_gate_total", gate_labels)
    before_retried = _metric_value("hyeve_aix_runner_retried_runs_total", retried_labels)
    before_duration_count = _metric_value("hyeve_aix_runner_execution_duration_seconds_count", duration_labels)
    before_stage = _metric_value("hyeve_aix_runner_stage_total", stage_labels)
    before_bottleneck = _metric_value("hyeve_aix_runner_bottlenecks_total", bottleneck_labels)

    observe_runner_outcome(payload=payload, record=record)

    assert _metric_value("hyeve_aix_runner_quality_gate_total", gate_labels) == before_gate + 1.0
    assert _metric_value("hyeve_aix_runner_retried_runs_total", retried_labels) == before_retried
    assert _metric_value("hyeve_aix_runner_execution_duration_seconds_count", duration_labels) == before_duration_count
    assert _metric_value("hyeve_aix_runner_stage_total", stage_labels) == before_stage + 1.0
    assert _metric_value("hyeve_aix_runner_bottlenecks_total", bottleneck_labels) == before_bottleneck + 1.0


def test_observe_runner_outcome_emits_transition_metrics_from_execution_digest() -> None:
    payload = _sample_payload()
    record = build_run_ledger_record(payload)
    record["execution_digest"] = {
        "mutation": {
            "primary_mutation_tool": "edit_file",
            "fallback_occurred": True,
            "fallback_entries": [
                {"primitive": "replace_in_function", "reason": "explicit_fallback_gate"},
            ],
        },
        "attestation": {
            "passed": False,
            "reason_code": "SYMBOL_RESOLVER_UNAVAILABLE",
        },
        "postcheck": {
            "passed": False,
            "scope": "touched_files",
        },
        "reason_code": "PYRIGHT_FAILED",
    }

    primary_labels = {
        "primary_mutation_tool": "edit_file",
        "fallback_used": "true",
    }
    fallback_labels = {
        "primitive": "replace_in_function",
        "reason": "explicit_fallback_gate",
    }
    attestation_labels = {
        "reason_code": "SYMBOL_RESOLVER_UNAVAILABLE",
    }
    postcheck_labels = {
        "validation_scope": "touched_files",
        "reason_code": "PYRIGHT_FAILED",
    }

    before_primary = _metric_value("hyeve_aix_runner_primary_mutation_path_total", primary_labels)
    before_fallback = _metric_value("hyeve_aix_runner_mutation_fallback_total", fallback_labels)
    before_attestation = _metric_value("hyeve_aix_runner_attestation_failures_total", attestation_labels)
    before_postcheck = _metric_value("hyeve_aix_runner_post_apply_reject_total", postcheck_labels)

    observe_runner_outcome(payload=payload, record=record)

    assert _metric_value("hyeve_aix_runner_primary_mutation_path_total", primary_labels) == before_primary + 1.0
    assert _metric_value("hyeve_aix_runner_mutation_fallback_total", fallback_labels) == before_fallback + 1.0
    assert _metric_value("hyeve_aix_runner_attestation_failures_total", attestation_labels) == before_attestation + 1.0
    assert _metric_value("hyeve_aix_runner_post_apply_reject_total", postcheck_labels) == before_postcheck + 1.0


def test_observe_planner_packet_path_emits_strict_packet_counter() -> None:
    labels = {
        "task_family": "hyeve-codeintel-service:impl:code_change_required",
        "execution_phase": "impl",
        "planner_packet_attempted": "true",
        "planner_packet_ready": "true",
        "planner_packet_mode": "strict_packet",
        "planner_packet_status": "ready",
    }
    before = _metric_value("hyeve_aix_runner_planner_packet_path_total", labels)

    observe_planner_packet_path(
        task_family="hyeve-codeintel-service:impl:code_change_required",
        execution_phase="impl",
        planner_packet_attempted=True,
        planner_packet_ready=True,
        planner_packet_mode="strict_packet",
        planner_packet_status="ready",
        clarification_gap_count=0,
    )

    assert _metric_value("hyeve_aix_runner_planner_packet_path_total", labels) == before + 1.0


def test_observe_planner_packet_path_emits_fallback_counter() -> None:
    labels = {
        "task_family": "hyeve-codeintel-service:impl:code_change_required",
        "execution_phase": "impl",
        "planner_packet_attempted": "true",
        "planner_packet_ready": "false",
        "planner_packet_mode": "fallback",
        "planner_packet_status": "clarification_required",
    }
    before = _metric_value("hyeve_aix_runner_planner_packet_path_total", labels)

    observe_planner_packet_path(
        task_family="hyeve-codeintel-service:impl:code_change_required",
        execution_phase="impl",
        planner_packet_attempted=True,
        planner_packet_ready=False,
        planner_packet_mode="fallback",
        planner_packet_status="clarification_required",
        clarification_gap_count=2,
    )

    assert _metric_value("hyeve_aix_runner_planner_packet_path_total", labels) == before + 1.0
