"""Bounded planner evidence requests (AIX-PD-001).

Not a generic research language. The planner may only ask for the request types
parsed by `parse_evidence_requests`; this module fingerprints those asks and
supplies defaults when the model omitted `requests[]`.
"""
from __future__ import annotations

import hashlib
import json

_ALLOWED = frozenset(
    {
        "find_file",
        "find_symbol",
        "symbol_references",
        "related_tests",
        "related_routes",
        "read_file",
    }
)


def normalize_evidence_requests(requests: tuple[dict[str, str], ...] | list[dict[str, str]]) -> tuple[dict[str, str], ...]:
    out: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in requests or ():
        kind = str(item.get("type") or "").strip()
        query = str(item.get("query") or "").strip()
        if kind not in _ALLOWED or not query:
            continue
        key = (kind, query.casefold())
        if key in seen:
            continue
        seen.add(key)
        out.append({"type": kind, "query": query})
    return tuple(out)


def evidence_request_fingerprint(requests: tuple[dict[str, str], ...] | list[dict[str, str]]) -> str:
    normalized = [
        {"type": item["type"], "query": item["query"].casefold()}
        for item in normalize_evidence_requests(requests)
    ]
    raw = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def default_evidence_requests(*, reason: str, reply: str, objective: str) -> tuple[dict[str, str], ...]:
    """When the planner omits `requests[]`, still produce a bounded exploration ask."""
    query = (reply or objective or "related source").strip()[:160]
    if str(reason or "").startswith("TARGET_SCOPE_NOT_GROUNDED"):
        return normalize_evidence_requests(
            (
                {"type": "find_file", "query": query},
                {"type": "related_tests", "query": query},
            )
        )
    return normalize_evidence_requests(
        (
            {"type": "find_symbol", "query": query},
            {"type": "related_tests", "query": query},
        )
    )


_UNBOUNDED_EVIDENCE_MARKERS = (
    "all files",
    "all backend",
    "complete contents",
    "entire repository",
    "whole codebase",
    "complete codebase",
    "every file",
    "entire codebase",
    "all source",
    "whole repo",
    "repo-wide",
    "every module",
    "complete contents of all",
)


def query_looks_unbounded(query: str) -> bool:
    """True when a planner ask would search/read the whole tree (hangs plan-prep)."""
    blob = str(query or "").casefold()
    return any(marker in blob for marker in _UNBOUNDED_EVIDENCE_MARKERS)


def evidence_requests_are_unbounded(
    requests: tuple[dict[str, str], ...] | list[dict[str, str]],
) -> bool:
    return any(query_looks_unbounded(str(item.get("query") or "")) for item in requests or ())


__all__ = [
    "default_evidence_requests",
    "evidence_request_fingerprint",
    "evidence_requests_are_unbounded",
    "normalize_evidence_requests",
    "query_looks_unbounded",
]
