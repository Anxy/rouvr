"""Candidate strategies, impact/risk estimates, and mandatory comparison."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

StrategyKind = Literal[
    "minimal_local_edit",
    "extract_refactor",
    "rewrite_module",
    "config_only",
    "migration_backed",
    "other",
]


class StrategyAnalysisError(RuntimeError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class StrategyCandidate:
    strategy_id: str
    kind: StrategyKind
    summary: str
    affected_paths: tuple[str, ...] = ()
    estimated_files_touched: int = 0
    estimated_risk: Literal["LOW", "MEDIUM", "HIGH"] = "MEDIUM"
    reversible: bool = True
    requires_migration: bool = False
    rationale: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ImpactRiskEstimate:
    consumer_count: int
    blast_radius: Literal["LOCAL", "MODULE", "CROSS_MODULE"]
    risk: Literal["LOW", "MEDIUM", "HIGH"]
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class StrategyComparison:
    schema: str = "aix.strategy_comparison.v1"
    candidates: tuple[StrategyCandidate, ...] = ()
    selected_strategy_id: str | None = None
    selection_rationale: str = ""
    why_not_compared: str | None = None
    estimates: dict[str, dict[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "candidates": [c.to_dict() for c in self.candidates],
            "selected_strategy_id": self.selected_strategy_id,
            "selection_rationale": self.selection_rationale,
            "why_not_compared": self.why_not_compared,
            "estimates": self.estimates,
        }


def estimate_impact_risk(
    *,
    affected_paths: tuple[str, ...] | list[str],
    transitive_consumers: int = 0,
    requires_migration: bool = False,
) -> ImpactRiskEstimate:
    n_files = len(tuple(affected_paths))
    if requires_migration or transitive_consumers >= 5 or n_files >= 8:
        risk: Literal["LOW", "MEDIUM", "HIGH"] = "HIGH"
        radius: Literal["LOCAL", "MODULE", "CROSS_MODULE"] = "CROSS_MODULE"
    elif transitive_consumers >= 2 or n_files >= 3:
        risk = "MEDIUM"
        radius = "MODULE"
    else:
        risk = "LOW"
        radius = "LOCAL"
    return ImpactRiskEstimate(
        consumer_count=int(transitive_consumers),
        blast_radius=radius,
        risk=risk,
        notes=f"files={n_files}; consumers={transitive_consumers}; migration={requires_migration}",
    )


def compare_strategies(
    *,
    candidates: list[StrategyCandidate] | tuple[StrategyCandidate, ...],
    selected_strategy_id: str | None = None,
    selection_rationale: str = "",
    why_not_compared: str | None = None,
    consumer_counts: dict[str, int] | None = None,
) -> StrategyComparison:
    """DoD: complex work compares ≥2 feasible strategies or records why not."""
    cands = tuple(candidates)
    consumers = consumer_counts or {}
    if len(cands) < 2:
        if not why_not_compared or not why_not_compared.strip():
            raise StrategyAnalysisError(
                "STRATEGY_COMPARISON_REQUIRED",
                "provide at least two candidates or an explicit why_not_compared",
            )
        return StrategyComparison(
            candidates=cands,
            selected_strategy_id=selected_strategy_id or (cands[0].strategy_id if cands else None),
            selection_rationale=selection_rationale or "single-path work",
            why_not_compared=why_not_compared.strip(),
        )

    estimates: dict[str, dict[str, Any]] = {}
    for c in cands:
        est = estimate_impact_risk(
            affected_paths=c.affected_paths,
            transitive_consumers=int(consumers.get(c.strategy_id, 0)),
            requires_migration=c.requires_migration,
        )
        estimates[c.strategy_id] = est.to_dict()

    selected = selected_strategy_id
    if selected is None:
        # Prefer lowest risk then fewest files
        ranked = sorted(
            cands,
            key=lambda c: (
                {"LOW": 0, "MEDIUM": 1, "HIGH": 2}[estimates[c.strategy_id]["risk"]],
                c.estimated_files_touched or len(c.affected_paths),
            ),
        )
        selected = ranked[0].strategy_id
        selection_rationale = selection_rationale or (
            f"selected lowest estimated risk strategy {selected}"
        )
    elif selected not in {c.strategy_id for c in cands}:
        raise StrategyAnalysisError("UNKNOWN_STRATEGY", selected)
    elif not selection_rationale.strip():
        raise StrategyAnalysisError(
            "SELECTION_RATIONALE_REQUIRED",
            "explicit selection requires rationale",
        )

    return StrategyComparison(
        candidates=cands,
        selected_strategy_id=selected,
        selection_rationale=selection_rationale,
        why_not_compared=None,
        estimates=estimates,
    )
