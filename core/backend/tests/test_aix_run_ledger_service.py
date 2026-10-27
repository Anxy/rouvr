from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from backend.services.aix_run_ledger_service import (
    _resolve_runlog_root,
    build_attestation_from_run_ledger_record,
    build_execution_state_projection,
    build_run_ledger_record,
    build_run_ledger_summary,
    build_task_fast_ledger,
    build_transition_record_from_run_ledger_record,
    classify_run_failure,
    get_latest_runtime_memory_capsule,
    list_run_ledger_records,
    persist_run_ledger_record,
    to_compact_run_ledger_record,
)


def _sample_payload() -> dict:
    return {
        "ok": False,
        "project_id": "bh-migrated-7-aix",
        "task_number": 1458,
        "task_id": "task-1458",
        "task_intent": "code_change_required",
        "final_status": "needs_review",
        "result": {
            "run_id": "run-1458",
            "correlation_id": "corr-1458",
            "project_id": "bh-migrated-7-aix",
            "task_id": "task-1458",
            "task_number": 1458,
            "status": "failed",
            "evidence": {
                "proposal_artifacts": {"diff_ref": "local://diff.patch", "changed_files_ref": "local://changed.json"},
                "escalation_packet": {
                    "schema_version": "aix.escalation_packet.current",
                    "failure_class": "grounding_failed",
                    "question_kind": "grounding_confirmation",
                    "question": "What authoritative symbol should AIX mutate?",
                },
                "commands": ["pytest tests/test_runner.py -q"],
                "tests": {"detected": True, "commands": ["pytest tests/test_runner.py -q"]},
                "transcript": [
                    {
                        "tool": "confirm_anchor",
                        "ok": True,
                        "result": {
                            "relative_path": "backend/indexing/runner.py",
                            "symbol_name": "RunnerService.execute",
                            "reason_code": "MATCH",
                            "matched": True,
                        },
                    },
                    {
                        "tool": "edit_symbol",
                        "ok": False,
                        "result": {
                            "dry_run": True,
                            "dry_run_failures": [
                                {
                                    "tool": "edit_symbol",
                                    "symbol": "RunnerService.execute",
                                    "failure_code": "INVALID_SIGNATURE_SYNTAX",
                                    "detail": "bad signature",
                                    "suggested_fix": "preserve ordering",
                                }
                            ],
                        },
                    },
                ],
            },
            "error": {"code": "quality_gate_rejected", "message": "quality gate rejected the run"},
            "evidence_refs": ["local://evidence/ref-1"],
        },
        "control_plane_outcome": {
            "phase": "final",
            "status": "needs_review",
            "substate": "quality_gate_rejected",
            "reason_code": "TARGET_LOGIC_NOT_CONFIRMED",
            "reason_summary": "planner exhausted bounded discovery without confirming a safe mutating path",
            "handoff_status": "needs_replan",
            "failure_category": "bad_decomposition",
            "blocked_reason": None,
        },
        "planner": {
            "planner_used": True,
            "attempts": 2,
            "provider": "slavx",
            "local_trace_ref": "local://planner-traces/trace-1458.json",
            "contract_fingerprint": "abc123fingerprint",
            "model_lane": "diagnostic",
            "model_lane_role": "diagnostic_safe",
            "model_lane_cost_tier": "cheap",
            "model_lane_selection_reason": "verification_grounded_low_cost_lane",
            "model_lane_config_path": "backend/config/model_routing_config.yaml",
            "model_lane_source_chain": [
                {"layer": "base_config", "source": "backend/config/model_routing_config.yaml"},
                {"layer": "metadata.model_lane_policy"},
            ],
            "error": "DIAGNOSTIC_BLOCKED",
            "planner_packet_status": "ready",
            "planner_packet_metrics": {"hint_count": 1},
            "planner_packet": {
                "schema_name": "aix.planner.slice_packet.current",
                "schema_version": "1",
                "status": "ready",
                "slice_id": "slice-1458-001",
                "task_number": 1458,
                "task_intent": "code_change_required",
                "target_symbol": "RunnerService.execute",
                "target_files": ["backend/indexing/runner.py"],
                "validation_commands": ["pytest tests/test_runner.py -q"],
                "tool_policy": {"execution_lane": "mutation"},
            },
            "repair_retry_history": [
                {
                    "stage": "quality_gate",
                    "reason_code": "CANDIDATE_SYNTAX_INVALID",
                    "budget_key": "parse_failure",
                }
            ],
        },
        "quality_gate": {"passed": False, "code": "diagnostic_blocked", "message": "diagnostic blocked"},
        "repair_loop": {
            "failure_code": "TARGET_LOGIC_NOT_CONFIRMED",
            "failure_class": "unknown",
            "policy_action": "replan",
            "budget_key": None,
            "max_attempts": 0,
            "attempts_used": 0,
            "retryable": False,
            "same_turn_repair_active": False,
            "recovery_task_suppressed": False,
            "recovery_submission_reason": "RECOVERY_TASK_CREATED",
            "retry_history": [
                {
                    "stage": "quality_gate",
                    "reason_code": "CANDIDATE_SYNTAX_INVALID",
                    "budget_key": "parse_failure",
                }
            ],
        },
        "diagnostic_outcome": {
            "status": "diagnostic_blocked",
            "reason_code": "TARGET_LOGIC_NOT_CONFIRMED",
            "reason_summary": "planner exhausted bounded discovery without confirming a safe mutating path",
        },
        "assist_loop": {
            "conversation_state": "awaiting_guidance",
            "awaiting_actor": "onlineai",
            "open_questions": ["What grounded symbol should be mutated?"],
            "guidance_requests": [
                {
                    "requester": "planner",
                    "reason_code": "TARGET_LOGIC_NOT_CONFIRMED",
                    "question": "What grounded symbol should be mutated?",
                    "channel": "onlineai",
                }
            ],
            "onlineai_guidance": [
                {"summary": "Prefer CodeintelQueryService.answer_endpoint_handler_lookup", "source": "onlineai"}
            ],
            "resume_context": {"resume_run_id": "devrun-1458a"},
        },
        "outcome_policy_trace": {
            "trace_ref": "ayrpack://packet/pkt-outcome-1458",
            "local_trace_ref": "local://outcome-policy/1458.json",
        },
        "writeback": {"status": "failed", "opsbus_written": True, "workhub_written": False, "retry_enqueued": True},
        "workspace": {"source_workspace_root": "C:/dev/hyeve", "effective_workspace_root": "C:/dev/hyeve-proposals/x"},
        "runtime_trace": {
            "stage_current": {
                "timestamp_utc": "2026-03-18T10:00:03+00:00",
                "stage": "verifier_entry",
                "summary": "entering verifier and closeout projection",
                "details": {"quality_gate_passed": False, "result_status": "failed"},
            },
            "stage_events": [
                {
                    "timestamp_utc": "2026-03-18T10:00:01+00:00",
                    "stage": "planner_dispatch",
                    "summary": "dispatching planner bridge for bounded execution slice",
                    "details": {"task_number": 1458},
                },
                {
                    "timestamp_utc": "2026-03-18T10:00:02+00:00",
                    "stage": "devexecutor_dispatch",
                    "summary": "dispatching bounded slice into devexecutor runtime",
                    "details": {"step_count": 2},
                },
                {
                    "timestamp_utc": "2026-03-18T10:00:03+00:00",
                    "stage": "verifier_entry",
                    "summary": "entering verifier and closeout projection",
                    "details": {"quality_gate_passed": False, "result_status": "failed"},
                },
            ],
            "durable_phases": [
                {
                    "phase_name": "plan",
                    "attempt": 1,
                    "status": "completed",
                    "ref": "local://phase-ledger/1458/plan.1.json",
                },
                {
                    "phase_name": "slice_validation",
                    "attempt": 1,
                    "status": "completed",
                    "ref": "local://phase-ledger/1458/slice_validation.1.json",
                },
                {
                    "phase_name": "dispatch",
                    "attempt": 1,
                    "status": "completed",
                    "ref": "local://phase-ledger/1458/dispatch.1.json",
                },
                {
                    "phase_name": "mutation",
                    "attempt": 1,
                    "status": "failed",
                    "reason_code": "TARGET_LOGIC_NOT_CONFIRMED",
                    "ref": "local://phase-ledger/1458/mutation.1.json",
                },
                {
                    "phase_name": "closeout",
                    "attempt": 1,
                    "status": "completed",
                    "ref": "local://phase-ledger/1458/closeout.1.json",
                },
            ],
        },
        "codeintel": {
            "policy_mode": "codeintel_first",
            "mode": "fallback",
            "error": "planner exhausted bounded discovery",
            "query_identity": {
                "entity_query": "RunnerService.execute",
                "query_fingerprint": "fp-1458",
            },
            "retrieval_mode": "codeintel_first",
            "confidence_score": 0.42,
            "latency_ms": 128.5,
            "retrieval_adapter": "haystack_fallback",
            "adapter_latency_ms": 41.0,
            "topk_returned": 3,
            "source_refs_count": 2,
            "citations_count": 1,
            "citations": [
                {
                    "repo_id": "repo-1",
                    "file_path": "backend/indexing/runner.py",
                    "start_line": 44,
                    "end_line": 60,
                    "snapshot_id": "snap-1458",
                    "commit_sha": "commit-1458",
                    "confidence": 0.42,
                }
            ],
            "fallback_reason_code": "TARGET_LOGIC_NOT_CONFIRMED",
            "fallback_reason_summary": "planner exhausted bounded discovery without confirming a safe mutating path",
        },
        "repo_profile": {"profile_id": "RP-HYEVE-CODEINTEL-PY-FASTAPI-CURRENT"},
        "task_packet": {
            "task_id": "task-1458",
            "task_number": 1458,
            "title": "ledger enrichment",
            "priority": "P1",
            "metadata": {
                "repo_key": "hyeve-aix-service",
                "slice_focus": "phase15_minimal_run_outcome",
                "change_intent": "code_change_required",
                "roadmap_phase": "phase0",
            },
        },
        "feature_slice_plan": [
            {"slice_id": "impl", "target_files": ["backend/indexing/runner.py"]},
            {"slice_id": "verification", "target_files": ["tests/test_runner.py"]},
        ],
        "validation_contract": {
            "expected_commands": ["pytest tests/test_runner.py -q"],
            "target_files": ["backend/indexing/runner.py"],
            "prompt_symbol_targets": [
                {
                    "relative_path": "backend/indexing/runner.py",
                    "symbol": "RunnerService.execute",
                    "label": "runner owner",
                }
            ],
            "code_change_required": True,
        },
        "temporal": {
            "enabled": True,
            "dispatch": {"attempted": True, "results": [{"stage": "plan", "status": "started"}]},
            "state_machine": {
                "state": "needs_review",
                "events": [
                    {
                        "sequence": 1,
                        "from_state": "queued",
                        "to_state": "planning",
                        "reason_code": "WORKHUB_TASK_CLAIMED",
                    },
                    {
                        "sequence": 2,
                        "from_state": "planning",
                        "to_state": "needs_review",
                        "reason_code": "QUALITY_GATE_REJECTED",
                    },
                ],
            },
        },
    }


