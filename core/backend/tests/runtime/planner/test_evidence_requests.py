"""Bounded planner evidence requests — reject repo-wide hangs."""
from __future__ import annotations

from backend.aix_core.runtime.planner.evidence_requests import (
    evidence_requests_are_unbounded,
    query_looks_unbounded,
)


def test_query_looks_unbounded_detects_repo_wide_asks() -> None:
    assert query_looks_unbounded("complete contents of all files in backend")
    assert query_looks_unbounded("search the whole codebase for routers")
    assert not query_looks_unbounded("read backend/main.py health_check")


def test_evidence_requests_are_unbounded() -> None:
    assert evidence_requests_are_unbounded(
        ({"type": "find_file", "query": "all backend routers and every file"},)
    )
    assert not evidence_requests_are_unbounded(
        ({"type": "read_file", "query": "backend/main.py"},)
    )
