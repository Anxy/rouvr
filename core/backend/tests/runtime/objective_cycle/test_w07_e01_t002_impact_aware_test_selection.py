"""AIX-W07-E01-T002: Impact-Aware Test Selection Tests.

Verifies:
1. Named source file plus new test file runs the specific new test files.
2. Graph staleness triggers explicit escalation with TestSelectionError.
3. Selective pytest command is generated over targeted test paths.
"""

from __future__ import annotations

from pathlib import Path
import pytest

from backend.aix_core.intelligence.repository.repository_intel.staleness import (
    RepositorySnapshot,
)
from backend.aix_core.intelligence.repository.repository_intel.symbol_index import (
    LanguageAwareSymbolIndex,
)
from backend.aix_core.runtime.verification.test_selection import (
    ImpactTestSelectionError,
    select_impacted_tests,
)


def test_impact_aware_test_selection(tmp_path: Path) -> None:
    """Verifies that modified source file and new test file selects target test paths."""
    idx = LanguageAwareSymbolIndex(root=tmp_path)
    idx.index_file("backend/routers/auth.py", "def login(): pass\n")

    known_tests = ["backend/tests/test_auth.py"]
    modified = ["backend/routers/auth.py", "backend/tests/test_new_feature.py"]

    selection = select_impacted_tests(
        modified_sources=modified,
        symbol_index=idx,
        known_test_paths=known_tests,
    )

    assert "backend/tests/test_new_feature.py" in selection.selected_tests
    assert "test_new_feature.py" in selection.pytest_command


def test_stale_graph_triggers_escalation(tmp_path: Path) -> None:
    """Mismatched graph snapshot triggers TestSelectionError('GRAPH_STALE_RETRY_OR_REINDEX')."""
    idx = LanguageAwareSymbolIndex(root=tmp_path)
    snap1 = RepositorySnapshot("snap-1", "fp-v1", str(tmp_path))
    snap2 = RepositorySnapshot("snap-1", "fp-v2", str(tmp_path))  # drifted!

    with pytest.raises(ImpactTestSelectionError) as exc_info:
        select_impacted_tests(
            modified_sources=["backend/main.py"],
            symbol_index=idx,
            known_test_paths=[],
            snapshot_current=snap2,
            snapshot_index_bound=snap1,
        )

    assert "GRAPH_STALE_RETRY_OR_REINDEX" in str(exc_info.value)
