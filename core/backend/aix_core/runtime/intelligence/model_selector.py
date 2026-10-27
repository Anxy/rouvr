"""P15: Dynamic Model Selector.

Selects the optimal model for a task based on: complexity, context length,
cost tier, latency requirements, capability match, and failure history.
Integrates with the existing model registry and routing config.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

TaskComplexity = Literal["trivial", "simple", "moderate", "complex", "expert"]
CostTier = Literal["free", "low", "medium", "high", "premium"]


@dataclass(frozen=True)
class ModelCandidate:
    model_id: str
    provider: str
    cost_tier: CostTier
    context_window: int
    latency_class: Literal["fast", "standard", "slow"] = "standard"
    strengths: tuple[str, ...] = ()
    weaknesses: tuple[str, ...] = ()
    max_output_tokens: int = 4096
    supports_streaming: bool = True


@dataclass(frozen=True)
class TaskProfile:
    """Describes what a task needs from a model."""
    role: str  # planner, coder, tester, reviewer, verifier
    complexity: TaskComplexity
    estimated_context_tokens: int = 0
    estimated_output_tokens: int = 0
    requires_reasoning: bool = False
    requires_code_generation: bool = False
    requires_structured_output: bool = False
    max_cost_tier: CostTier = "high"
    max_latency_class: Literal["fast", "standard", "slow"] = "slow"
    previous_failures: tuple[str, ...] = ()


@dataclass(frozen=True)
class SelectionDecision:
    selected_model: ModelCandidate
    reason: str
    fallback_models: tuple[ModelCandidate, ...] = ()
    estimated_cost_usd: float = 0.0


# ── Default model catalog ────────────────────────────────────────────────────

_COST_ORDER = {"free": 0, "low": 1, "medium": 2, "high": 3, "premium": 4}
_LATENCY_ORDER = {"fast": 0, "standard": 1, "slow": 2}
_COMPLEXITY_TO_MIN_TIER: dict[TaskComplexity, CostTier] = {
    "trivial": "free",
    "simple": "low",
    "moderate": "medium",
    "complex": "high",
    "expert": "premium",
}


class ModelSelector:
    """Selects the best model for a task from available candidates."""

    def __init__(self, candidates: list[ModelCandidate] | None = None) -> None:
        self._candidates = list(candidates or [])

    def register(self, candidate: ModelCandidate) -> None:
        self._candidates.append(candidate)

    def select(self, profile: TaskProfile) -> SelectionDecision:
        if not self._candidates:
            raise ValueError("no model candidates registered")

        eligible = self._filter_eligible(profile)
        if not eligible:
            eligible = self._candidates  # fallback to all if none match

        ranked = self._rank(eligible, profile)
        best = ranked[0]
        fallbacks = tuple(ranked[1:3])

        return SelectionDecision(
            selected_model=best,
            reason=self._explain(best, profile),
            fallback_models=fallbacks,
        )

    def _filter_eligible(self, profile: TaskProfile) -> list[ModelCandidate]:
        eligible = []
        max_cost = _COST_ORDER.get(profile.max_cost_tier, 99)
        max_latency = _LATENCY_ORDER.get(profile.max_latency_class, 99)

        for c in self._candidates:
            if _COST_ORDER.get(c.cost_tier, 99) > max_cost:
                continue
            if _LATENCY_ORDER.get(c.latency_class, 99) > max_latency:
                continue
            if profile.estimated_context_tokens > c.context_window:
                continue
            if c.model_id in profile.previous_failures:
                continue
            eligible.append(c)
        return eligible

    def _rank(self, candidates: list[ModelCandidate], profile: TaskProfile) -> list[ModelCandidate]:
        min_tier = _COST_ORDER.get(_COMPLEXITY_TO_MIN_TIER.get(profile.complexity, "low"), 1)

        def score(c: ModelCandidate) -> tuple[int, int, int]:
            cost_score = _COST_ORDER.get(c.cost_tier, 2)
            match_score = 0
            if profile.requires_code_generation and "code" in c.strengths:
                match_score += 2
            if profile.requires_reasoning and "reasoning" in c.strengths:
                match_score += 2
            if profile.requires_structured_output and "structured" in c.strengths:
                match_score += 1
            adequacy = max(0, cost_score - min_tier)
            return (-match_score, adequacy, cost_score)

        return sorted(candidates, key=score)

    def _explain(self, model: ModelCandidate, profile: TaskProfile) -> str:
        parts = [f"selected {model.model_id} ({model.cost_tier} tier)"]
        parts.append(f"for {profile.complexity} {profile.role} task")
        if profile.requires_reasoning:
            parts.append("(reasoning required)")
        if profile.requires_code_generation:
            parts.append("(code generation)")
        return " ".join(parts)


__all__ = [
    "TaskComplexity",
    "CostTier",
    "ModelCandidate",
    "TaskProfile",
    "SelectionDecision",
    "ModelSelector",
]
