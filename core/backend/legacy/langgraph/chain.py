from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .state import LangGraphExecutionState, LangGraphTraceEvent

StateGraph: Any = None
try:  # pragma: no cover - optional dependency
    from langgraph.graph import END
    from langgraph.graph import StateGraph as _RuntimeStateGraph

    StateGraph = _RuntimeStateGraph
except Exception:  # pragma: no cover
    END = "__end__"

PlannerFn = Callable[[LangGraphExecutionState], dict[str, Any]]
ContextFn = Callable[[LangGraphExecutionState], dict[str, Any]]
GateFn = Callable[[LangGraphExecutionState], tuple[bool, str | None]]
DispatchFn = Callable[[LangGraphExecutionState], dict[str, Any]]
CoderFn = Callable[[LangGraphExecutionState], dict[str, Any]]
EvidenceFn = Callable[[LangGraphExecutionState], dict[str, Any]]
TransitionFn = Callable[[LangGraphExecutionState], dict[str, Any]]

IMMEDIATE_FALLBACK_FAILURE_CODES = {
    "PATCH_HYGIENE_MID_FILE_IMPORT",
    "PATCH_HYGIENE_ROUTE_IN_LIFECYCLE_ZONE",
    "PRECHECK_ENVIRONMENT_FAILED",
}
RETRY_GATED_FALLBACK_FAILURE_CODES = {
    "SKIPPED_REQUIRED_VALIDATION",
    "ACCEPTANCE_MAPPING_MISSING",
    "ACCEPTANCE_PROOF_MISSING",
}


def _as_str_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _as_trace_events(value: Any) -> list[LangGraphTraceEvent]:
    if not isinstance(value, list):
        return []
    trace: list[LangGraphTraceEvent] = []
    for item in value:
        if isinstance(item, LangGraphTraceEvent):
            trace.append(item)
            continue
        if isinstance(item, dict):
            trace.append(
                LangGraphTraceEvent(
                    node=str(item.get("node") or "unknown"),
                    status=str(item.get("status") or "unknown"),
                    detail=str(item.get("detail")) if item.get("detail") is not None else None,
                )
            )
    return trace


def _default_context(state: LangGraphExecutionState) -> dict[str, Any]:
    return {
        "project_id": state.project_id,
        "task_number": state.task_number,
        "goal": state.user_goal,
    }


def _default_planner(state: LangGraphExecutionState) -> dict[str, Any]:
    return {
        "steps": [
            {"id": "context", "title": "Assemble context"},
            {"id": "plan", "title": "Draft execution plan"},
            {"id": "execute", "title": "Dispatch to coder"},
        ],
        "routing_profile": state.routing_profile,
    }


def _default_approval_gate(state: LangGraphExecutionState) -> tuple[bool, str | None]:
    approved = bool((state.approval or {}).get("approved"))
    if approved:
        return True, None
    return False, "WORKHUB_APPROVAL_REQUIRED"


def _default_dispatch(state: LangGraphExecutionState) -> dict[str, Any]:
    envelope = {
        "envelope_version": state.envelope_version,
        "prompt_profile": state.prompt_profile,
        "project_id": state.project_id,
        "task_number": state.task_number,
        "correlation_id": state.correlation_id,
        "routing_profile": state.routing_profile,
        "model_policy": state.model_policy,
        "retry_count": state.retry_count,
        "selected_provider": state.selected_provider,
        "plan": state.plan,
    }
    if state.fallback_reason:
        envelope["force_escalation"] = True
        envelope["escalation_reason"] = state.fallback_reason
        envelope["preferred_provider"] = "onlineai"
    return {"ok": True, "channel": "opsbus.dispatch", "envelope": envelope}


def _default_coder(state: LangGraphExecutionState) -> dict[str, Any]:
    return {
        "ok": True,
        "summary": "Coder step completed",
        "actions": ["edit_file", "write_file", "apply_patch", "cmd.run"],
    }


def _default_evidence(state: LangGraphExecutionState) -> dict[str, Any]:
    return {
        "ok": True,
        "workhub_binding": {
            "task_number": state.task_number,
            "correlation_id": state.correlation_id,
        },
    }


def _default_transition(state: LangGraphExecutionState) -> dict[str, Any]:
    if state.fallback_reason:
        return {
            "ok": True,
            "target_status": "running",
            "route": "executor_fallback",
            "fallback_reason": state.fallback_reason,
        }
    return {"ok": True, "target_status": "needs_review"}


def _should_trigger_executor_fallback(state: LangGraphExecutionState) -> tuple[bool, str | None]:
    if str(state.model_policy or "").strip().lower() != "allow_escalate":
        return False, None
    if str(state.selected_provider or "").strip().lower() == "onlineai":
        return False, None
    codes = {str(item or "").strip() for item in state.failure_codes if str(item or "").strip()}
    immediate = sorted(code for code in codes if code in IMMEDIATE_FALLBACK_FAILURE_CODES)
    if immediate:
        return True, immediate[0]
    retry_gated = sorted(code for code in codes if code in RETRY_GATED_FALLBACK_FAILURE_CODES)
    if retry_gated and int(state.retry_count or 0) >= int(state.fallback_retry_threshold or 0):
        return True, retry_gated[0]
    return False, None


