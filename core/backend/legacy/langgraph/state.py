from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class LangGraphTraceEvent:
    node: str
    status: str
    detail: str | None = None


@dataclass
class LangGraphExecutionState:
    project_id: str
    task_number: int
    correlation_id: str
    routing_profile: str = "B"
    envelope_version: str = "v1"
    prompt_profile: str = "default"
    user_goal: str = ""
    model_policy: str = "allow_escalate"
    retry_count: int = 0
    fallback_retry_threshold: int = 2
    selected_provider: str | None = None
    failure_codes: list[str] = field(default_factory=list)
    fallback_reason: str | None = None
    context: dict[str, Any] = field(default_factory=dict)
    plan: dict[str, Any] = field(default_factory=dict)
    coder_output: dict[str, Any] = field(default_factory=dict)
    dispatch: dict[str, Any] = field(default_factory=dict)
    approval: dict[str, Any] = field(default_factory=dict)
    evidence: dict[str, Any] = field(default_factory=dict)
    transition: dict[str, Any] = field(default_factory=dict)
    blocked_reason: str | None = None
    trace: list[LangGraphTraceEvent] = field(default_factory=list)

    def mark(self, *, node: str, status: str, detail: str | None = None) -> None:
        self.trace.append(LangGraphTraceEvent(node=node, status=status, detail=detail))