def test_classify_run_failure_prefers_diagnostic_outcome() -> None:
    failure = classify_run_failure(_sample_payload())
    assert failure["stage"] == "planning"
    assert failure["category"] == "diagnostic_blocked"
    assert failure["code"] == "TARGET_LOGIC_NOT_CONFIRMED"
    assert failure["outcome_class"] == "discovery.miss"
    assert failure["policy_action"] == "replan"
    assert failure["failure_family"] == "grounding_failed"


def test_classify_run_failure_treats_planner_upstream_timeout_as_execution_failure() -> None:
    payload = _sample_payload()
    payload["diagnostic_outcome"] = {
        "status": "diagnostic_blocked",
        "reason_code": "PLANNER_UPSTREAM_REQUEST_FAILED",
        "reason_summary": "PLANNER_UPSTREAM_REQUEST_FAILED error=timed out attempts=1",
    }

    failure = classify_run_failure(payload)

    assert failure["stage"] == "planning"
    assert failure["code"] == "PLANNER_UPSTREAM_REQUEST_FAILED"
    assert failure["category"] == "execution_failure"
    assert failure["outcome_class"] == "runtime.unreachable"
    assert failure["policy_action"] == "retry"
    assert failure["failure_family"] == "env_error"


def test_persist_and_list_run_ledger_records(tmp_path: Path) -> None:
    outcome = persist_run_ledger_record(_sample_payload(), root=tmp_path)
    assert Path(outcome["ref"]).exists()
    record = json.loads(Path(outcome["ref"]).read_text(encoding="utf-8"))
    assert record["schema_version"] == "aix.run_ledger.current"
    assert record["attestation"]["pack_id"] == "coding"
    assert record["attestation"]["attestation_type"] == "QUALITY_GATE"
    assert record["attestation"]["verdict"] == "FAIL"
    assert record["attestation"]["scope"]["scope_type"] == "execution_unit"
    assert record["attestation"]["pack_attestation_id"]
    assert record["transition_record"]["prior_state"] == "planning"
    assert record["transition_record"]["new_state"] == "needs_review"
    assert record["transition_record"]["attestation_id"] == record["attestation"]["attestation_id"]
    assert record["failure"]["category"] == "diagnostic_blocked"
    assert record["failure"]["outcome_class"] == "discovery.miss"
    assert record["failure"]["policy_action"] == "replan"

    items = list_run_ledger_records(project_id="bh-migrated-7-aix", limit=10, root=tmp_path)
    assert len(items) == 1
    assert items[0]["correlation_id"] == "corr-1458"
    assert items[0]["ai_operator"]["symbol_targets"][0]["symbol"] == "RunnerService.execute"
    assert items[0]["ai_operator"]["anchor_confirmations"][0]["reason_code"] == "MATCH"
    assert items[0]["ai_operator"]["typed_failures"][0]["failure_code"] == "INVALID_SIGNATURE_SYNTAX"
    assert items[0]["ai_operator"]["typed_failures"][0]["outcome_class"] == "unknown"
    assert items[0]["ai_operator"]["typed_failures"][0]["policy_action"] == "escalate"
    assert items[0]["ai_operator"]["typed_failures"][0]["failure_family"] == "parse_error"
    assert items[0]["ai_operator"]["typed_failures"][0]["retry_budget_key"] == "parse_failure"
    assert items[0]["ai_operator"]["typed_failures"][0]["matched_policy"] == "default"
    assert (
        items[0]["ai_operator"]["typed_failures"][0]["next_best_actions"][0]
        == "repair syntax and indentation boundaries in the changed files"
    )
    assert items[0]["ai_operator"]["mutation_attempts"][0]["reason_code"] == "INVALID_SIGNATURE_SYNTAX"
    assert items[0]["ai_operator"]["mutation_attempts"][0]["failure_detail"] == "bad signature"
    assert items[0]["planner"]["model_lane_cost_tier"] == "cheap"
    assert items[0]["planner"]["model_lane_selection_reason"] == "verification_grounded_low_cost_lane"
    assert items[0]["planner"]["model_lane_config_path"] == "backend/config/model_routing_config.yaml"
    assert items[0]["planner"]["model_lane_source_chain"][0]["layer"] == "base_config"
    assert items[0]["planner"]["model_lane_source_chain"][1]["layer"] == "metadata.model_lane_policy"
    assert items[0]["codeintel"]["policy_mode"] == "codeintel_first"
    assert items[0]["codeintel"]["query_identity"]["entity_query"] == "RunnerService.execute"
    assert items[0]["codeintel"]["query_identity"]["query_fingerprint"] == "fp-1458"
    assert items[0]["codeintel"]["citations"][0]["file_path"] == "backend/indexing/runner.py"
    assert items[0]["codeintel"]["citations"][0]["start_line"] == 44
    assert items[0]["codeintel"]["fallback_reason_code"] == "TARGET_LOGIC_NOT_CONFIRMED"
    assert items[0]["repair_loop"]["failure_code"] == "TARGET_LOGIC_NOT_CONFIRMED"
    assert items[0]["assist_loop"]["conversation_state"] == "awaiting_guidance"
    assert items[0]["assist_loop"]["guidance_requests"][0]["channel"] == "onlineai"
    assert (
        items[0]["assist_loop"]["onlineai_guidance"][0]["summary"]
        == "Prefer CodeintelQueryService.answer_endpoint_handler_lookup"
    )
    assert items[0]["repair_loop"]["recovery_submission_reason"] == "RECOVERY_TASK_CREATED"
    assert items[0]["repair_loop"]["retry_history"][0]["reason_code"] == "CANDIDATE_SYNTAX_INVALID"
    assert items[0]["escalation_packet"]["question_kind"] == "grounding_confirmation"
    assert items[0]["ai_operator"]["retry_actions"][0]["kind"] == "quality_gate_retry"
    assert items[0]["ai_operator"]["retry_actions"][0]["budget_key"] == "parse_failure"
    assert items[0]["refs"]["outcome_policy_trace_ref"] == "ayrpack://packet/pkt-outcome-1458"
    assert items[0]["control_plane_outcome"]["reason_code"] == "TARGET_LOGIC_NOT_CONFIRMED"
    assert (
        items[0]["control_plane_outcome"]["reason_summary"]
        == "planner exhausted bounded discovery without confirming a safe mutating path"
    )
    assert items[0]["control_plane_outcome"]["handoff_status"] == "needs_replan"
    assert items[0]["control_plane_outcome"]["failure_category"] == "bad_decomposition"
    assert items[0]["temporal"]["state_machine"]["state"] == "needs_review"
    assert items[0]["attestation"]["attester_role"] == "planner"
    assert items[0]["transition_record"]["new_state"] == "needs_review"
    assert items[0]["attestation"]["evidence_refs"][0]["owned_by"] == "PACK"
    assert items[0]["execution_digest"]["reason_code"] == "TARGET_LOGIC_NOT_CONFIRMED"
    assert items[0]["execution_digest"]["audit_authority_source"] == "temporal.state_machine.history"
    assert items[0]["execution_digest"]["artifact_projections"]["run_ledger"] == "projection"
    assert items[0]["execution_digest"]["artifact_projections"]["opsbus"] == "projection"
    assert items[0]["execution_digest"]["refs"]["run_ledger_ref"]
    assert items[0]["execution_digest"]["refs"]["run_ledger_latest_ref"]
    assert items[0]["execution_digest"]["mutation"]["tool_sequence_classification"] == "audit"
    assert items[0]["execution_digest"]["mutation"]["legacy_removed_tools_seen"] == ["edit_symbol"]
    assert items[0]["execution_digest"]["runtime"]["stage_current"]["stage"] == "verifier_entry"
    assert items[0]["execution_digest"]["runtime"]["phase_summary"]["current_phase"] == "closeout"
    assert items[0]["execution_digest"]["runtime"]["phase_summary"]["failed_phase"] == "mutation"
    assert (
        items[0]["execution_digest"]["runtime"]["phase_summary"]["failed_reason_code"] == "TARGET_LOGIC_NOT_CONFIRMED"
    )
    assert items[0]["execution_digest"]["runtime"]["phase_summary"]["phase_count"] == 5
    assert items[0]["minimal_run_outcome"]["schema_version"] == "aix.minimal_run_outcome.current"
    assert items[0]["minimal_run_outcome"]["repo_key"] == "hyeve-aix-service"
    assert items[0]["minimal_run_outcome"]["task_family_tags"] == [
        "phase15_minimal_run_outcome",
        "code_change_required",
        "phase0",
    ]
    assert items[0]["minimal_run_outcome"]["slice_count"] == 2
    assert items[0]["minimal_run_outcome"]["target_files"] == ["backend/indexing/runner.py"]


