from __future__ import annotations

from typing import Any

from backend.clients.opsbus_run_logs_client import opsbus_run_logs_client
from backend.clients.workhub_task_evidence_client import workhub_task_evidence_client
from backend.legacy.langgraph.chain import LangGraphOrchestrationChain
from backend.legacy.langgraph.state import LangGraphExecutionState


async def execute_langgraph_orchestration(
    *,
    tenant_id: str,
    actor_id: str,
    state: LangGraphExecutionState,
    workhub_task_id: str | None = None,
    opsbus_run_id: str | None = None,
) -> dict[str, Any]:
    chain = LangGraphOrchestrationChain()
    resolved = chain.run(state)

    opsbus_written = False
    opsbus_error: str | None = None
    if opsbus_run_id and not resolved.blocked_reason:
        opsbus_written, opsbus_error = await opsbus_run_logs_client.log_model_usage(
            tenant_id=tenant_id,
            project_id=resolved.project_id,
            run_id=opsbus_run_id,
            correlation_id=resolved.correlation_id,
            actor_id=actor_id,
            usage_block={
                "langgraph": {
                    "routing_profile": resolved.routing_profile,
                    "prompt_profile": resolved.prompt_profile,
                    "trace": [event.__dict__ for event in resolved.trace],
                    "dispatch": resolved.dispatch,
                }
            },
        )

    workhub_written = False
    workhub_error: str | None = None
    if workhub_task_id and not resolved.blocked_reason:
        workhub_written, workhub_error = await workhub_task_evidence_client.add_model_routing_decision_evidence(
            tenant_id=tenant_id,
            project_id=resolved.project_id,
            task_id=workhub_task_id,
            correlation_id=resolved.correlation_id,
            actor_id=actor_id,
            decision_payload={
                "langgraph": {
                    "routing_profile": resolved.routing_profile,
                    "trace": [event.__dict__ for event in resolved.trace],
                    "dispatch": resolved.dispatch,
                    "transition": resolved.transition,
                }
            },
        )

    # C003: LangGraph may not mint canonical terminal success.
    from backend.aix_core.runtime.governance.completion_authority import adapter_non_canonical_report

    non_canonical = adapter_non_canonical_report(
        source="langgraph_orchestration",
        tenant_id=tenant_id,
        objective_id=str(workhub_task_id or f"{state.project_id}:{state.task_number}"),
        run_id=str(opsbus_run_id or state.correlation_id),
        claimed_status="completed" if not resolved.blocked_reason else "blocked",
        detail="Feature-flagged LangGraph write path is non-canonical until C004 unmount.",
    )

    return {
        "blocked": bool(resolved.blocked_reason),
        "blocked_reason": resolved.blocked_reason,
        "state": {
            "project_id": resolved.project_id,
            "task_number": resolved.task_number,
            "correlation_id": resolved.correlation_id,
            "routing_profile": resolved.routing_profile,
            "envelope_version": resolved.envelope_version,
            "prompt_profile": resolved.prompt_profile,
            "context": resolved.context,
            "plan": resolved.plan,
            "coder_output": resolved.coder_output,
            "dispatch": resolved.dispatch,
            "approval": resolved.approval,
            "evidence": resolved.evidence,
            "transition": resolved.transition,
            "trace": [event.__dict__ for event in resolved.trace],
        },
        "opsbus_written": opsbus_written,
        "opsbus_error": opsbus_error,
        "workhub_written": workhub_written,
        "workhub_error": workhub_error,
        "completion_authority": non_canonical.to_dict(),
    }
