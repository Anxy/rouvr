"""AIX-W07-E01-T002: Impact-Aware Test Selection.

Selects mandatory unit test paths from repository graph impact rather than running generic pytest of the whole repo.
Escalates when the graph is stale.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from backend.aix_core.intelligence.repository.repository_intel.impact_query import (
    ImpactQueryRequest,
    build_impact_context,
)
from backend.aix_core.intelligence.repository.repository_intel.staleness import (
    RepositorySnapshot,
    StalenessError,
)
from backend.aix_core.intelligence.repository.repository_intel.symbol_index import (
    LanguageAwareSymbolIndex,
)


class ImpactTestSelectionError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


@dataclass
class ImpactTestSelection:
    source_files: list[str]
    selected_tests: list[str]
    is_staleness_escalated: bool = False
    pytest_command: str = ""


def select_impacted_tests(
    *,
    modified_sources: Sequence[str],
    symbol_index: LanguageAwareSymbolIndex,
    known_test_paths: Sequence[str],
    snapshot_current: RepositorySnapshot | None = None,
    snapshot_index_bound: RepositorySnapshot | None = None,
) -> ImpactTestSelection:
    """Selects test paths affected by modified source files. Escalates if graph snapshot is stale."""
    if snapshot_current and snapshot_index_bound:
        if snapshot_current.fingerprint != snapshot_index_bound.fingerprint:
            raise ImpactTestSelectionError("GRAPH_STALE_RETRY_OR_REINDEX", f"current={snapshot_current.fingerprint}")

    selected: set[str] = set()

    for src in modified_sources:
        # Check direct symbol linkage or test file match
        req = ImpactQueryRequest(symbol=Path(src).stem, max_files=10, include_tests=True)
        manifest = build_impact_context(
            request=req,
            symbol_index=symbol_index,
            test_paths=list(known_test_paths),
        )
        for item in manifest.items:
            path = item.get("path")
            if path and ("test_" in path or "_test." in path):
                selected.add(path)

    # Always include new test files directly provided in modified sources
    for src in modified_sources:
        if "test_" in src or "_test." in src:
            selected.add(src)

    sorted_tests = sorted(selected)
    cmd = f"python -m pytest {' '.join(sorted_tests)}" if sorted_tests else "python -m pytest"

    return ImpactTestSelection(
        source_files=list(modified_sources),
        selected_tests=sorted_tests,
        pytest_command=cmd,
    )


__all__ = [
    "ImpactTestSelectionError",
    "ImpactTestSelection",
    "select_impacted_tests",
]
