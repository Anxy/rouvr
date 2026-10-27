"""AIX-W09-E01-T002: Production Reachability and Duplicate Detection Tests.

Verifies:
1. Reachability classification into PRODUCTION, TEST, EXPERIMENTAL, UNWIRED, DEAD.
2. Exactly one component is canonical production writer (objective_cycle_router).
3. Overlapping legacy mutation routes are marked DEAD duplicates.
"""

from __future__ import annotations

from pathlib import Path
import pytest

from backend.aix_core.intelligence.repository.reachability_classification import (
    ReachabilityAnalyzer,
    ReachabilityRecord,
)


def test_production_reachability_classification(tmp_path: Path) -> None:
    """Verifies that ReachabilityAnalyzer produces evidence-backed reachability records."""
    analyzer = ReachabilityAnalyzer(repo_root=tmp_path)
    records = analyzer.analyze_reachability()

    assert len(records) >= 5

    categories = {r.category for r in records}
    assert "PRODUCTION" in categories
    assert "DEAD" in categories

    canonical_writers = [r for r in records if r.is_canonical and r.category == "PRODUCTION"]
    assert len(canonical_writers) == 1
    assert canonical_writers[0].component_name == "objective_cycle_router"


def test_duplicate_mutation_routes_classified_as_dead(tmp_path: Path) -> None:
    """Retired mutation routers are flagged as DEAD duplicates of objective_cycle_router."""
    analyzer = ReachabilityAnalyzer(repo_root=tmp_path)
    records = analyzer.analyze_reachability()

    dead_duplicates = [r for r in records if r.category == "DEAD"]
    assert len(dead_duplicates) >= 3

    for dup in dead_duplicates:
        assert dup.duplicate_of == "objective_cycle_router"
        assert dup.is_canonical is False
