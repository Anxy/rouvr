"""Compaction & retention policy (Stage 5 core).

Tiers (approved model): 30d hot (Redis) / 90d warm (Postgres) /
1y cold archive, never delete. ``compact_capsule`` keeps decisions,
evidence refs, and identity (what a replacement worker needs) and
drops bulky reconstructible fields (transcripts, full snapshots —
re-fetchable from CodeIntel/WorkHub). ``tier_for_age`` routes by age.
Pure policy; the mover cron enforcing it is operator-owned.
"""

from __future__ import annotations

from typing import Any

HOT_DAYS = 30
WARM_DAYS = 90
COLD_DAYS = 365

KEEP_TOP_LEVEL = frozenset({
    "capsule_version",
    "session_id",
    "tenant_id",
    "intent",
    "lane_checkpoint",
    "lane_tag",
    "persona_bindings",
    "guardrails_hash",
    "created_at",
})

DROP_SNAPSHOT_KEYS = frozenset({"voice_transcript_ref"})


def tier_for_age(*, age_days: int) -> str:
    """'hot' | 'warm' | 'cold' | 'expired' by approved tiers."""
    if age_days < 0:
        raise ValueError("negative age")
    if age_days <= HOT_DAYS:
        return "hot"
    if age_days <= WARM_DAYS:
        return "warm"
    if age_days <= COLD_DAYS:
        return "cold"
    return "expired"


def compact_capsule(capsule: dict[str, Any]) -> dict[str, Any]:
    """Compact to replacement-worker essentials (pure, unit-tested)."""
    kept = {k: capsule[k] for k in KEEP_TOP_LEVEL if k in capsule}
    snapshot = capsule.get("codeintel_snapshot", {})
    artifacts = snapshot.get("artifacts", []) if isinstance(snapshot, dict) else []
    workhub = capsule.get("workhub_snapshot", {})
    mandates = workhub.get("open_mandate_ids", []) if isinstance(workhub, dict) else []
    kept["codeintel_snapshot"] = {"queried": bool(artifacts), "artifacts": list(artifacts)}
    kept["workhub_snapshot"] = {"open_mandate_ids": list(mandates)}
    kept["compacted"] = True
    return kept
