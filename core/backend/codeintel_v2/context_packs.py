"""Context packs for the CodeIntel v2 lane (Wave 4 CI6, AIX lane).

A ``ContextPack`` is an advisory-only bundle of candidate ``Citation`` items
bound to one snapshot. Packs never resolve, select, or rank anything on the
caller's behalf: :func:`build_pack` validates, caps, and labels only.

Freshness rule (worst of members): any ``stale`` member makes the pack
``stale``; otherwise any ``unknown`` member makes the pack ``unknown``;
otherwise the pack is ``current``. Every pack carries its freshness label
plus a human-readable reason, including empty packs.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from backend.codeintel_v2.citations import (
    FRESHNESS_STATES,
    Citation,
    FreshnessRecord,
)
from backend.codeintel_v2.snapshots import codeintel_posture

#: Maximum citations carried in one pack; overflow is dropped from the tail
#: and recorded via ``ContextPack.truncated``.
MAX_PACK_CITATIONS = 50

#: Reason recorded when the posture gate refuses to build.
REASON_POSTURE_OFF = "posture_off: AIX_CODEINTEL is off; no citations emitted"

#: Reason recorded when no citations were supplied.
REASON_EMPTY = "empty: no citations supplied"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _member_label(citation: Citation) -> str:
    record = citation.freshness
    assert isinstance(record, FreshnessRecord)
    return record.freshness


def _check_provenance(citation: Any) -> Citation:
    """Return ``citation`` when it carries full provenance, else ``ValueError``.

    Full provenance means a real :class:`Citation` (whose constructor already
    enforces ``path`` / ``line_range`` / ``snapshot_id`` / ``commit_sha`` /
    ``confidence``) with a non-blank ``source`` and a valid freshness label.
    Anything else -- plain dicts, partial shapes, unlabelled items -- is
    rejected, never repaired.
    """
    if not isinstance(citation, Citation):
        raise ValueError(
            "build_pack rejects items without full provenance: "
            f"expected Citation, got {type(citation).__name__}"
        )
    if not isinstance(citation.source, str) or not citation.source.strip():
        raise ValueError(
            "build_pack rejects citations with missing provenance field 'source'"
        )
    if _member_label(citation) not in FRESHNESS_STATES:
        raise ValueError(
            "build_pack rejects citations with missing provenance field 'freshness'"
        )
    return citation


def _worst_of(labels: list[str]) -> tuple[str, str]:
    """Fold member freshness labels into ``(pack_label, reason)``."""
    total = len(labels)
    stale = sum(1 for label in labels if label == "stale")
    unknown = sum(1 for label in labels if label == "unknown")
    if stale:
        return (
            "stale",
            f"pack stale: {stale} of {total} citations stale",
        )
    if unknown:
        return (
            "unknown",
            f"pack unknown: {unknown} of {total} citations unknown",
        )
    return ("current", f"pack current: all {total} citations current")


@dataclass
class ContextPack:
    """Advisory-only bundle of candidate citations for one snapshot."""

    pack_id: str
    repo_id: str
    commit_sha: str
    citations: list[Citation] = field(default_factory=list)
    freshness: FreshnessRecord = field(
        default_factory=lambda: FreshnessRecord(
            repo_id="", commit_sha="", snapshot_id="", freshness="unknown",
            reason=REASON_EMPTY,
        )
    )
    advisory_degraded: bool = True
    built_at: str = ""
    truncated: bool = False
    reason: str = REASON_EMPTY

    def to_dict(self) -> dict[str, Any]:
        from backend.codeintel_v2.citations import citation_to_dict

        return {
            "pack_id": self.pack_id,
            "repo_id": self.repo_id,
            "commit_sha": self.commit_sha,
            "citations": [citation_to_dict(item) for item in self.citations],
            "freshness": self.freshness.to_dict(),
            "advisory_degraded": self.advisory_degraded,
            "built_at": self.built_at,
            "truncated": self.truncated,
            "reason": self.reason,
        }


def _snapshot_field(snapshot: Any, name: str) -> str:
    if snapshot is None:
        return ""
    if isinstance(snapshot, dict):
        value = snapshot.get(name, "")
    else:
        value = getattr(snapshot, name, "")
    return str(value or "")


def _empty_pack(
    snapshot: Any, reason: str, freshness_label: str = "unknown"
) -> ContextPack:
    repo_id = _snapshot_field(snapshot, "repo_id")
    commit_sha = _snapshot_field(snapshot, "commit_sha")
    snapshot_id = _snapshot_field(snapshot, "snapshot_id")
    return ContextPack(
        pack_id=uuid.uuid4().hex,
        repo_id=repo_id,
        commit_sha=commit_sha,
        citations=[],
        freshness=FreshnessRecord(
            repo_id=repo_id,
            commit_sha=commit_sha,
            snapshot_id=snapshot_id,
            freshness=freshness_label,
            reason=reason,
        ),
        advisory_degraded=(freshness_label in ("stale", "unknown")),
        built_at=_utc_now_iso(),
        truncated=False,
        reason=reason,
    )


def build_pack(
    citations: Any, snapshot: Any, workspace_head: Any = None
) -> ContextPack:
    """Validate ``citations`` and bundle them into a :class:`ContextPack`.

    - Posture ``off`` => empty pack with reason ``posture_off``; supplied
      items are not even inspected, so nothing is fabricated.
    - Empty input => empty pack with reason ``empty: ...`` (never an error).
    - Any item lacking full provenance => ``ValueError``.
    - More than :data:`MAX_PACK_CITATIONS` items => head kept, tail dropped,
      ``truncated`` set to ``True`` and noted in the reason.
    - Pack freshness is the worst of its members; ``advisory_degraded`` is
      true exactly when the pack is ``stale`` or ``unknown``.
    """
    del workspace_head  # reserved for future pack-level checks; unused today.
    if codeintel_posture() == "off":
        return _empty_pack(snapshot, REASON_POSTURE_OFF)
    if citations is None:
        return _empty_pack(snapshot, REASON_EMPTY)
    items = list(citations)
    if not items:
        return _empty_pack(snapshot, REASON_EMPTY)
    checked = [_check_provenance(item) for item in items]
    truncated = len(checked) > MAX_PACK_CITATIONS
    kept = checked[:MAX_PACK_CITATIONS]
    label, pack_reason = _worst_of([_member_label(item) for item in kept])
    if truncated:
        pack_reason = (
            f"{pack_reason}; truncated: kept {MAX_PACK_CITATIONS} "
            f"of {len(checked)} citations"
        )
    repo_id = _snapshot_field(snapshot, "repo_id")
    commit_sha = _snapshot_field(snapshot, "commit_sha")
    snapshot_id = _snapshot_field(snapshot, "snapshot_id")
    return ContextPack(
        pack_id=uuid.uuid4().hex,
        repo_id=repo_id,
        commit_sha=commit_sha,
        citations=kept,
        freshness=FreshnessRecord(
            repo_id=repo_id,
            commit_sha=commit_sha,
            snapshot_id=snapshot_id,
            freshness=label,
            reason=pack_reason,
        ),
        advisory_degraded=(label in ("stale", "unknown")),
        built_at=_utc_now_iso(),
        truncated=truncated,
        reason=pack_reason,
    )


__all__ = [
    "MAX_PACK_CITATIONS",
    "REASON_EMPTY",
    "REASON_POSTURE_OFF",
    "ContextPack",
    "build_pack",
]
