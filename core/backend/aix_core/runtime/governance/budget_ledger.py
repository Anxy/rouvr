"""P21: Resource & Cost Governance — persistent budget ledger.

Tracks token usage, LLM cost, compute time, tool calls, retries,
and concurrency at multiple scopes: tenant, project, objective, agent, step.
Complements the existing CostCircuitBreaker and CapacityGate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

BudgetScope = Literal["tenant", "project", "objective", "agent", "step", "capability"]


@dataclass
class BudgetLimitV1:
    scope: BudgetScope
    scope_id: str
    max_tokens: int | None = None
    max_cost_usd: float | None = None
    max_wall_seconds: float | None = None
    max_tool_calls: int | None = None
    max_retries: int | None = None
    max_concurrent: int | None = None


@dataclass
class BudgetUsageV1:
    scope: BudgetScope
    scope_id: str
    tokens_used: int = 0
    cost_usd: float = 0.0
    wall_seconds: float = 0.0
    tool_calls: int = 0
    retries: int = 0
    active_concurrent: int = 0


@dataclass(frozen=True)
class BudgetCheckResult:
    allowed: bool
    exhausted_dimension: str | None = None  # which limit was hit
    usage: BudgetUsageV1 | None = None
    limit: BudgetLimitV1 | None = None


class BudgetLedger:
    """Tracks resource usage against limits. In-memory; production targets Postgres."""

    def __init__(self) -> None:
        self._limits: dict[str, BudgetLimitV1] = {}  # keyed by scope:scope_id
        self._usage: dict[str, BudgetUsageV1] = {}

    def _key(self, scope: BudgetScope, scope_id: str) -> str:
        return f"{scope}:{scope_id}"

    def set_limit(self, limit: BudgetLimitV1) -> None:
        self._limits[self._key(limit.scope, limit.scope_id)] = limit

    def get_usage(self, scope: BudgetScope, scope_id: str) -> BudgetUsageV1:
        key = self._key(scope, scope_id)
        if key not in self._usage:
            self._usage[key] = BudgetUsageV1(scope=scope, scope_id=scope_id)
        return self._usage[key]

    def record(
        self, scope: BudgetScope, scope_id: str, *,
        tokens: int = 0, cost_usd: float = 0.0,
        wall_seconds: float = 0.0, tool_calls: int = 0,
        retries: int = 0,
    ) -> BudgetCheckResult:
        """Record usage and return whether budget allows continuation."""
        usage = self.get_usage(scope, scope_id)
        usage.tokens_used += tokens
        usage.cost_usd += cost_usd
        usage.wall_seconds += wall_seconds
        usage.tool_calls += tool_calls
        usage.retries += retries

        key = self._key(scope, scope_id)
        limit = self._limits.get(key)
        if limit is None:
            return BudgetCheckResult(allowed=True, usage=usage)

        return self._check(usage, limit)

    def check(self, scope: BudgetScope, scope_id: str) -> BudgetCheckResult:
        """Check budget without recording new usage."""
        usage = self.get_usage(scope, scope_id)
        key = self._key(scope, scope_id)
        limit = self._limits.get(key)
        if limit is None:
            return BudgetCheckResult(allowed=True, usage=usage)
        return self._check(usage, limit)

    def _check(self, usage: BudgetUsageV1, limit: BudgetLimitV1) -> BudgetCheckResult:
        checks = [
            ("tokens", usage.tokens_used, limit.max_tokens),
            ("cost_usd", usage.cost_usd, limit.max_cost_usd),
            ("wall_seconds", usage.wall_seconds, limit.max_wall_seconds),
            ("tool_calls", usage.tool_calls, limit.max_tool_calls),
            ("retries", usage.retries, limit.max_retries),
        ]
        for dim, used, cap in checks:
            if cap is not None and used >= cap:
                return BudgetCheckResult(
                    allowed=False, exhausted_dimension=dim,
                    usage=usage, limit=limit,
                )
        return BudgetCheckResult(allowed=True, usage=usage, limit=limit)

    def reset(self, scope: BudgetScope, scope_id: str) -> None:
        key = self._key(scope, scope_id)
        self._usage.pop(key, None)


__all__ = [
    "BudgetScope", "BudgetLimitV1", "BudgetUsageV1",
    "BudgetCheckResult", "BudgetLedger",
]
