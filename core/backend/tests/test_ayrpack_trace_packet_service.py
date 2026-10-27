from __future__ import annotations

from backend.services.ayrpack_trace_packet_service import (
    TRACE_SCHEMA_NAME,
    build_model_gateway_trace_packet_payload,
    build_outcome_policy_trace_packet_payload,
    build_planner_trace_packet_payload,
)


def test_build_planner_trace_packet_payload_uses_typed_trace_contract() -> None:
    payload = build_planner_trace_packet_payload(
        packet_id="pkt-1",
        project_id="proj",
        correlation_id="corr",
        workspace_root="/workspace/hyeve",
        provider="slavx",
        model="openai/gpt-4.1-mini",
        prompt="plan it",
        attempts_used=2,
        raw_output_text='{"steps":[]}',
        parsed_output={"steps": []},
        normalized_steps=[{"tool": "get_status", "args": {}}],
    )
    assert payload["schema_name"] == TRACE_SCHEMA_NAME
    assert payload["payload_json"]["trace_kind"] == "planner_trace"
    assert payload["payload_json"]["context"]["workspace_root"] == "/workspace/hyeve"
    assert payload["payload_json"]["planner"]["provider"] == "slavx"
    assert payload["payload_json"]["planner"]["normalized_steps"][0]["tool"] == "get_status"
    assert payload["metadata_json"] == {
        "source": "aix.devexecutor_planner_bridge",
        "shadow_write": True,
    }


def test_build_model_gateway_trace_packet_payload_uses_typed_trace_contract() -> None:
    payload = build_model_gateway_trace_packet_payload(
        packet_id="pkt-2",
        project_id="proj",
        conversation_id="task-42",
        correlation_id="corr",
        actor_id="user-1",
        request_payload={"phase": "planner", "task_type": "code_change", "risk_level": "medium"},
        decision_payload={"decision_status": "approved", "selected_provider": "slavx"},
        usage_payload={"tokens_used": 12},
        redaction_payload={"external_calls_blocked": False},
        routing_payload={"route_class": "primary"},
        decision_record_id="decision-1",
    )
    assert payload["schema_name"] == TRACE_SCHEMA_NAME
    assert payload["lane"] == "model_gateway_planner"
    assert payload["payload_json"]["trace_kind"] == "model_gateway_decision"
    assert payload["payload_json"]["context"]["decision_record_id"] == "decision-1"
    assert payload["payload_json"]["request"]["task_type"] == "code_change"
    assert payload["payload_json"]["decision"]["selected_provider"] == "slavx"
    assert payload["payload_json"]["usage"]["tokens_used"] == 12
    assert payload["metadata_json"]["source"] == "aix.model_gateway.resolve"


def test_build_outcome_policy_trace_packet_payload_uses_typed_trace_contract() -> None:
    payload = build_outcome_policy_trace_packet_payload(
        packet_id="pkt-3",
        project_id="proj",
        conversation_id="1458",
        correlation_id="corr",
        actor_id="aix-runner",
        workspace_root="/workspace/hyeve",
        task_number=1458,
        task_intent="code_change_required",
        final_status="needs_review",
        raw_signal={"source": "quality_gate", "code": "NOOP_CODE_CHANGE"},
        normalized_signal={"source": "verifier.semantic_drift", "code": "NOOP_CODE_CHANGE"},
        classified_policy={"policy_action": "replan", "outcome_class": "mutation.incomplete"},
        decision_payload={"final_status": "needs_review"},
    )
    assert payload["schema_name"] == TRACE_SCHEMA_NAME
    assert payload["lane"] == "aix_outcome_policy_trace"
    assert payload["payload_json"]["trace_kind"] == "outcome_policy_trace"
    assert payload["payload_json"]["context"]["task_number"] == 1458
    assert payload["payload_json"]["raw_signal"]["code"] == "NOOP_CODE_CHANGE"
    assert payload["payload_json"]["classified_policy"]["policy_action"] == "replan"
    assert payload["metadata_json"]["source"] == "aix.v2_runtime"
