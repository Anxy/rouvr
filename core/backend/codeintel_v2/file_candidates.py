"""File candidates for CodeIntel v2 (Wave 4 CI3, AIX lane).

Candidate-only, advisory-only: :func:`search_files` walks an explicit
workspace directory, scores filename matches, and returns every candidate
above threshold with ``ambiguous`` / ``selected`` flags. It never resolves,
ranks-for-action, or returns a file for a caller to act on:

- every candidate is verified with ``os.path.isfile``; non-files are dropped;
- when more than one candidate scores above threshold, ALL are flagged
  ``ambiguous=True`` and EVERY entry carries ``selected=False``
  (ambiguity rejection: return all, select none);
- even a single above-threshold candidate carries ``selected=False`` --
  no function in this module returns a chosen file.

Freshness reuses the frozen shapes from the citations module
(``FreshnessRecord`` / ``coerce_freshness``); candidates default to
``unknown`` freshness.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from backend.codeintel_v2.citations import FreshnessRecord, coerce_freshness

#: Minimum confidence for a walk hit to count as a candidate.
THRESHOLD = 0.5

__all__ = ["THRESHOLD", "FileCandidate", "candidate_to_dict", "search_files"]


def _check_confidence(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(
            f"FileCandidate.confidence must be a number in [0, 1], got {value!r}"
        )
    number = float(value)
    if math.isnan(number) or math.isinf(number) or not 0.0 <= number <= 1.0:
        raise ValueError(
            f"FileCandidate.confidence must be a number in [0, 1], got {value!r}"
        )
    return number


def _check_text(value: object, field_name: str) -> str:
    if isinstance(value, str) and value.strip():
        return value
    raise ValueError(f"FileCandidate.{field_name} must be a non-empty string")


@dataclass(frozen=True, slots=True)
class FileCandidate:
    """One candidate file: scored, advisory-only, never chosen.

    ``ambiguous`` marks ambiguity rejection; there is intentionally no
    chosen-file field -- choosing never happens here.
    """

    path: str
    confidence: float
    reason: str
    freshness: FreshnessRecord | Mapping[str, Any] | None = None
    ambiguous: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", _check_text(self.path, "path"))
        object.__setattr__(self, "confidence", _check_confidence(self.confidence))
        object.__setattr__(self, "reason", _check_text(self.reason, "reason"))
        object.__setattr__(self, "freshness", coerce_freshness(self.freshness))
        object.__setattr__(self, "ambiguous", bool(self.ambiguous))


def candidate_to_dict(candidate: FileCandidate) -> dict[str, Any]:
    """Serialize one candidate (serializer, not a chooser).

    ``selected`` is always ``False``: this module never marks a file
    for a caller to act on.
    """
    if not isinstance(candidate, FileCandidate):
        raise ValueError("candidate_to_dict expects a FileCandidate instance")
    freshness = candidate.freshness
    assert isinstance(freshness, FreshnessRecord)
    return {
        "path": candidate.path,
        "confidence": candidate.confidence,
        "reason": candidate.reason,
        "freshness": freshness.to_dict(),
        "ambiguous": candidate.ambiguous,
        "selected": False,
    }


def _score_filename(query: str, filename: str) -> tuple[float, str]:
    name = filename.lower()
    stem, _, _ = filename.lower().rpartition(".")
    if name == query:
        return 1.0, "exact filename match"
    if stem and stem == query:
        return 0.9, "filename stem match"
    if query in name:
        return 0.7, "query substring of filename"
    return 0.0, ""


def search_files(
    query: Any, workspace_root: str | Path, snapshot: Any = None
) -> list[dict[str, Any]]:
    """Walk ``workspace_root`` and return scored file candidates.

    Args:
        query: filename text to match (case-insensitive).
        workspace_root: explicit directory to walk; no index server needed.
        snapshot: optional record carrying a ``freshness`` payload; when
            absent/unreadable the candidates carry ``unknown`` freshness.

    Returns:
        List of candidate dicts (``path``, ``confidence``, ``reason``,
        ``freshness``, ``ambiguous``, ``selected``). Non-files are dropped,
        below-threshold hits are dropped, and ``selected`` is always
        ``False``. When >1 candidate scores above threshold, every entry
        carries ``ambiguous=True`` (ambiguity rejection: return all,
        select none).
    """
    if not isinstance(query, str) or not query.strip():
        return []
    try:
        root = Path(workspace_root)
        if not root.is_dir():
            return []
    except Exception:
        return []
    needle = query.strip().lower()
    freshness_source = getattr(snapshot, "freshness", None) if snapshot is not None else None
    try:
        freshness = coerce_freshness(freshness_source)
    except Exception:
        freshness = coerce_freshness(None)

    try:
        entries = list(os.walk(root))
    except Exception:
        return []
    scored: list[FileCandidate] = []
    for dirpath, _dirnames, filenames in entries:
        for filename in filenames:
            confidence, reason = _score_filename(needle, filename)
            if confidence < THRESHOLD:
                continue
            full = Path(dirpath) / filename
            try:
                if not full.is_file():
                    continue
            except Exception:
                continue
            rel = full.relative_to(root).as_posix()
            scored.append(
                FileCandidate(
                    path=rel,
                    confidence=confidence,
                    reason=f"{reason} for {query.strip()!r}",
                    freshness=freshness,
                )
            )
    scored.sort(key=lambda item: (-item.confidence, item.path))
    above = [item for item in scored if item.confidence >= THRESHOLD]
    rejected = len(above) > 1
    results: list[dict[str, Any]] = []
    for item in above:
        flagged = FileCandidate(
            path=item.path,
            confidence=item.confidence,
            reason=item.reason,
            freshness=item.freshness,
            ambiguous=rejected,
        )
        results.append(candidate_to_dict(flagged))
    return results
