"""W10: In-process intelligence-layer counters."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class IntelligenceMetrics:
    conversation_count: int = 0
    planner_activations: int = 0
    objective_detections: int = 0
    clarification_count: int = 0
    continue_count: int = 0
    execution_route_count: int = 0
    verification_route_count: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms_total: float = 0.0
    latency_samples: int = 0
    extras: dict[str, int] = field(default_factory=dict)

    def record_intent(self, intent: str) -> None:
        if intent == "conversation":
            self.conversation_count += 1
        elif intent == "objective":
            self.objective_detections += 1
        elif intent == "clarification":
            self.clarification_count += 1
        elif intent == "continue_objective":
            self.continue_count += 1

    def record_planner(self) -> None:
        self.planner_activations += 1

    def record_usage(self, usage: dict | None, *, latency_ms: float | None = None) -> None:
        if usage:
            self.prompt_tokens += int(usage.get("prompt_tokens") or 0)
            self.completion_tokens += int(usage.get("completion_tokens") or 0)
        if latency_ms is not None:
            self.latency_ms_total += float(latency_ms)
            self.latency_samples += 1

    def snapshot(self) -> dict[str, object]:
        avg_latency = (
            self.latency_ms_total / self.latency_samples if self.latency_samples else 0.0
        )
        planner = self.planner_activations
        chat = self.conversation_count + self.clarification_count
        # Rough savings signal: chat turns that did not activate planner
        return {
            "conversation_count": self.conversation_count,
            "clarification_count": self.clarification_count,
            "objective_detections": self.objective_detections,
            "continue_count": self.continue_count,
            "planner_activations": planner,
            "execution_route_count": self.execution_route_count,
            "verification_route_count": self.verification_route_count,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "avg_latency_ms": round(avg_latency, 2),
            "cost_savings_proxy": {
                "chat_turns_without_planner": max(chat - 0, 0),
                "planner_activation_rate": round(planner / max(chat + planner + self.objective_detections, 1), 4),
            },
        }


intelligence_metrics = IntelligenceMetrics()
