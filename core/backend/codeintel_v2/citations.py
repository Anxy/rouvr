"""Candidate-only citations for the CodeIntel v2 lane (Agent 4, Wave 4).

A ``Citation`` is a scored pointer to an exact ``(path, line_range,
snapshot_id, commit_sha)`` record. Citations never resolve, select, or rank
a file or symbol on the caller's behalf: this module exposes constructors,
validators, and serializers only. There is intentionally no function here
that returns a file or symbol for a caller to act on.

Snapshot/freshness shapes owned by parallel agents are stubbed locally as
``Protocol`` classes (``CodeIntelSnapshot``, ``Freshness``) matching the
frozen specs, so this module imports nothing from sibling agent code.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Literal, Mapping, Protocol, runtime_checkable

FreshnessState = Literal["current", "stale", "unknown"]

FRESHNESS_STATES: tuple[str, str, str] = ("current", "stale", "unknown")

#: Normalized markers that mean "no schema value was supplied".
_MISSING_MARKERS = frozenset({"", "unknown", "n/a", "none", "null"})


def _is_missing(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and value.strip().lower() in _MISSING_MARKERS:
        return True
    return False


@runtime_checkable
class CodeIntelSnapshot(Protocol):
    """Frozen spec stub: snapshot owned by the snapshots agent."""

    repo_id: str
    commit_sha: str
    snapshot_id: str


@runtime_checkable
class Freshness(Protocol):
    """Frozen spec stub: freshness owned by the snapshots agent."""

    repo_id: str
    commit_sha: str
    snapshot_id: str
    workspace_head: str
    freshness: str
    reason: str


@dataclass(frozen=True, slots=True)
class FreshnessRecord:
    """Concrete record implementing the ``Freshness`` shape for local use."""

    repo_id: str
    commit_sha: str
    snapshot_id: str
    workspace_head: str = ""
    freshness: str = "unknown"
    reason: str = ""

    def __post_init__(self) -> None:
        state = str(self.freshness or "").strip().lower()
        if state not in FRESHNESS_STATES:
            raise ValueError(
                f"freshness must be one of {list(FRESHNESS_STATES)}, got {self.freshness!r}"
            )
        object.__setattr__(self, "freshness", state)

    @property
    def is_stale(self) -> bool:
        return self.freshness == "stale"

    def to_dict(self) -> dict[str, Any]:
        return {
            "repo_id": self.repo_id,
            "commit_sha": self.commit_sha,
            "snapshot_id": self.snapshot_id,
            "workspace_head": self.workspace_head,
            "freshness": self.freshness,
            "reason": self.reason,
        }


def coerce_freshness(value: Freshness | Mapping[str, Any] | None) -> FreshnessRecord:
    """Normalize a freshness payload into a ``FreshnessRecord``.

    ``None`` becomes an ``unknown`` record. Mappings use explicit keys with
    empty-string fallbacks for the descriptive fields only; the ``freshness``
    state itself is validated and never defaulted from other fields.
    """
    if value is None:
        return FreshnessRecord(repo_id="", commit_sha="", snapshot_id="")
    if isinstance(value, FreshnessRecord):
        return value
    if isinstance(value, Mapping):
        return FreshnessRecord(
            repo_id=str(value.get("repo_id") or ""),
            commit_sha=str(value.get("commit_sha") or ""),
            snapshot_id=str(value.get("snapshot_id") or ""),
            workspace_head=str(value.get("workspace_head") or ""),
            freshness=str(value.get("freshness") or "unknown"),
            reason=str(value.get("reason") or ""),
        )
    return FreshnessRecord(
        repo_id=str(getattr(value, "repo_id", "") or ""),
        commit_sha=str(getattr(value, "commit_sha", "") or ""),
        snapshot_id=str(getattr(value, "snapshot_id", "") or ""),
        workspace_head=str(getattr(value, "workspace_head", "") or ""),
        freshness=str(getattr(value, "freshness", "") or "unknown"),
        reason=str(getattr(value, "reason", "") or ""),
    )


def _check_schema_field(value: object, field_name: str) -> str:
    if isinstance(value, str) and not _is_missing(value):
        return value
    raise ValueError(
        f"Citation.{field_name} is required: schemaless input "
        f"(missing or 'unknown' {field_name}) is rejected"
    )


def _check_line_range(value: object) -> tuple[int, int]:
    if isinstance(value, (list, tuple)) and len(value) == 2:
        start, end = value
        if (
            isinstance(start, int)
            and isinstance(end, int)
            and not isinstance(start, bool)
            and not isinstance(end, bool)
            and 1 <= start <= end
        ):
            return (start, end)
    raise ValueError(
        "Citation.line_range must be a (start, end) pair of ints "
        "with 1 <= start <= end"
    )


def _check_confidence(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(
            f"Citation.confidence must be a number in [0, 1], got {value!r}"
        )
    number = float(value)
    if math.isnan(number) or math.isinf(number) or not 0.0 <= number <= 1.0:
        raise ValueError(
            f"Citation.confidence must be a number in [0, 1], got {value!r}"
        )
    return number


@dataclass(frozen=True, slots=True)
class Citation:
    """One candidate citation: scored, schema-bound, advisory-only.

    The constructor rejects schemaless input (any of ``path``,
    ``snapshot_id``, ``commit_sha`` missing or ``'unknown'``) and any
    out-of-range ``confidence`` with ``ValueError``.
    """

    path: str
    line_range: tuple[int, int]
    snapshot_id: str
    commit_sha: str
    confidence: float
    source: str = "codeintel_v2"
    freshness: Freshness | Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", _check_schema_field(self.path, "path"))
        object.__setattr__(self, "line_range", _check_line_range(self.line_range))
        object.__setattr__(
            self, "snapshot_id", _check_schema_field(self.snapshot_id, "snapshot_id")
        )
        object.__setattr__(
            self, "commit_sha", _check_schema_field(self.commit_sha, "commit_sha")
        )
        object.__setattr__(self, "confidence", _check_confidence(self.confidence))
        if not isinstance(self.source, str):
            raise ValueError(
                f"Citation.source must be a string, got {self.source!r}"
            )
        object.__setattr__(self, "freshness", coerce_freshness(self.freshness))

    @property
    def is_stale(self) -> bool:
        record = self.freshness
        assert isinstance(record, FreshnessRecord)
        return record.is_stale


def citation_to_dict(citation: Citation) -> dict[str, Any]:
    """Serialize one citation to plain data (serializer, not a selector)."""
    if not isinstance(citation, Citation):
        raise ValueError("citation_to_dict expects a Citation instance")
    freshness = citation.freshness
    assert isinstance(freshness, FreshnessRecord)
    return {
        "path": citation.path,
        "line_range": [citation.line_range[0], citation.line_range[1]],
        "snapshot_id": citation.snapshot_id,
        "commit_sha": citation.commit_sha,
        "confidence": citation.confidence,
        "source": citation.source,
        "freshness": freshness.to_dict(),
    }


__all__ = [
    "FRESHNESS_STATES",
    "Citation",
    "CodeIntelSnapshot",
    "Freshness",
    "FreshnessRecord",
    "FreshnessState",
    "citation_to_dict",
    "coerce_freshness",
]