def test_persist_run_ledger_record_generates_runtime_memory_capsule(tmp_path: Path) -> None:
    outcome = persist_run_ledger_record(_sample_payload(), root=tmp_path)
    assert Path(outcome["capsule_ref"]).exists()
    capsule = get_latest_runtime_memory_capsule(task_id="task-1458", root=tmp_path / "memory_capsules")
    assert capsule is not None
    assert capsule["workhub_task"]["task_id"] == "task-1458"
    assert capsule["workhub_task"]["contract_summary"]["intent"] == "code_change_required"
    assert capsule["run_ledger_facts"]["failure_reason_code"] == "TARGET_LOGIC_NOT_CONFIRMED"
    assert capsule["persona_snapshot"]["name"] == "planner"
    assert capsule["semantic_grounding"].startswith("This slice must repair")


def test_build_run_ledger_summary_counts_failures_and_latest_items() -> None:
    success_payload = _sample_payload()
    success_payload["ok"] = True
    success_payload["final_status"] = "done"
    success_payload["diagnostic_outcome"] = {}
    success_payload["quality_gate"] = {"passed": True, "code": "QUALITY_OK", "message": "ok"}
    success_payload["planner"]["error"] = None
    success_payload["result"]["status"] = "completed"
    success_payload["result"]["error"] = {}
    success_payload["result"]["correlation_id"] = "corr-1459"
    success_payload["task_number"] = 1459
    success_payload["result"]["task_number"] = 1459
    success_payload["writeback"] = {"status": "completed", "opsbus_written": True, "workhub_written": True}
    success_payload["codeintel"] = {
        "policy_mode": "codeintel_first",
        "mode": "codeintel_first",
        "retrieval_mode": "codeintel_first",
        "confidence_score": 0.93,
        "latency_ms": 64.2,
        "fallback_reason_code": None,
    }

    summary = build_run_ledger_summary(
        [build_run_ledger_record(_sample_payload()), build_run_ledger_record(success_payload)],
        project_id="bh-migrated-7-aix",
    )
    assert summary["schema_version"] == "aix.run_ledger.summary.current"
    assert summary["total_runs"] == 2
    assert summary["success_count"] == 1
    assert summary["failure_count"] == 1
    assert summary["counts"]["by_category"]["diagnostic_blocked"] == 1
    assert summary["counts"]["by_category"]["success"] == 1
    assert summary["latest_record"]["correlation_id"] == "corr-1458"
    assert summary["latest_record"]["planner"]["planner_packet_summary"]["slice_id"] == "slice-1458-001"
    assert summary["latest_planner"]["planner_packet_summary"]["execution_lane"] == "mutation"
    assert summary["latest_failure"]["code"] == "TARGET_LOGIC_NOT_CONFIRMED"
    assert summary["codeintel_first"]["runs"] == 2
    assert summary["codeintel_first"]["hits"] == 1
    assert summary["codeintel_first"]["fallbacks"] == 1
    assert summary["codeintel_first"]["fallback_reasons"]["TARGET_LOGIC_NOT_CONFIRMED"] == 1
    assert summary["codeintel_first"]["confidence_distribution"]["low"] == 1
    assert summary["codeintel_first"]["confidence_distribution"]["high"] == 1


def test_build_run_ledger_record_projects_compact_planner_packet_summary() -> None:
    record = build_run_ledger_record(_sample_payload())

    planner_summary = record["planner"]["planner_packet_summary"]
    assert record["planner"]["contract_fingerprint"] == "abc123fingerprint"
    assert planner_summary == {
        "schema_name": "aix.planner.slice_packet.current",
        "schema_version": "1",
        "status": "ready",
        "slice_id": "slice-1458-001",
        "task_number": 1458,
        "task_intent": "code_change_required",
        "target_symbol": "RunnerService.execute",
        "target_files": ["backend/indexing/runner.py"],
        "validation_commands": ["pytest tests/test_runner.py -q"],
        "execution_lane": "mutation",
        "clarification_gaps": [],
        "metrics": {"hint_count": 1},
    }


def test_build_run_ledger_record_preserves_codeintel_query_identity_and_citations() -> None:
    record = build_run_ledger_record(_sample_payload())

    assert record["codeintel"]["query_identity"] == {
        "entity_query": "RunnerService.execute",
        "query_fingerprint": "fp-1458",
    }
    assert record["codeintel"]["citations"] == [
        {
            "repo_id": "repo-1",
            "file_path": "backend/indexing/runner.py",
            "start_line": 44,
            "end_line": 60,
            "snapshot_id": "snap-1458",
            "commit_sha": "commit-1458",
            "confidence": 0.42,
        }
    ]


def test_build_run_ledger_record_keeps_empty_codeintel_provenance_bundle_shape() -> None:
    payload = _sample_payload()
    payload["codeintel"]["query_identity"] = {}
    payload["codeintel"]["citations"] = []

    record = build_run_ledger_record(payload)

    assert record["codeintel"]["query_identity"] == {
        "entity_query": None,
        "query_fingerprint": None,
    }
    assert record["codeintel"]["citations"] == []


def test_build_run_ledger_record_projects_failure_path_planner_packet_summary() -> None:
    payload = _sample_payload()
    payload["planner"]["planner_packet_status"] = "ready_for_planning"
    payload["planner"]["planner_packet_metrics"] = {
        "status": "ready_for_planning",
        "strict_packet_present": False,
        "target_file_count": 2,
        "target_symbol_count": 1,
        "validation_command_count": 1,
    }
    payload["planner"]["planner_packet_clarification_gaps"] = ["grounded_symbol"]
    payload["planner"]["planner_packet"] = {
        "status": "ready_for_planning",
        "task_intent": "code_change_required",
        "admitted_target_files": [
            "services/hyeve-codeintel-service/backend/services/codeintel_query_service.py",
            "services/hyeve-codeintel-service/tests/test_codeintel_query_service.py",
        ],
        "admitted_symbol_targets": ["CodeintelQueryService.list_entities"],
        "ticket_contract_validation": {"missing_fields": ["grounded_symbol"]},
        "ticket_contract_current": {
            "execution_slice": {
                "validation_commands": [
                    "python -m pytest services/hyeve-codeintel-service/tests/test_codeintel_query_service.py -q --tb=short -k entities"
                ]
            }
        },
    }

    record = build_run_ledger_record(payload)

    planner_summary = record["planner"]["planner_packet_summary"]
    assert planner_summary["status"] == "ready_for_planning"
    assert planner_summary["target_symbol"] == "CodeintelQueryService.list_entities"
    assert planner_summary["target_files"] == [
        "services/hyeve-codeintel-service/backend/services/codeintel_query_service.py",
        "services/hyeve-codeintel-service/tests/test_codeintel_query_service.py",
    ]
    assert planner_summary["validation_commands"] == [
        "python -m pytest services/hyeve-codeintel-service/tests/test_codeintel_query_service.py -q --tb=short -k entities"
    ]
    assert planner_summary["clarification_gaps"] == ["grounded_symbol"]


def test_build_run_ledger_record_projects_enriched_retry_history() -> None:
    payload = _sample_payload()
    payload["repair_loop"]["planner_attempts_total"] = 4
    payload["repair_loop"]["retry_attempts_recorded"] = 3
    payload["repair_loop"]["retry_history"] = [
        {
            "stage": "planning",
            "reason_code": "EDIT_FILE_SEARCH_TEXT_NOT_FOUND",
            "budget_key": "parse_failure",
            "attempt_index": 2,
            "failure_detail": "EDIT_FILE_SEARCH_TEXT_NOT_FOUND:{...}",
            "provider": "openrouter",
            "model": "openrouter/mistralai/codestral-2508",
            "lane": "mutation_repair",
            "trace_ref": "/app/_runlogs/planner-traces/c002.json",
            "step_count": 4,
            "mutating_step_present": True,
            "context_source": "repair_live_excerpt",
            "codeintel_confidence_score": 0.35,
            "codeintel_fallback_reason_code": "CODEINTEL_LOW_CONFIDENCE",
        }
    ]

    record = build_run_ledger_record(payload)

    assert record["repair_loop"]["planner_attempts_total"] == 4
    assert record["repair_loop"]["retry_attempts_recorded"] == 3
    assert record["repair_loop"]["retry_history"][0]["attempt_index"] == 2
    assert record["repair_loop"]["retry_history"][0]["provider"] == "openrouter"
    assert record["repair_loop"]["retry_history"][0]["mutating_step_present"] is True
    assert record["repair_loop"]["retry_history"][0]["codeintel_confidence_score"] == 0.35


