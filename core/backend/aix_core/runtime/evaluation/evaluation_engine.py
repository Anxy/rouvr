"""P23: Evaluation & Benchmarking Engine.

Independent evaluation system measuring objective success, false completion,
planner quality, verification accuracy, repair/replan success, capability
reliability, policy violations, human interventions, latency, and cost.

Supports versioned benchmark corpus and machine-readable contracts.
A change cannot be called an improvement without comparison against a stored baseline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(frozen=True)
class EvaluationMetricV1:
    name: str
    value: float
    unit: str = ""
    higher_is_better: bool = True


@dataclass(frozen=True)
class EvaluationCaseV1:
    case_id: str
    objective: str
    expected_outcome: str
    category: str = "general"
    difficulty: Literal["easy", "medium", "hard", "expert"] = "medium"
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class EvaluationResultV1:
    case_id: str
    verdict: Literal["PASS", "FAIL", "PARTIAL", "ERROR"]
    metrics: tuple[EvaluationMetricV1, ...] = ()
    details: dict[str, Any] = field(default_factory=dict)
    duration_ms: int = 0
    tokens_used: int = 0
    cost_usd: float = 0.0
    false_completion: bool = False
    policy_violations: int = 0
    human_interventions: int = 0


@dataclass(frozen=True)
class EvaluationRunV1:
    run_id: str
    version: str
    timestamp: str
    configuration: dict[str, Any] = field(default_factory=dict)
    results: tuple[EvaluationResultV1, ...] = ()
    baseline_run_id: str | None = None


@dataclass(frozen=True)
class BaselineV1:
    baseline_id: str
    version: str
    created_at: str
    metrics: dict[str, float] = field(default_factory=dict)
    run_id: str = ""


@dataclass(frozen=True)
class BaselineComparison:
    improved: tuple[str, ...] = ()
    regressed: tuple[str, ...] = ()
    unchanged: tuple[str, ...] = ()
    is_improvement: bool = False


class BenchmarkCorpus:
    """Versioned corpus of evaluation cases."""

    def __init__(self) -> None:
        self._cases: dict[str, EvaluationCaseV1] = {}
        self._version: str = "0.0.0"

    def add_case(self, case: EvaluationCaseV1) -> None:
        self._cases[case.case_id] = case

    def get_case(self, case_id: str) -> EvaluationCaseV1 | None:
        return self._cases.get(case_id)

    def list_cases(self, *, category: str | None = None, tag: str | None = None) -> list[EvaluationCaseV1]:
        cases = list(self._cases.values())
        if category:
            cases = [c for c in cases if c.category == category]
        if tag:
            cases = [c for c in cases if tag in c.tags]
        return cases

    @property
    def version(self) -> str:
        return self._version

    def set_version(self, v: str) -> None:
        self._version = v


class EvaluationEngine:
    """Runs evaluations and compares against baselines."""

    def __init__(self) -> None:
        self._runs: dict[str, EvaluationRunV1] = {}
        self._baselines: dict[str, BaselineV1] = {}

    def record_run(self, run: EvaluationRunV1) -> None:
        self._runs[run.run_id] = run

    def get_run(self, run_id: str) -> EvaluationRunV1 | None:
        return self._runs.get(run_id)

    def store_baseline(self, baseline: BaselineV1) -> None:
        self._baselines[baseline.baseline_id] = baseline

    def get_baseline(self, baseline_id: str) -> BaselineV1 | None:
        return self._baselines.get(baseline_id)

    def get_latest_baseline(self) -> BaselineV1 | None:
        if not self._baselines:
            return None
        return max(self._baselines.values(), key=lambda b: b.created_at)

    def aggregate_metrics(self, run: EvaluationRunV1) -> dict[str, float]:
        agg: dict[str, list[float]] = {}
        for r in run.results:
            for m in r.metrics:
                agg.setdefault(m.name, []).append(m.value)
        return {k: sum(v) / len(v) for k, v in agg.items()}

    def compare_to_baseline(
        self, run: EvaluationRunV1, baseline: BaselineV1,
        *, threshold: float = 0.0,
    ) -> BaselineComparison:
        run_metrics = self.aggregate_metrics(run)
        improved, regressed, unchanged = [], [], []
        for name, run_val in run_metrics.items():
            base_val = baseline.metrics.get(name)
            if base_val is None:
                unchanged.append(name)
                continue
            diff = run_val - base_val
            if diff > threshold:
                improved.append(name)
            elif diff < -threshold:
                regressed.append(name)
            else:
                unchanged.append(name)
        is_improvement = len(improved) > 0 and len(regressed) == 0
        return BaselineComparison(
            improved=tuple(improved), regressed=tuple(regressed),
            unchanged=tuple(unchanged), is_improvement=is_improvement,
        )


__all__ = [
    "EvaluationMetricV1", "EvaluationCaseV1", "EvaluationResultV1",
    "EvaluationRunV1", "BaselineV1", "BaselineComparison",
    "BenchmarkCorpus", "EvaluationEngine",
]
