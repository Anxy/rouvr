"""AIX-E2E-CYCLE-001M-W13: aggregate cost circuit-breaker.

Monitors cumulative cost across a cycle and trips a circuit when any budget ceiling
is breached. Trip is irreversible within a cycle — once the circuit trips, the cycle
must be manually reviewed and a human must re-authorize budget extension before
resumption.

Circuit breaker dimensions:
  - cost_usd:     total cost in USD
  - tokens:       total token count (in + out)
  - time_seconds: wall-clock seconds elapsed
  - retries:      total retry attempts
  - steps:        total step count

Each dimension has:
  - A hard ceiling (from BudgetV1 on the ObjectiveEnvelopeV1).
  - A warning threshold (configurable; default 80% of ceiling).
  - A trip condition: consumed >= ceiling → TRIPPED.

Trip semantics:
  - Once tripped, `CostCircuitBreaker.state` transitions to TRIPPED permanently.
  - Resuming a tripped circuit requires calling `authorize_resumption(human_auth_token)`.
  - Calling `check(...)` on a TRIPPED circuit always returns a TRIPPED result (fail-closed).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal


class CircuitState(StrEnum):
    CLOSED = "CLOSED"       # Normal operation — no breach
    WARNING = "WARNING"     # Approaching ceiling — alert emitted
    TRIPPED = "TRIPPED"     # Ceiling breached — execution must halt


class CircuitBreakerError(Exception):
    pass


@dataclass(frozen=True)
class BudgetCeiling:
    """Mirrors `BudgetV1` but as a simple, dependency-free record so this module
    does not import from `contracts.py` and create a circular import."""
    cost_usd: float
    tokens: int
    time_seconds: int
    retries: int
    steps: int


@dataclass(frozen=True)
class ConsumedBudget:
    cost_usd: float = 0.0
    tokens: int = 0
    time_seconds: float = 0.0
    retries: int = 0
    steps: int = 0


@dataclass(frozen=True)
class CircuitCheckResultV1:
    state: CircuitState
    breached_dimensions: tuple[str, ...]
    warning_dimensions: tuple[str, ...]
    utilisation: dict[str, float]  # dimension → fraction (0.0–1.0+)
    reason: str


class CostCircuitBreaker:
    """Stateful aggregate cost circuit breaker for one execution cycle.

    Usage:
        cb = CostCircuitBreaker(ceiling=BudgetCeiling(...), warning_pct=0.80)
        result = cb.check(ConsumedBudget(...))
        if result.state == CircuitState.TRIPPED:
            # halt execution, await human re-authorization
    """

    def __init__(
        self,
        *,
        ceiling: BudgetCeiling,
        warning_pct: float = 0.80,
    ) -> None:
        if not (0 < warning_pct < 1.0):
            raise CircuitBreakerError("warning_pct must be in (0, 1)")
        self._ceiling = ceiling
        self._warning_pct = warning_pct
        self._state = CircuitState.CLOSED
        self._trip_reason: str = ""
        self._human_auth_token: str | None = None

    @property
    def state(self) -> CircuitState:
        return self._state

    def check(self, consumed: ConsumedBudget) -> CircuitCheckResultV1:
        """Evaluate consumed budget against ceiling. If already TRIPPED, returns
        TRIPPED immediately without re-evaluating (fail-closed)."""
        if self._state == CircuitState.TRIPPED:
            return CircuitCheckResultV1(
                state=CircuitState.TRIPPED,
                breached_dimensions=(),
                warning_dimensions=(),
                utilisation={},
                reason=f"Circuit is already TRIPPED: {self._trip_reason}",
            )

        dims: dict[str, tuple[float, float]] = {
            "cost_usd":     (float(consumed.cost_usd),     float(self._ceiling.cost_usd)),
            "tokens":       (float(consumed.tokens),       float(self._ceiling.tokens)),
            "time_seconds": (float(consumed.time_seconds), float(self._ceiling.time_seconds)),
            "retries":      (float(consumed.retries),      float(self._ceiling.retries)),
            "steps":        (float(consumed.steps),        float(self._ceiling.steps)),
        }

        breached: list[str] = []
        warning: list[str] = []
        utilisation: dict[str, float] = {}

        for dim, (used, ceiling) in dims.items():
            if ceiling <= 0:
                utilisation[dim] = 0.0
                continue
            frac = used / ceiling
            utilisation[dim] = round(frac, 4)
            if frac >= 1.0:
                breached.append(dim)
            elif frac >= self._warning_pct:
                warning.append(dim)

        if breached:
            reason = (
                f"Budget ceiling breached on: {breached}. "
                "Execution must halt. Human re-authorization required before resumption."
            )
            self._state = CircuitState.TRIPPED
            self._trip_reason = reason
            return CircuitCheckResultV1(
                state=CircuitState.TRIPPED,
                breached_dimensions=tuple(breached),
                warning_dimensions=tuple(warning),
                utilisation=utilisation,
                reason=reason,
            )

        if warning:
            self._state = CircuitState.WARNING
            return CircuitCheckResultV1(
                state=CircuitState.WARNING,
                breached_dimensions=(),
                warning_dimensions=tuple(warning),
                utilisation=utilisation,
                reason=f"Approaching budget ceiling on: {warning} (≥{self._warning_pct*100:.0f}%).",
            )

        self._state = CircuitState.CLOSED
        return CircuitCheckResultV1(
            state=CircuitState.CLOSED,
            breached_dimensions=(),
            warning_dimensions=(),
            utilisation=utilisation,
            reason="All budget dimensions within safe range.",
        )

    def authorize_resumption(self, human_auth_token: str) -> None:
        """Reset a TRIPPED circuit after human review and re-authorization.

        Raises:
            CircuitBreakerError: if not TRIPPED, or auth token is empty.
        """
        if self._state != CircuitState.TRIPPED:
            raise CircuitBreakerError(
                f"authorize_resumption called on a {self._state.value} circuit. "
                "Only TRIPPED circuits can be resumed."
            )
        if not human_auth_token or not human_auth_token.strip():
            raise CircuitBreakerError(
                "human_auth_token is required to resume a tripped circuit. "
                "Automated resumption is not permitted."
            )
        self._state = CircuitState.CLOSED
        self._trip_reason = ""
        self._human_auth_token = human_auth_token


__all__ = [
    "CircuitState",
    "CircuitBreakerError",
    "BudgetCeiling",
    "ConsumedBudget",
    "CircuitCheckResultV1",
    "CostCircuitBreaker",
]
