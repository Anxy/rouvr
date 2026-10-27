"""C041 strategy comparison tests."""

from __future__ import annotations

import pytest

from backend.aix_core.planning_strategy import (
    StrategyAnalysisError,
    StrategyCandidate,
    compare_strategies,
    estimate_impact_risk,
)


def test_estimate_impact_risk_scales_with_consumers() -> None:
    low = estimate_impact_risk(affected_paths=("a.py",), transitive_consumers=0)
    assert low.risk == "LOW"
    mid = estimate_impact_risk(affected_paths=("a.py", "b.py", "c.py"), transitive_consumers=2)
    assert mid.risk == "MEDIUM"
    high = estimate_impact_risk(
        affected_paths=("a.py",), transitive_consumers=0, requires_migration=True
    )
    assert high.risk == "HIGH"


def test_compare_two_strategies_selects_lower_risk() -> None:
    a = StrategyCandidate(
        strategy_id="s1",
        kind="minimal_local_edit",
        summary="edit one helper",
        affected_paths=("helper.py",),
        estimated_files_touched=1,
        estimated_risk="LOW",
    )
    b = StrategyCandidate(
        strategy_id="s2",
        kind="rewrite_module",
        summary="rewrite module",
        affected_paths=("mod.py", "a.py", "b.py", "c.py", "d.py", "e.py", "f.py", "g.py"),
        estimated_files_touched=8,
        estimated_risk="HIGH",
        requires_migration=True,
    )
    cmp = compare_strategies(candidates=[a, b], consumer_counts={"s1": 0, "s2": 6})
    assert cmp.why_not_compared is None
    assert cmp.selected_strategy_id == "s1"
    assert "s1" in cmp.estimates and "s2" in cmp.estimates
    assert cmp.estimates["s2"]["risk"] == "HIGH"


def test_single_strategy_requires_why_not_compared() -> None:
    only = StrategyCandidate(
        strategy_id="only",
        kind="config_only",
        summary="flip flag",
        affected_paths=("settings.yaml",),
    )
    with pytest.raises(StrategyAnalysisError) as exc:
        compare_strategies(candidates=[only])
    assert exc.value.code == "STRATEGY_COMPARISON_REQUIRED"
    cmp = compare_strategies(
        candidates=[only],
        why_not_compared="trivial config toggle; second strategy would be no-op",
    )
    assert cmp.why_not_compared is not None
    assert cmp.selected_strategy_id == "only"


def test_explicit_selection_requires_rationale() -> None:
    a = StrategyCandidate(strategy_id="a", kind="other", summary="a")
    b = StrategyCandidate(strategy_id="b", kind="other", summary="b")
    with pytest.raises(StrategyAnalysisError) as exc:
        compare_strategies(candidates=[a, b], selected_strategy_id="b", selection_rationale="")
    assert exc.value.code == "SELECTION_RATIONALE_REQUIRED"
    cmp = compare_strategies(
        candidates=[a, b],
        selected_strategy_id="b",
        selection_rationale="preserves public API surface",
    )
    assert cmp.selected_strategy_id == "b"