def test_build_run_ledger_record_prefers_final_evidence_repair_loop_for_minimal_outcome() -> None:
    payload = _sample_payload()
    payload["repair_loop"] = {
        "failure_code": "MUTATION_REQUIRED_BUT_ABSENT",
        "failure_class": "unknown",
        "policy_action": "escalate",
        "max_attempts": 0,
        "attempts_used": 0,
        "retryable": False,
        "same_turn_repair_active": False,
    }
    payload["result"]["evidence"]["repair_loop"] = {
        "failure_code": "MUTATION_REQUIRED_BUT_ABSENT",
        "failure_class": "mutation_missing",
        "policy_action": "retry",
        "budget_key": "mutation_missing",
        "max_attempts": 3,
        "attempts_used": 1,
        "retryable": True,
        "same_turn_repair_active": True,
    }

    record = build_run_ledger_record(payload)

    assert record["repair_loop"]["failure_class"] == "mutation_missing"
    assert record["repair_loop"]["policy_action"] == "retry"
    assert record["repair_loop"]["attempts_used"] == 1
    assert record["minimal_run_outcome"]["repair_taken"]["failure_class"] == "mutation_missing"
    assert record["minimal_run_outcome"]["repair_taken"]["policy_action"] == "retry"
    assert record["minimal_run_outcome"]["repair_taken"]["attempts_used"] == 1
    assert record["minimal_run_outcome"]["repair_taken"]["same_turn_repair_active"] is True


def test_build_run_ledger_record_preserves_root_cause_when_retry_budget_exhausts() -> None:
    payload = _sample_payload()
    payload["final_status"] = "waiting_on_human"
    payload["control_plane_outcome"] = {
        "status": "waiting_on_human",
        "substate": "clarification_required",
        "reason_code": "MUTATION_REQUIRED_BUT_ABSENT",
        "reason_summary": (
            "RETRY_BUDGET_EXHAUSTED failure_code=MUTATION_REQUIRED_BUT_ABSENT "
            "budget_key=mutation_missing attempts_used=1 max_attempts=1"
        ),
        "handoff_status": "waiting_on_human",
        "failure_category": None,
        "phase": "final",
    }
    payload["quality_gate"] = {
        "passed": False,
        "code": "MUTATION_REQUIRED_BUT_ABSENT",
        "message": "edit_file_missing_semantic_mutation",
    }
    payload["repair_loop"] = {
        "failure_code": "RETRY_BUDGET_EXHAUSTED",
        "failure_class": "unknown",
        "policy_action": "escalate",
        "budget_key": "mutation_missing",
        "max_attempts": 1,
        "attempts_used": 1,
        "retryable": False,
        "same_turn_repair_active": True,
    }

    record = build_run_ledger_record(payload)

    assert record["failure_lineage"]["root_cause_code"] == "MUTATION_REQUIRED_BUT_ABSENT"
    assert record["failure_lineage"]["root_failure_family"] == "mutation_missing"
    assert record["failure_lineage"]["retry_context"]["envelope_code"] == "RETRY_BUDGET_EXHAUSTED"
    assert record["failure_lineage"]["retry_context"]["exhausted"] is True
    assert record["minimal_run_outcome"]["failure_class"] != "unknown"
    assert record["minimal_run_outcome"]["repair_taken"]["failure_code"] == "MUTATION_REQUIRED_BUT_ABSENT"
    assert record["minimal_run_outcome"]["repair_taken"]["failure_class"] == "mutation_missing"
    assert record["minimal_run_outcome"]["repair_taken"]["policy_action"] == "retry"
    assert record["minimal_run_outcome"]["repair_taken"]["retry_context"]["envelope_code"] == "RETRY_BUDGET_EXHAUSTED"


def test_to_compact_run_ledger_record_projects_mixed_stage_repair_timeline() -> None:
    payload = _sample_payload()
    payload["repair_loop"]["planner_attempts_total"] = 3
    payload["repair_loop"]["retry_attempts_recorded"] = 2
    payload["repair_loop"]["retry_history"] = [
        {
            "stage": "planning",
            "reason_code": "EDIT_FILE_SEARCH_TEXT_NOT_FOUND",
            "attempt_index": 2,
            "provider": "openrouter",
            "model": "openrouter/mistralai/codestral-2508",
            "lane": "mutation_repair",
            "trace_ref": "/app/_runlogs/planner-traces/c002.json",
            "mutating_step_present": True,
            "context_source": "repair_live_excerpt",
        },
        {
            "stage": "execution",
            "reason_code": "executor_terminal_step_failed",
            "attempt_index": 3,
            "provider": "openrouter",
            "model": "openrouter/mistralai/codestral-2508",
            "lane": "mutation_repair",
            "trace_ref": "/app/_runlogs/planner-traces/c003.json",
            "mutating_step_present": True,
            "context_source": "execution_failure_repair",
        },
    ]

    record = build_run_ledger_record(payload)
    compact = to_compact_run_ledger_record(record)

    assert compact["repair"]["planner_attempts_total"] == 3
    assert compact["repair"]["retry_attempts_recorded"] == 2
    assert compact["repair"]["timeline"][0]["stage"] == "planning"
    assert compact["repair"]["timeline"][0]["reason_code"] == "EDIT_FILE_SEARCH_TEXT_NOT_FOUND"
    assert compact["repair"]["timeline"][1]["stage"] == "execution"
    assert compact["repair"]["timeline"][1]["context_source"] == "execution_failure_repair"


