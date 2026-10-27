"""AIX-E2E-CYCLE-001M-W12: soak and chaos testing framework.

Provides:
  1. Soak test scenario contract: configures duration, concurrency, and pass criteria.
  2. Chaos injection policy: what faults may be injected (allow-listed), which are
     forbidden (network call to live WorkHub, production deploy, etc.).
  3. Chaos scenario result: outcome + evidence refs for the soak/chaos run.

Chaos faults in scope (allowed):
  - kill_worker: terminate a worker process mid-step
  - network_partition: isolate a service from its dependencies for N seconds
  - db_slow: inject latency on DB queries
  - db_failover: trigger a PostgreSQL failover
  - msg_drop: drop a configured % of Memurai stream messages
  - budget_exhaust: inject a fake budget exhaustion signal
  - stop_the_line_inject: inject a StopTheLine signal mid-execution

Chaos faults that are NEVER injected in this programme:
  - production_mutation: any change to a production environment
  - cross_tenant_inject: injecting faults into another tenant's execution path
  - workhub_signal: sending signals to live WorkHub instances
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

ALLOWED_CHAOS_FAULTS: frozenset[str] = frozenset(
    {
        "kill_worker",
        "network_partition",
        "db_slow",
        "db_failover",
        "msg_drop",
        "budget_exhaust",
        "stop_the_line_inject",
    }
)

FORBIDDEN_CHAOS_FAULTS: frozenset[str] = frozenset(
    {
        "production_mutation",
        "cross_tenant_inject",
        "workhub_signal",
        "auto_accept",
        "production_deploy",
    }
)


class ChaosError(ValueError):
    pass


@dataclass(frozen=True)
class SoakScenarioV1:
    """Configuration for a soak test run."""
    scenario_id: str
    description: str
    duration_seconds: int       # Total soak duration
    target_concurrency: int     # Simultaneous cycle executions
    max_error_rate_pct: float   # Pass criterion: error rate must stay below this
    min_throughput_cycles: int  # Pass criterion: at least N cycles must complete
    chaos_faults: tuple[str, ...] = ()  # Faults to inject during soak

    def __post_init__(self) -> None:
        forbidden = frozenset(self.chaos_faults) & FORBIDDEN_CHAOS_FAULTS
        if forbidden:
            raise ChaosError(
                f"SoakScenario contains forbidden chaos faults: {sorted(forbidden)}. "
                "These faults are never permitted in AIX soak testing."
            )
        unknown = frozenset(self.chaos_faults) - ALLOWED_CHAOS_FAULTS
        if unknown:
            raise ChaosError(
                f"SoakScenario contains unknown chaos faults: {sorted(unknown)}. "
                f"Allowed: {sorted(ALLOWED_CHAOS_FAULTS)}"
            )


@dataclass(frozen=True)
class ChaosFaultInjectionV1:
    """A single chaos fault injection event."""
    fault_type: str
    target_service: str
    parameters: dict[str, Any]
    inject_at_second: int       # Seconds into the soak run

    def __post_init__(self) -> None:
        if self.fault_type in FORBIDDEN_CHAOS_FAULTS:
            raise ChaosError(
                f"Fault type {self.fault_type!r} is forbidden. "
                "Production, cross-tenant, and WorkHub faults are never permitted."
            )
        if self.fault_type not in ALLOWED_CHAOS_FAULTS:
            raise ChaosError(f"Unknown fault type: {self.fault_type!r}")


@dataclass(frozen=True)
class SoakResultV1:
    scenario_id: str
    passed: bool
    duration_seconds_actual: int
    cycles_completed: int
    error_rate_pct: float
    faults_injected: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    reason: str


class SoakAndChaosRunner:
    """Stub runner — real soak/chaos execution is done in a test environment, not in
    production. This class provides the validation and result contract; test
    infrastructure calls it with real instrumentation."""

    def validate_scenario(self, scenario: SoakScenarioV1) -> None:
        """Validate a soak scenario before running. Raises ChaosError on policy breach."""
        if scenario.duration_seconds <= 0:
            raise ChaosError("duration_seconds must be positive")
        if scenario.target_concurrency <= 0:
            raise ChaosError("target_concurrency must be positive")
        if not (0 <= scenario.max_error_rate_pct <= 100):
            raise ChaosError("max_error_rate_pct must be in [0, 100]")
        # Fault allowlist already validated in SoakScenarioV1.__post_init__

    def evaluate_result(
        self,
        *,
        scenario: SoakScenarioV1,
        cycles_completed: int,
        error_rate_pct: float,
        duration_seconds_actual: int,
        faults_injected: tuple[str, ...],
        evidence_refs: tuple[str, ...],
    ) -> SoakResultV1:
        passed = (
            cycles_completed >= scenario.min_throughput_cycles
            and error_rate_pct <= scenario.max_error_rate_pct
        )
        reason = (
            "Soak passed: throughput and error-rate criteria met."
            if passed
            else (
                f"Soak FAILED: cycles_completed={cycles_completed} "
                f"(min={scenario.min_throughput_cycles}), "
                f"error_rate={error_rate_pct:.1f}% "
                f"(max={scenario.max_error_rate_pct:.1f}%)"
            )
        )
        return SoakResultV1(
            scenario_id=scenario.scenario_id,
            passed=passed,
            duration_seconds_actual=duration_seconds_actual,
            cycles_completed=cycles_completed,
            error_rate_pct=error_rate_pct,
            faults_injected=faults_injected,
            evidence_refs=evidence_refs,
            reason=reason,
        )


__all__ = [
    "ALLOWED_CHAOS_FAULTS",
    "FORBIDDEN_CHAOS_FAULTS",
    "ChaosError",
    "SoakScenarioV1",
    "ChaosFaultInjectionV1",
    "SoakResultV1",
    "SoakAndChaosRunner",
]
