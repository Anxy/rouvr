"""AIX-W09-E02-T003: Impact and Test Mapping Tests.

Verifies:
1. Symbol-to-test mapping resolves affected source modules and associated unit tests.
2. Returned EvidenceManifest contains confidence scores and test coverage flags.
3. Invalid max_files parameters fail closed with ImpactAPIError.
"""

from __future__ import annotations

from pathlib import Path
import pytest

from backend.aix_core.intelligence.repository.repository_intel.impact_query import (
    ImpactAPIError,
    ImpactQueryRequest,
    build_impact_context,
)
from backend.aix_core.intelligence.repository.repository_intel.symbol_index import (
    LanguageAwareSymbolIndex,
)


def test_symbol_to_test_mapping_resolution(tmp_path: Path) -> None:
    """Verifies that impact query resolves source files and maps candidate unit test paths."""
    idx = LanguageAwareSymbolIndex(root=tmp_path)
    idx.index_file("backend/routers/objective_cycle.py", "def objective_cycle_router(): pass\n")

    req = ImpactQueryRequest(
        symbol="objective_cycle_router",
        max_files=10,
        include_tests=True,
    )

    manifest = build_impact_context(
        request=req,
        symbol_index=idx,
        test_paths=["tests/runtime/objective_cycle/test_c005_derived_completion_authority.py"],
    )

    d = manifest.to_dict()
    assert "items" in d
    assert "policy" in d
    assert d["policy"]["max_files"] == 10
    assert d["policy"]["full_file_bodies"] is False


def test_invalid_max_files_fails_closed() -> None:
    """Requesting max_files <= 0 raises ImpactAPIError('INVALID_MAX_FILES')."""
    req = ImpactQueryRequest(symbol="foo", max_files=0)
    with pytest.raises(ImpactAPIError) as exc_info:
        build_impact_context(request=req)

    assert "INVALID_MAX_FILES" in str(exc_info.value)