def _apply_executor_fallback_dispatch(state: LangGraphExecutionState) -> None:
    if not isinstance(state.dispatch, dict):
        state.dispatch = {}
    envelope = state.dispatch.get("envelope")
    if not isinstance(envelope, dict):
        envelope = {}
        state.dispatch["envelope"] = envelope
    if state.fallback_reason:
        envelope["force_escalation"] = True
        envelope["escalation_reason"] = state.fallback_reason
        envelope["preferred_provider"] = "onlineai"


class LangGraphOrchestrationChain:
    def __init__(
        self,
        *,
        context_fn: ContextFn = _default_context,
        planner_fn: PlannerFn = _default_planner,
        approval_gate_fn: GateFn = _default_approval_gate,
        dispatch_fn: DispatchFn = _default_dispatch,
        coder_fn: CoderFn = _default_coder,
        evidence_fn: EvidenceFn = _default_evidence,
        transition_fn: TransitionFn = _default_transition,
    ) -> None:
        self._context_fn = context_fn
        self._planner_fn = planner_fn
        self._approval_gate_fn = approval_gate_fn
        self._dispatch_fn = dispatch_fn
        self._coder_fn = coder_fn
        self._evidence_fn = evidence_fn
        self._transition_fn = transition_fn

    def run(self, state: LangGraphExecutionState) -> LangGraphExecutionState:
        if StateGraph is None:
            return self._run_sequential(state)
        return self._run_with_langgraph(state)

    def _run_sequential(self, state: LangGraphExecutionState) -> LangGraphExecutionState:
        state.mark(node="DiscussIntent", status="ok")
        state.context = self._context_fn(state)
        state.mark(node="ContextAssemble", status="ok")

        state.plan = self._planner_fn(state)
        if not isinstance(state.plan.get("steps"), list) or not state.plan.get("steps"):
            state.blocked_reason = "PLAN_EMPTY"
            state.mark(node="PlanValidate", status="blocked", detail="PLAN_EMPTY")
            return state
        state.mark(node="PlanDraft", status="ok")
        state.mark(node="PlanValidate", status="ok")

        approved, reason = self._approval_gate_fn(state)
        if not approved:
            state.blocked_reason = reason or "APPROVAL_BLOCKED"
            state.mark(node="ApproveGate", status="blocked", detail=state.blocked_reason)
            return state
        state.mark(node="ApproveGate", status="ok")

        state.dispatch = self._dispatch_fn(state)
        state.mark(node="Dispatch", status="ok")

        state.coder_output = self._coder_fn(state)
        state.mark(node="CoderExecute", status="ok")

        state.evidence = self._evidence_fn(state)
        state.mark(node="Evidence", status="ok")

        should_fallback, reason = _should_trigger_executor_fallback(state)
        if should_fallback:
            state.fallback_reason = reason
            _apply_executor_fallback_dispatch(state)
            state.mark(node="ExecutorFallbackPolicy", status="reroute", detail=reason)
        else:
            state.mark(node="ExecutorFallbackPolicy", status="ok")

        state.transition = self._transition_fn(state)
        state.mark(node="Transition", status="ok")
        return state

    def _run_with_langgraph(self, state: LangGraphExecutionState) -> LangGraphExecutionState:
        assert StateGraph is not None
        graph = StateGraph(LangGraphExecutionState)

        def _discuss(input_state: LangGraphExecutionState) -> LangGraphExecutionState:
            input_state.mark(node="DiscussIntent", status="ok")
            return input_state

        def _context(input_state: LangGraphExecutionState) -> LangGraphExecutionState:
            input_state.context = self._context_fn(input_state)
            input_state.mark(node="ContextAssemble", status="ok")
            return input_state

        def _plan(input_state: LangGraphExecutionState) -> LangGraphExecutionState:
            input_state.plan = self._planner_fn(input_state)
            input_state.mark(node="PlanDraft", status="ok")
            return input_state

        def _validate(input_state: LangGraphExecutionState) -> LangGraphExecutionState:
            if not isinstance(input_state.plan.get("steps"), list) or not input_state.plan.get("steps"):
                input_state.blocked_reason = "PLAN_EMPTY"
                input_state.mark(node="PlanValidate", status="blocked", detail="PLAN_EMPTY")
            else:
                input_state.mark(node="PlanValidate", status="ok")
            return input_state

        def _approve(input_state: LangGraphExecutionState) -> LangGraphExecutionState:
            approved, reason = self._approval_gate_fn(input_state)
            if approved:
                input_state.mark(node="ApproveGate", status="ok")
            else:
                input_state.blocked_reason = reason or "APPROVAL_BLOCKED"
                input_state.mark(node="ApproveGate", status="blocked", detail=input_state.blocked_reason)
            return input_state

        def _dispatch(input_state: LangGraphExecutionState) -> LangGraphExecutionState:
            input_state.dispatch = self._dispatch_fn(input_state)
            input_state.mark(node="Dispatch", status="ok")
            return input_state

        def _coder(input_state: LangGraphExecutionState) -> LangGraphExecutionState:
            input_state.coder_output = self._coder_fn(input_state)
            input_state.mark(node="CoderExecute", status="ok")
            return input_state

        def _evidence(input_state: LangGraphExecutionState) -> LangGraphExecutionState:
            input_state.evidence = self._evidence_fn(input_state)
            input_state.mark(node="Evidence", status="ok")
            return input_state

        def _fallback_policy(input_state: LangGraphExecutionState) -> LangGraphExecutionState:
            should_fallback, reason = _should_trigger_executor_fallback(input_state)
            if should_fallback:
                input_state.fallback_reason = reason
                _apply_executor_fallback_dispatch(input_state)
                input_state.mark(node="ExecutorFallbackPolicy", status="reroute", detail=reason)
            else:
                input_state.mark(node="ExecutorFallbackPolicy", status="ok")
            return input_state

        def _transition(input_state: LangGraphExecutionState) -> LangGraphExecutionState:
            input_state.transition = self._transition_fn(input_state)
            input_state.mark(node="Transition", status="ok")
            return input_state

        graph.add_node("DiscussIntent", _discuss)
        graph.add_node("ContextAssemble", _context)
        graph.add_node("PlanDraft", _plan)
        graph.add_node("PlanValidate", _validate)
        graph.add_node("ApproveGate", _approve)
        graph.add_node("Dispatch", _dispatch)
        graph.add_node("CoderExecute", _coder)
        graph.add_node("Evidence", _evidence)
        graph.add_node("ExecutorFallbackPolicy", _fallback_policy)
        graph.add_node("Transition", _transition)

        graph.set_entry_point("DiscussIntent")
        graph.add_edge("DiscussIntent", "ContextAssemble")
        graph.add_edge("ContextAssemble", "PlanDraft")
        graph.add_edge("PlanDraft", "PlanValidate")
        graph.add_edge("PlanValidate", "ApproveGate")
        graph.add_conditional_edges(
            "ApproveGate",
            lambda current: "stop" if current.blocked_reason else "continue",
            {"stop": END, "continue": "Dispatch"},
        )
        graph.add_edge("Dispatch", "CoderExecute")
        graph.add_edge("CoderExecute", "Evidence")
        graph.add_edge("Evidence", "ExecutorFallbackPolicy")
        graph.add_edge("ExecutorFallbackPolicy", "Transition")
        graph.add_edge("Transition", END)
        app = graph.compile()
        result = app.invoke(state)
        if isinstance(result, LangGraphExecutionState):
            return result
        if isinstance(result, dict):
            trace = _as_trace_events(result.get("trace"))
            return LangGraphExecutionState(
                project_id=str(result.get("project_id") or state.project_id),
                task_number=int(result.get("task_number") or state.task_number),
                correlation_id=str(result.get("correlation_id") or state.correlation_id),
                routing_profile=str(result.get("routing_profile") or state.routing_profile),
                envelope_version=str(result.get("envelope_version") or state.envelope_version),
                prompt_profile=str(result.get("prompt_profile") or state.prompt_profile),
                user_goal=str(result.get("user_goal") or state.user_goal),
                model_policy=str(result.get("model_policy") or state.model_policy),
                retry_count=int(result.get("retry_count") or state.retry_count),
                fallback_retry_threshold=int(result.get("fallback_retry_threshold") or state.fallback_retry_threshold),
                selected_provider=(
                    str(result.get("selected_provider"))
                    if result.get("selected_provider") is not None
                    else state.selected_provider
                ),
                failure_codes=[
                    str(item)
                    for item in (result.get("failure_codes") or state.failure_codes or [])
                    if str(item).strip()
                ],
                fallback_reason=(
                    str(result.get("fallback_reason"))
                    if result.get("fallback_reason") is not None
                    else state.fallback_reason
                ),
                context=_as_str_dict(result.get("context")),
                plan=_as_str_dict(result.get("plan")),
                coder_output=_as_str_dict(result.get("coder_output")),
                dispatch=_as_str_dict(result.get("dispatch")),
                approval=_as_str_dict(result.get("approval")),
                evidence=_as_str_dict(result.get("evidence")),
                transition=_as_str_dict(result.get("transition")),
                blocked_reason=str(result.get("blocked_reason")) if result.get("blocked_reason") is not None else None,
                trace=trace,
            )
        return state