def test_resolve_runlog_root_falls_back_to_cwd_when_repo_depth_is_shallow(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("backend.services.devexecutor_runlog_resolver_service.settings.AIX_RUNLOG_ROOT", str(tmp_path))
    resolved = _resolve_runlog_root()
    assert resolved == tmp_path


def test_build_task_fast_ledger_merges_invocations_and_runner_results() -> None:
    record = build_run_ledger_record(_sample_payload())
    journal = build_task_fast_ledger(
        project_id="bh-migrated-7-aix",
        task_number=1458,
        records=[record],
        invocations=[
            {
                "invocation_id": "runonce-1458",
                "status": "completed_with_failure",
                "accepted_at_utc": "2026-03-18T10:00:00+00:00",
                "started_at_utc": "2026-03-18T10:00:01+00:00",
                "finished_at_utc": "2026-03-18T10:00:10+00:00",
                "project_id": "bh-migrated-7-aix",
                "task_number": 1458,
                "stage_events": [
                    {
                        "timestamp_utc": "2026-03-18T10:00:02+00:00",
                        "stage": "devexecutor_dispatch",
                        "summary": "dispatching bounded slice into devexecutor runtime",
                        "details": {"step_count": 2},
                    }
                ],
                "result": {"final_status": "needs_review", "ok": False},
            }
        ],
    )
    assert journal["schema_version"] == "aix.fast_ledger.v2"
    assert journal["record_schema_version"] == "aix.ledger.core.current"
    assert journal["entry_count"] == 5
    runner_result = next(item for item in journal["items"] if item["kind"] == "runner.result")
    assert runner_result["event"]["attestation"]["attestation_type"] == "QUALITY_GATE"
    assert runner_result["event"]["attestation"]["verdict"] == "FAIL"
    assert runner_result["event"]["transition_record"]["new_state"] == "needs_review"
    assert any(ref["ref_type"] == "execution_trace" for ref in runner_result["event"]["attestation"]["evidence_refs"])
    assert journal["latest"]["kind"] == "runner.result"
    assert journal["items"][0]["kind"] == "invocation.accepted"
    assert any(
        item["kind"] == "executor.stage" and item["stage"] == "devexecutor_dispatch" for item in journal["items"]
    )
    assert runner_result["event"]["schema_version"] == "aix.ledger.core.current"
    assert runner_result["event"]["workflow"]["handoff_status"] == "needs_replan"
    assert runner_result["event"]["contract"]["validation_plan"] == ["pytest tests/test_runner.py -q"]
    assert "ayrpack://packet/pkt-outcome-1458" in runner_result["event"]["evidence_refs"]
    assert "local://planner-traces/trace-1458.json" in runner_result["event"]["evidence_refs"]
    assert runner_result["event"]["minimal_run_outcome"]["schema_version"] == "aix.minimal_run_outcome.current"
    assert runner_result["event"]["minimal_run_outcome"]["repair_taken"]["policy_action"] == "replan"


def test_build_task_fast_ledger_merges_workhub_trace_bundle_entries() -> None:
    record = build_run_ledger_record(_sample_payload())
    journal = build_task_fast_ledger(
        project_id="bh-migrated-7-aix",
        task_number=1458,
        records=[record],
        invocations=[],
        task_trace_bundle={
            "task": {
                "id": "task-1458",
                "status": "needs_review",
                "created_at": "2026-03-18T09:59:00+00:00",
                "updated_at": "2026-03-18T10:00:30+00:00",
            },
            "evidence": [
                {
                    "evidence_type": "devexecutor_run",
                    "created_at": "2026-03-18T10:00:05+00:00",
                    "correlation_id": "corr-1458",
                    "evidence_json": {"summary": "run artifact attached", "evidence_ref": "run://1458"},
                }
            ],
            "verification": {
                "history": [
                    {
                        "author_id": "verifier-1",
                        "body": "verification requested",
                        "created_at": "2026-03-18T10:00:25+00:00",
                        "correlation_id": "corr-1458",
                        "metadata_json": {"event_type": "verification_evaluation"},
                    }
                ]
            },
            "runs": [
                {
                    "run_id": "run-1458",
                    "run": {
                        "id": "run-1458",
                        "status": "completed",
                        "started_at": "2026-03-18T10:00:06+00:00",
                        "completed_at": "2026-03-18T10:00:24+00:00",
                    },
                    "steps": [
                        {
                            "id": "step-1",
                            "step_name": "planner.accepted",
                            "status": "completed",
                            "created_at": "2026-03-18T10:00:07+00:00",
                        }
                    ],
                }
            ],
            "trace": {"correlation_ids": ["corr-1458"]},
        },
        transition_audit=[
            {
                "actor_sub": "runner",
                "request_id": "corr-1458",
                "created_at": "2026-03-18T10:00:26+00:00",
                "metadata_json": {"from_status": "running", "to_status": "needs_review"},
            }
        ],
    )
    kinds = [item["kind"] for item in journal["items"]]
    assert "workhub.task.created" in kinds
    assert "workhub.evidence.attached" in kinds
    assert "workhub.transition" in kinds
    assert "workhub.verification.comment" in kinds
    assert "opsbus.run.started" in kinds
    assert "opsbus.step" in kinds
    assert journal["trace"]["correlation_ids"] == ["corr-1458"]
    assert journal["latest"]["kind"] == "runner.result"


def test_build_task_fast_ledger_derives_runner_result_from_trace_bundle_when_records_are_missing() -> None:
    journal = build_task_fast_ledger(
        project_id="bh-migrated-7-aix",
        task_number=2093,
        records=[],
        invocations=[],
        task_trace_bundle={
            "task": {
                "id": "task-2093",
                "status": "blocked",
                "created_at": "2026-03-22T05:00:00+00:00",
                "updated_at": "2026-03-22T06:16:39+00:00",
            },
            "evidence": [
                {
                    "evidence_type": "review_validation",
                    "created_at": "2026-03-22T06:16:38+00:00",
                    "correlation_id": "corr-wh-runner-2093-7a58d529",
                    "evidence_json": {
                        "summary": "model_routing_decision",
                        "evidence_ref": "aix://runtime/aix-v2-runtime/corr-v2-runtime-2093-7a58d529",
                    },
                }
            ],
            "trace": {"correlation_ids": ["corr-wh-runner-2093-7a58d529"]},
        },
    )
    runner_result = next(item for item in journal["items"] if item["kind"] == "runner.result")
    assert runner_result["derived"] is True
    assert runner_result["derived_source"] == "workhub_trace_bundle"
    assert runner_result["status"] == "blocked"
    assert runner_result["summary"] == (
        "task blocked: canonical runner.result unavailable; derived from WorkHub trace bundle"
    )
    assert runner_result["minimal_run_outcome"]["correlation_id"] == "corr-wh-runner-2093-7a58d529"
    assert (
        runner_result["minimal_run_outcome"]["evidence_refs"][0]
        == "aix://runtime/aix-v2-runtime/corr-v2-runtime-2093-7a58d529"
    )
    assert runner_result["event"]["schema_version"] == "aix.ledger.core.current"
    assert runner_result["event"]["outcome"]["status"] == "blocked"
    assert journal["latest"]["kind"] == "runner.result"


def test_build_task_fast_ledger_surfaces_non_processed_invocation_reason() -> None:
    journal = build_task_fast_ledger(
        project_id="bh-migrated-3-hyeve-codeintel-service",
        task_number=136,
        records=[],
        invocations=[
            {
                "invocation_id": "runonce-136",
                "status": "completed_with_failure",
                "accepted_at_utc": "2026-03-19T13:22:31+00:00",
                "started_at_utc": "2026-03-19T13:22:31+00:00",
                "finished_at_utc": "2026-03-19T13:22:32+00:00",
                "project_id": "bh-migrated-3-hyeve-codeintel-service",
                "task_number": 136,
                "result": {
                    "processed": False,
                    "reason": "TASK_NOT_QUEUED",
                    "current_status": "blocked",
                    "target_task_number": 136,
                },
            }
        ],
    )

    latest = journal["latest"]
    assert latest["kind"] == "invocation.finished"
    assert latest["final_status"] == "not_queued"
    assert latest["error"] == "TASK_NOT_QUEUED"
    assert "current status: blocked" in latest["summary"]


def test_build_attestation_from_run_ledger_record_types_control_plane_refs() -> None:
    record = build_run_ledger_record(_sample_payload())

    attestation = build_attestation_from_run_ledger_record(record)

    ref_types = {item["ref_id"]: item for item in attestation["evidence_refs"]}
    assert ref_types["local://evidence/ref-1"]["owned_by"] == "PACK"
    assert ref_types["ayrpack://packet/pkt-outcome-1458"]["owned_by"] == "CONTROL_PLANE"
    assert ref_types["ayrpack://packet/pkt-outcome-1458"]["ref_type"] == "execution_trace"


def test_build_transition_record_from_run_ledger_record_uses_temporal_events() -> None:
    record = build_run_ledger_record(_sample_payload())

    transition = build_transition_record_from_run_ledger_record(record)

    assert transition["prior_state"] == "planning"
    assert transition["new_state"] == "needs_review"
    assert transition["handoff_status"] == "needs_replan"
    assert transition["attestation_id"] == record["attestation"]["attestation_id"]


def test_build_attestation_from_run_ledger_record_uses_verifier_role_for_successful_closure() -> None:
    payload = _sample_payload()
    payload["ok"] = True
    payload["final_status"] = "done"
    payload["diagnostic_outcome"] = {}
    payload["quality_gate"] = {"passed": True, "code": "QUALITY_OK", "message": "ok"}
    payload["result"]["status"] = "completed"
    payload["result"]["error"] = {}

    record = build_run_ledger_record(payload)

    assert record["attestation"]["attestation_type"] == "CLOSURE"
    assert record["attestation"]["attester_role"] == "verifier"
    assert record["attestation"]["verdict"] == "PASS"


def test_build_attestation_from_run_ledger_record_uses_coding_pack_for_unspecified_grounded_mutation() -> None:
    payload = _sample_payload()
    payload["task_intent"] = "unspecified"
    payload["ticket_contract_current"] = {
        "target_surface": {
            "target_files": ["scripts/wh.py"],
            "prompt_symbol_targets": [
                {
                    "relative_path": "scripts/wh.py",
                    "symbol": "scripts.wh._run_fire_promotion_preflight",
                }
            ],
            "owning_symbols": ["scripts.wh._run_fire_promotion_preflight"],
        }
    }
    payload["validation_contract"] = {
        "expected_commands": ["python -m pytest scripts/test_wh_run_fire.py -q --tb=short"],
        "target_files": ["scripts/wh.py"],
        "prompt_symbol_targets": [
            {
                "relative_path": "scripts/wh.py",
                "symbol": "scripts.wh._run_fire_promotion_preflight",
                "label": "scripts.wh._run_fire_promotion_preflight",
            }
        ],
    }

    record = build_run_ledger_record(payload)

    assert record["attestation"]["pack_id"] == "coding"


def test_build_attestation_from_run_ledger_record_preserves_coding_pack_for_unspecified_targeted_mutation() -> None:
    payload = _sample_payload()
    payload["task_intent"] = "unspecified"
    payload["task_packet"]["metadata"]["ticket_contract_current"] = {
        "target_surface": {
            "target_files": ["projects/AIX/hyeve-aix-service/backend/services/aix_run_ledger_service.py"],
        }
    }
    payload["validation_contract"] = {
        "target_files": ["projects/AIX/hyeve-aix-service/backend/services/aix_run_ledger_service.py"],
        "code_change_required": True,
    }

    record = build_run_ledger_record(payload)

    assert record["attestation"]["pack_id"] == "coding"


def test_build_attestation_from_run_ledger_record_honors_explicit_admission_pack_contract() -> None:
    payload = _sample_payload()
    payload["task_intent"] = "unspecified"
    payload["validation_contract"] = {
        "target_files": ["projects/AIX/hyeve-aix-service/backend/services/aix_run_ledger_service.py"],
        "code_change_required": False,
    }
    payload["admission"] = {
        "pack_contract": {
            "pack_id": "coding",
        }
    }

    record = build_run_ledger_record(payload)

    assert record["pack_contract"]["pack_id"] == "coding"
    assert record["attestation"]["pack_id"] == "coding"


def test_build_task_fast_ledger_uses_blocked_reason_in_summary() -> None:
    record = build_run_ledger_record(_sample_payload())
    record["final_status"] = "blocked"
    record["failure"]["summary"] = "writeback failed"
    record["control_plane_outcome"] = {
        "status": "blocked",
        "substate": "writeback_failed",
        "reason_code": "WORKHUB_EVIDENCE_WRITEBACK_FAILED",
        "reason_summary": None,
        "handoff_status": "blocked",
        "failure_category": "execution_failed",
        "blocked_reason": "workhub_evidence_writeback_failed",
        "phase": "final",
        "blocked_at": "2026-03-20T01:02:03+00:00",
        "remediation": ["restore WorkHub availability for evidence writes"],
        "next_best_actions": ["retry writeback after WorkHub recovery"],
    }
    journal = build_task_fast_ledger(
        project_id="bh-migrated-7-aix",
        task_number=1458,
        records=[record],
        invocations=[],
        expand=True,
    )
    assert journal["latest"]["summary"] == "task blocked: WORKHUB_EVIDENCE_WRITEBACK_FAILED"
    expanded = journal["items"][0]["expanded"]["control_plane_outcome"]
    assert expanded["blocked_at"] == "2026-03-20T01:02:03+00:00"
    assert expanded["remediation"] == ["restore WorkHub availability for evidence writes"]
    assert expanded["next_best_actions"] == ["retry writeback after WorkHub recovery"]


def test_build_task_fast_ledger_prefers_attestation_authorization_summary_when_contract_blocks_lifecycle() -> None:
    record = build_run_ledger_record(_sample_payload())
    record["attestation_authorization"] = {
        "authorized": False,
        "reason_code": "ATTESTATION_ROLE_UNAUTHORIZED",
        "reason_summary": "attester role 'planner' is not authorized for attestation type 'QUALITY_GATE'",
    }
    journal = build_task_fast_ledger(
        project_id="bh-migrated-7-aix",
        task_number=1458,
        records=[record],
        invocations=[],
    )
    assert (
        journal["latest"]["summary"]
        == "contract blocked lifecycle: attester role 'planner' is not authorized for attestation type 'QUALITY_GATE'"
    )


def test_to_compact_run_ledger_record_builds_authoritative_projection() -> None:
    record = build_run_ledger_record(_sample_payload())
    record["attestation_authorization"] = {"authorized": True, "reason_code": None, "reason_summary": None}
    compact = to_compact_run_ledger_record(record)
    assert compact["schema_version"] == "aix.ledger.core.current"
    assert compact["source_classification"] == "projection"
    assert compact["audit_authority_source"] == "temporal.state_machine.history"
    assert compact["audit_authority_classification"] == "source_of_truth"
    assert compact["run_ledger_classification"] == "projection"
    assert compact["opsbus_classification"] == "projection"
    assert compact["identity"]["tenant_id"] == "default"
    assert compact["identity"]["task_number"] == 1458
    assert compact["workflow"]["actor"] == "planner"
    assert compact["workflow"]["handoff_status"] == "needs_replan"
    assert compact["workflow"]["failure_category"] == "bad_decomposition"
    assert compact["contract"]["target_files"] == ["backend/indexing/runner.py"]
    assert compact["contract"]["target_symbols"] == ["RunnerService.execute"]
    assert compact["outcome"]["status"] == "needs_review"
    assert compact["outcome"]["reason_code"] == "TARGET_LOGIC_NOT_CONFIRMED"
    assert (
        compact["outcome"]["reason_summary"]
        == "planner exhausted bounded discovery without confirming a safe mutating path"
    )
    assert compact["transition_record"]["prior_state"] == "planning"
    assert compact["transition_record"]["new_state"] == "needs_review"
    assert compact["temporal"]["current_state"] == "needs_review"
    assert compact["temporal"]["prior_state"] == "planning"
    assert compact["temporal"]["new_state"] == "needs_review"
    assert compact["temporal"]["attestation_id"] == compact["attestation"]["attestation_id"]
    assert compact["temporal"]["latest_transition"]["reason_code"] == "TARGET_LOGIC_NOT_CONFIRMED"
    assert compact["attestation_authorization"]["authorized"] is True
    assert compact["execution_digest"]["reason_code"] == "TARGET_LOGIC_NOT_CONFIRMED"
    assert compact["outcome"]["execution_state"]["state"] == "awaiting_human"
    assert compact["minimal_run_outcome"]["schema_version"] == "aix.minimal_run_outcome.current"
    assert compact["minimal_run_outcome"]["audit_authority_source"] == "temporal.state_machine.history"
    assert compact["minimal_run_outcome"]["run_ledger_classification"] == "projection"
    assert compact["minimal_run_outcome"]["opsbus_classification"] == "projection"
    assert (
        compact["minimal_run_outcome"]["execution_state"]["audit_authority_source"] == "temporal.state_machine.history"
    )
    assert compact["minimal_run_outcome"]["execution_state"]["state"] == "awaiting_human"
    assert compact["minimal_run_outcome"]["failure_class"] == "discovery.miss"
    assert compact["minimal_run_outcome"]["repair_taken"]["failure_code"] == "TARGET_LOGIC_NOT_CONFIRMED"
    assert compact["minimal_run_outcome"]["slice_count"] == 2


def test_execution_digest_captures_failed_command_runtime_and_guidance(tmp_path: Path) -> None:
    payload = _sample_payload()
    payload["result"]["evidence"]["commands"] = [
        {
            "command": ["python", "-m", "pytest", "tests/test_runner.py", "-q"],
            "command_text": "python -m pytest tests/test_runner.py -q",
            "returncode": 1,
            "stderr": "traceback...\nAssertionError: bad output",
            "ok": False,
            "runtime": {"mode": "docker", "image": "hyeve-aix-devexecutor-runtime:latest", "container_required": True},
        }
    ]
    payload["result"]["evidence"]["tests"] = {
        "detected": True,
        "commands": list(payload["result"]["evidence"]["commands"]),
    }
    payload["result"]["evidence"]["terminal_failure"] = {
        "code": "executor_terminal_step_failed",
        "message": "command failed",
        "details": {
            "tool": "cmd.run",
            "command": ["python", "-m", "pytest", "tests/test_runner.py", "-q"],
            "stderr": "traceback...\nAssertionError: bad output",
        },
    }
    payload["recovery_submission"] = {
        "submitted": True,
        "task_number": 1460,
        "reason": "RECOVERY_TASK_CREATED",
        "policy_action": "replan",
        "next_best_actions": ["narrow the execution slice before retrying"],
    }

    outcome = persist_run_ledger_record(payload, root=tmp_path)

    digest = outcome["record"]["execution_digest"]
    assert digest["failing_command"] == "python -m pytest tests/test_runner.py -q"
    assert digest["stderr_tail"] == "traceback...\nAssertionError: bad output"
    assert digest["runtime"]["runtime_mode"] == "docker"
    assert digest["runtime"]["runtime_image"] == "hyeve-aix-devexecutor-runtime:latest"
    assert digest["guidance"]["recovery_submission"]["task_number"] == 1460
    assert digest["guidance"]["next_best_actions"][0] == "narrow the execution slice before retrying"
    assert digest["validation"]["expected_commands"][0]["shell"] == "pytest tests/test_runner.py -q"
    assert digest["refs"]["run_ledger_ref"] == outcome["ref"]


def test_execution_digest_uses_stdout_tail_when_failed_command_stderr_empty(tmp_path: Path) -> None:
    payload = _sample_payload()
    payload["result"]["evidence"]["commands"] = [
        {
            "command": ["python", "-m", "pytest", "tests/test_runner.py", "-q", "--tb=short"],
            "command_text": "python -m pytest tests/test_runner.py -q --tb=short",
            "returncode": 1,
            "stderr": "",
            "stdout": "FAILED test_runner.py::test_case\nTypeError: run_command() got an unexpected keyword argument",
            "ok": False,
        }
    ]
    payload["result"]["evidence"]["terminal_failure"] = {
        "code": "executor_terminal_step_failed",
        "message": "tool step failed: cmd.run",
        "details": {
            "tool": "cmd.run",
            "command": ["python", "-m", "pytest", "tests/test_runner.py", "-q", "--tb=short"],
            "stderr": "",
            "stdout": "FAILED test_runner.py::test_case\nTypeError: run_command() got an unexpected keyword argument",
        },
    }

    outcome = persist_run_ledger_record(payload, root=tmp_path)

    digest = outcome["record"]["execution_digest"]
    assert digest["stderr_tail"] == (
        "FAILED test_runner.py::test_case\nTypeError: run_command() got an unexpected keyword argument"
    )


def test_execution_digest_projects_packet_and_mutation_truth(tmp_path: Path) -> None:
    payload = _sample_payload()
    fresh_timestamp = datetime.now(UTC).isoformat()
    payload["task_packet"] = {
        "task_id": "task-1458",
        "task_number": 1458,
        "project_id": "bh-migrated-7-aix",
        "metadata": {
            "ayrpack": {
                "meta": {"schema_version": "ayrpack.current", "trace_id": "trace-1458", "producer": "test"},
                "header": {"packet_type": "ayrpack", "packet_version": "v1"},
                "contract_ref": {
                    "kind": "task_contract",
                    "ref": "local://contracts/task-1458/v3.json",
                    "version": "v3",
                    "summary": "task contract v3",
                },
                "active_frame": {
                    "frame_id": "frame-1458",
                    "kind": "execution",
                    "phase": "verification",
                    "status": "blocked",
                    "updated_at_utc": fresh_timestamp,
                    "summary": "execution capability attestation failed",
                    "execution_capability_attestation": {
                        "passed": False,
                        "reason_code": "SYMBOL_RESOLVER_UNAVAILABLE",
                        "reason_summary": "symbol resolver unavailable",
                        "failed_checks": [{"code": "symbol_resolver_available"}],
                    },
                },
                "spine": [
                    {
                        "entry_id": "spine-1",
                        "index": 0,
                        "kind": "decision",
                        "timestamp_utc": "2026-03-25T10:00:00+00:00",
                        "phase": "execution",
                        "status": "blocked",
                        "actor": {"kind": "system", "id": "runner"},
                        "title": "internal fallback",
                        "summary": "fallback edit primitive used",
                        "details": {
                            "primitive": "replace_range",
                            "reason": "edit_file missing trailing newline fallback",
                            "scope": "scripts/wh.py",
                        },
                    }
                ],
            }
        },
    }
    payload["quality_gate"] = {
        "passed": False,
        "code": "QUALITY_GATE_REJECTED",
        "message": "post-apply semantic validation failed",
    }
    payload["result"]["evidence"]["proposal_artifacts"] = {
        "changed_files": ["scripts/wh.py"],
        "diff_ref": "local://diff.patch",
        "changed_files_ref": "local://changed.json",
    }
    payload["result"]["evidence"]["transcript"] = [
        {
            "tool": "edit_file",
            "ok": False,
            "result": {
                "changed_files": ["scripts/wh.py"],
                "semantic_precheck_passed": True,
                "semantic_precheck_output": "",
                "pyright_passed": False,
                "pyright_output": "scripts/wh.py:12: error: Incompatible return value type",
                "post_apply_validation_scope": "touched_files",
                "post_apply_validation_targets": ["scripts/wh.py"],
                "post_apply_validation_results": [
                    {
                        "path": "scripts/wh.py",
                        "passed": False,
                        "error_count": 1,
                        "warning_count": 0,
                        "diagnostics": [{"message": "Incompatible return value type"}],
                    }
                ],
            },
        },
        {"tool": "cmd.run", "ok": False, "result": {}},
    ]

    outcome = persist_run_ledger_record(payload, root=tmp_path)

    digest = outcome["record"]["execution_digest"]
    assert digest["digest_classification"] == "projection"
    assert digest["task"]["contract_version"] == "v3"
    assert digest["lifecycle"]["current_state"] == "blocked"
    assert digest["lifecycle"]["active_frame_summary"] == "execution capability attestation failed"
    assert digest["mutation"]["chosen_mutation_path"] == "edit_file"
    assert digest["mutation"]["tool_sequence_classification"] == "audit"
    assert digest["mutation"]["legacy_removed_tools_seen"] == []
    assert digest["mutation"]["fallback_occurred"] is True
    assert digest["mutation"]["fallback_entries"][0]["primitive"] == "replace_range"
    assert digest["mutation"]["touched_files"] == ["scripts/wh.py"]
    assert digest["mutation"]["validator_rejection_code"] is None
    assert digest["attestation"]["passed"] is False
    assert digest["attestation"]["reason_code"] == "SYMBOL_RESOLVER_UNAVAILABLE"
    assert digest["precheck"]["passed"] is True
    assert digest["precheck"]["reason_code"] is None
    assert digest["postcheck"]["passed"] is False
    assert digest["postcheck"]["reason_code"] is None
    assert digest["postcheck"]["scope"] == "touched_files"
    assert digest["postcheck"]["targets"] == ["scripts/wh.py"]
    assert digest["final_gate"]["code"] == "QUALITY_GATE_REJECTED"
    assert digest["outcome"]["reason"]["code"] == "TARGET_LOGIC_NOT_CONFIRMED"


def test_execution_digest_projects_validator_rejection_and_precommit_gate_outcome(tmp_path: Path) -> None:
    payload = _sample_payload()
    payload["result"]["evidence"]["proposal_artifacts"] = {
        "changed_files": ["backend/services/example.py"],
        "diff_ref": "local://diff.patch",
        "changed_files_ref": "local://changed.json",
    }
    payload["result"]["evidence"]["transcript"] = [
        {
            "tool": "edit_file",
            "ok": False,
            "result": {
                "relative_path": "backend/services/example.py",
                "changed_files": [],
                "reason_code": "pyright_precheck_failed",
                "reason_summary": "semantic precheck rejected prospective edit",
                "semantic_precheck_passed": False,
                "semantic_precheck_output": "backend/services/example.py:4: error: IndentationError",
                "pyright_passed": None,
                "post_apply_validation_scope": None,
                "post_apply_validation_targets": [],
                "post_apply_validation_results": [],
            },
        }
    ]

    outcome = persist_run_ledger_record(payload, root=tmp_path)

    digest = outcome["record"]["execution_digest"]
    attempt = outcome["record"]["ai_operator"]["mutation_attempts"][0]
    assert attempt["tool"] == "edit_file"
    assert attempt["reason_code"] == "pyright_precheck_failed"
    assert attempt["semantic_precheck_passed"] is False
    assert digest["mutation"]["primary_mutation_tool"] == "edit_file"
    assert digest["mutation"]["validator_rejection_code"] == "pyright_precheck_failed"
    assert digest["mutation"]["validator_rejection_summary"] == "semantic precheck rejected prospective edit"
    assert digest["precheck"]["passed"] is False
    assert digest["precheck"]["reason_code"] == "pyright_precheck_failed"
    assert digest["precheck"]["output"] == "backend/services/example.py:4: error: IndentationError"
    assert digest["postcheck"]["passed"] is None
    assert digest["postcheck"]["reason_code"] is None


def test_to_compact_run_ledger_record_prefers_control_plane_outcome_for_attestation_authorization_failure() -> None:
    record = build_run_ledger_record(_sample_payload())
    record["attestation_authorization"] = {
        "authorized": False,
        "reason_code": "ATTESTATION_ROLE_UNAUTHORIZED",
        "reason_summary": "attester role 'planner' is not authorized for attestation type 'QUALITY_GATE'",
    }
    record["control_plane_outcome"] = {
        "status": "needs_review",
        "substate": "quality_gate_rejected",
        "reason_code": "TARGET_LOGIC_NOT_CONFIRMED",
        "reason_summary": "planner exhausted bounded discovery without confirming a safe mutating path",
        "handoff_status": "needs_replan",
        "failure_category": "bad_decomposition",
        "phase": "final",
        "blocked_reason": None,
    }

    compact = to_compact_run_ledger_record(record)

    assert compact["workflow"]["failure_category"] == "validation_failed"
    assert compact["outcome"]["reason_code"] == "ATTESTATION_ROLE_UNAUTHORIZED"
    assert (
        compact["outcome"]["reason_summary"]
        == "attester role 'planner' is not authorized for attestation type 'QUALITY_GATE'"
    )


def test_to_compact_run_ledger_record_uses_failure_fields_when_typed_contract_missing() -> None:
    record = build_run_ledger_record(_sample_payload())
    record.pop("transition_record", None)
    record.pop("control_plane_outcome", None)
    record["final_status"] = "blocked"
    record["failure"] = {
        "stage": "writeback",
        "code": "WORKHUB_EVIDENCE_WRITEBACK_FAILED",
        "category": "writeback_failure",
        "summary": "writeback failed while attaching evidence",
        "tool_class": "runtime",
    }
    compact = to_compact_run_ledger_record(record)
    assert compact["workflow"]["handoff_status"] == "blocked"
    assert compact["workflow"]["failure_category"] == "execution_failed"
    assert compact["outcome"]["status"] == "blocked"
    assert compact["outcome"]["reason_code"] == "WORKHUB_EVIDENCE_WRITEBACK_FAILED"
    assert compact["outcome"]["reason_summary"] == "writeback failed while attaching evidence"


def test_to_compact_run_ledger_record_preserves_canonical_blocked_fields() -> None:
    record = build_run_ledger_record(_sample_payload())
    record["final_status"] = "blocked"
    record["control_plane_outcome"] = {
        "status": "blocked",
        "substate": "writeback_failed",
        "reason_code": "WORKHUB_EVIDENCE_WRITEBACK_FAILED",
        "reason_summary": "writeback failed while attaching evidence",
        "handoff_status": "blocked",
        "failure_category": "execution_failed",
        "blocked_reason": "workhub_evidence_writeback_failed",
        "phase": "final",
        "blocked_at": "2026-03-20T01:02:03+00:00",
        "remediation": ["restore WorkHub availability for evidence writes"],
        "next_best_actions": ["retry writeback after WorkHub recovery"],
    }

    compact = to_compact_run_ledger_record(record)

    assert compact["outcome"]["blocked_reason"] == "workhub_evidence_writeback_failed"
    assert compact["outcome"]["blocked_at"] == "2026-03-20T01:02:03+00:00"
    assert compact["outcome"]["remediation"] == ["restore WorkHub availability for evidence writes"]
    assert compact["outcome"]["next_best_actions"] == ["retry writeback after WorkHub recovery"]


def test_build_task_fast_ledger_prefers_typed_reason_summary_but_falls_back_to_reason_code() -> None:
    record = build_run_ledger_record(_sample_payload())
    record["final_status"] = "waiting_on_human"
    record["control_plane_outcome"] = {
        "status": "waiting_on_human",
        "substate": "clarification_required",
        "reason_code": "TASK_CLARIFICATION_REQUIRED",
        "reason_summary": None,
        "handoff_status": "waiting_on_human",
        "failure_category": "missing_input",
        "phase": "final",
        "blocked_reason": None,
    }
    record["failure"] = {
        "stage": "planning",
        "code": "TASK_CLARIFICATION_REQUIRED",
        "category": "diagnostic_blocked",
        "summary": "",
        "tool_class": "planner",
    }
    journal = build_task_fast_ledger(
        project_id="bh-migrated-7-aix",
        task_number=1458,
        records=[record],
        invocations=[],
    )
    assert journal["latest"]["summary"] == "waiting on human input: TASK_CLARIFICATION_REQUIRED"


def test_build_execution_state_projection_maps_review_and_clarification_and_blocked() -> None:
    review_state = build_execution_state_projection(_sample_payload())
    assert review_state["state"] == "awaiting_human"

    alias_review_payload = _sample_payload()
    alias_review_payload["control_plane_outcome"] = {
        "status": "submitted_for_review",
        "reason_code": "VERIFIER_OR_EVIDENCE_REQUIRED",
        "handoff_status": "submitted_for_review",
    }
    alias_review_state = build_execution_state_projection(alias_review_payload)
    assert alias_review_state["state"] == "awaiting_human"
    assert alias_review_state["source_status"] == "submitted_for_review"

    clarification_payload = _sample_payload()
    clarification_payload["final_status"] = "waiting_on_human"
    clarification_payload["control_plane_outcome"] = {
        "status": "waiting_on_human",
        "substate": "clarification_required",
        "reason_code": "TASK_CLARIFICATION_REQUIRED",
        "handoff_status": "waiting_on_human",
    }
    clarification_state = build_execution_state_projection(clarification_payload)
    assert clarification_state["state"] == "awaiting_clarification"

    blocked_payload = _sample_payload()
    blocked_payload["final_status"] = "blocked"
    blocked_payload["control_plane_outcome"] = {
        "status": "blocked",
        "substate": "writeback_failed",
        "reason_code": "WORKHUB_EVIDENCE_WRITEBACK_FAILED",
        "handoff_status": "blocked",
    }
    blocked_state = build_execution_state_projection(blocked_payload)
    assert blocked_state["state"] == "failed"


# WH-2315: spine retirement tests


def test_spine_not_present_in_compact_ledger_output() -> None:
    record = build_run_ledger_record(_sample_payload())
    compact = to_compact_run_ledger_record(record)
    assert "spine" not in compact, "compact ledger record must not expose spine field"


def test_audit_events_list_present_in_run_ledger() -> None:
    record = build_run_ledger_record(_sample_payload())
    assert "audit_events" in record, "run ledger record must contain audit_events field"
    assert isinstance(record["audit_events"], list), "audit_events must be a list (may be empty)"


def test_spine_retired_marker_in_execution_digest() -> None:
    record = build_run_ledger_record(_sample_payload())
    digest = record["execution_digest"]
    assert "spine_marker" not in digest["mutation"], "execution digest must not carry deprecated spine marker"


def test_build_run_ledger_record_falls_back_to_underlying_root_cause_when_quality_gate_is_retry_envelope() -> None:
    payload = _sample_payload()
    payload["final_status"] = "blocked"
    payload["control_plane_outcome"] = {
        "status": "blocked",
        "substate": "planner_retry_exhausted",
        "reason_code": "TASK_CLARIFICATION_REQUIRED",
        "reason_summary": "planner needs a missing anchor from the user",
        "handoff_status": "blocked",
        "failure_category": "missing_input",
        "phase": "final",
        "blocked_reason": None,
    }
    payload["quality_gate"] = {
        "passed": False,
        "code": "RETRY_BUDGET_EXHAUSTED",
        "message": "retry budget exhausted after clarification lane",
    }
    payload["diagnostic_outcome"] = {
        "status": "blocked",
        "reason_code": "TASK_CLARIFICATION_REQUIRED",
        "reason_summary": "planner needs a missing anchor from the user",
    }
    payload["result"] = {
        "status": "failed",
        "error": {
            "code": "TASK_CLARIFICATION_REQUIRED",
            "message": "planner needs a missing anchor from the user",
        },
        "evidence": {
            "repair_loop": {
                "failure_code": "RETRY_BUDGET_EXHAUSTED",
                "budget_key": "clarification_missing",
                "attempts_used": 2,
                "max_attempts": 2,
                "retryable": False,
                "same_turn_repair_active": False,
                "root_cause_code": "TASK_CLARIFICATION_REQUIRED",
            }
        },
    }

    record = build_run_ledger_record(payload)
    minimal = record["minimal_run_outcome"]
    lineage = record["failure_lineage"]

    assert lineage["root_cause_code"] == "TASK_CLARIFICATION_REQUIRED"
    assert lineage["retry_context"]["envelope_code"] == "RETRY_BUDGET_EXHAUSTED"
    assert minimal["failure_class"] == "missing_input"
    assert minimal["repair_taken"]["failure_code"] == "TASK_CLARIFICATION_REQUIRED"
    assert minimal["repair_taken"]["retry_context"]["budget_key"] == "clarification_missing"


def test_execution_digest_guidance_repair_preserves_root_cause_through_retry_envelope(tmp_path: Path) -> None:
    payload = _sample_payload()
    payload["final_status"] = "waiting_on_human"
    payload["control_plane_outcome"] = {
        "status": "waiting_on_human",
        "substate": "clarification_required",
        "reason_code": "MUTATION_REQUIRED_BUT_ABSENT",
        "reason_summary": "retry budget exhausted after mutation_missing lane",
        "handoff_status": "waiting_on_human",
        "failure_category": "bad_decomposition",
        "phase": "final",
    }
    payload["quality_gate"] = {
        "passed": False,
        "code": "MUTATION_REQUIRED_BUT_ABSENT",
        "message": "mutation was required but absent",
    }
    payload["repair_loop"] = {
        "failure_code": "RETRY_BUDGET_EXHAUSTED",
        "failure_class": "unknown",
        "policy_action": "escalate",
        "budget_key": "mutation_missing",
        "attempts_used": 1,
        "max_attempts": 1,
        "retryable": False,
        "same_turn_repair_active": True,
        "root_cause_code": "MUTATION_REQUIRED_BUT_ABSENT",
    }

    outcome = persist_run_ledger_record(payload, root=tmp_path)

    repair = outcome["record"]["execution_digest"]["guidance"]["repair"]
    assert repair["failure_code"] == "MUTATION_REQUIRED_BUT_ABSENT"
    assert repair["failure_class"] == "mutation_missing"
    assert repair["policy_action"] == "retry"
    assert repair["retry_context"]["envelope_code"] == "RETRY_BUDGET_EXHAUSTED"


def test_execution_digest_guidance_repair_falls_back_to_root_cause_when_quality_gate_only_has_retry_envelope(
    tmp_path: Path,
) -> None:
    payload = _sample_payload()
    payload["final_status"] = "blocked"
    payload["control_plane_outcome"] = {
        "status": "blocked",
        "substate": "planner_retry_exhausted",
        "reason_code": "TASK_CLARIFICATION_REQUIRED",
        "reason_summary": "planner needs a missing anchor from the user",
        "handoff_status": "blocked",
        "failure_category": "missing_input",
        "phase": "final",
    }
    payload["quality_gate"] = {
        "passed": False,
        "code": "RETRY_BUDGET_EXHAUSTED",
        "message": "retry budget exhausted after clarification lane",
    }
    payload["diagnostic_outcome"] = {
        "status": "blocked",
        "reason_code": "TASK_CLARIFICATION_REQUIRED",
        "reason_summary": "planner needs a missing anchor from the user",
    }
    payload["result"] = {
        "status": "failed",
        "error": {
            "code": "TASK_CLARIFICATION_REQUIRED",
            "message": "planner needs a missing anchor from the user",
        },
        "evidence": {
            "repair_loop": {
                "failure_code": "RETRY_BUDGET_EXHAUSTED",
                "failure_class": "unknown",
                "policy_action": "escalate",
                "budget_key": "clarification_missing",
                "attempts_used": 2,
                "max_attempts": 2,
                "retryable": False,
                "same_turn_repair_active": False,
                "root_cause_code": "TASK_CLARIFICATION_REQUIRED",
            }
        },
    }

    outcome = persist_run_ledger_record(payload, root=tmp_path)

    repair = outcome["record"]["execution_digest"]["guidance"]["repair"]
    assert repair["failure_code"] == "TASK_CLARIFICATION_REQUIRED"
    assert repair["failure_class"] == "missing_input"
    assert repair["policy_action"] == "escalate"
    assert repair["retry_context"]["budget_key"] == "clarification_missing"


def test_capacity_timeout_lineage_preserves_capacity_failure_family_through_retry_envelope(tmp_path: Path) -> None:
    payload = _sample_payload()
    payload["final_status"] = "blocked"
    payload["control_plane_outcome"] = {
        "status": "blocked",
        "substate": "planner_capacity_exhausted",
        "reason_code": "PLANNER_MODEL_CAPACITY_EXCEEDED",
        "reason_summary": "ordered planner candidates exhausted admitted latency budget",
        "handoff_status": "blocked",
        "failure_category": "execution_failure",
        "phase": "final",
    }
    payload["quality_gate"] = {
        "passed": False,
        "code": "RETRY_BUDGET_EXHAUSTED",
        "message": "retry budget exhausted after planner capacity lane",
    }
    payload["result"] = {
        "status": "failed",
        "error": {
            "code": "PLANNER_MODEL_CAPACITY_EXCEEDED",
            "message": "ordered planner candidates exhausted admitted latency budget",
        },
        "evidence": {
            "repair_loop": {
                "failure_code": "RETRY_BUDGET_EXHAUSTED",
                "failure_class": "unknown",
                "policy_action": "escalate",
                "budget_key": "planner_capacity",
                "attempts_used": 1,
                "max_attempts": 1,
                "retryable": False,
                "same_turn_repair_active": False,
                "root_cause_code": "PLANNER_MODEL_CAPACITY_EXCEEDED",
            }
        },
    }

    outcome = persist_run_ledger_record(payload, root=tmp_path)

    record = outcome["record"]
    minimal = record["minimal_run_outcome"]
    repair = record["execution_digest"]["guidance"]["repair"]

    assert record["failure_lineage"]["root_cause_code"] == "PLANNER_MODEL_CAPACITY_EXCEEDED"
    assert record["failure_lineage"]["root_failure_family"] == "capacity"
    assert minimal["failure_class"] == "capacity"
    assert minimal["repair_taken"]["failure_code"] == "PLANNER_MODEL_CAPACITY_EXCEEDED"
    assert minimal["repair_taken"]["failure_class"] == "capacity"
    assert repair["failure_code"] == "PLANNER_MODEL_CAPACITY_EXCEEDED"
    assert repair["failure_class"] == "capacity"
    assert repair["policy_action"] == "escalate"
    assert repair["retry_context"]["envelope_code"] == "RETRY_BUDGET_EXHAUSTED"


def test_to_compact_run_ledger_record_preserves_validation_plan_without_projecting_it_into_execution_commands() -> None:
    record = build_run_ledger_record(_sample_payload())
    record["ai_operator"]["validation_contract"] = {
        "expected_commands": ["attach evidence note only"],
        "target_files": ["backend/indexing/runner.py"],
    }
    record["ticket_contract_current"] = {
        "acceptance_criteria": ["keep reporting truthful"],
        "validation_plan": ["attach evidence note only"],
        "execution_slice": {"validation_commands": []},
    }

    compact = to_compact_run_ledger_record(record)

    assert compact["contract"]["validation_plan"] == ["attach evidence note only"]
    assert compact["contract"]["execution_slice"]["validation_commands"] == []
