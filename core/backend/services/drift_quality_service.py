"""Drift quality scoring to suppress low-signal action-map proposals."""

from __future__ import annotations

from typing import Any


def assess_action_map_drift_quality(
    *,
    provider_before: list[str],
    provider_after: list[str],
    codeintel_context: dict[str, Any] | None,
    min_confidence: float = 0.6,
    min_entities: int = 2,
    min_citations: int = 2,
) -> dict[str, Any]:
    before = {str(item).strip().lower() for item in provider_before if str(item).strip()}
    after = {str(item).strip().lower() for item in provider_after if str(item).strip()}
    providers_added = sorted([item for item in after if item not in before])
    providers_removed = sorted([item for item in before if item not in after])
    provider_delta = len(providers_added) + len(providers_removed)

    context = codeintel_context or {}
    entities_total = int(context.get("entities_total") or 0)
    citations_total = len(context.get("citations") or [])
    confidence_score = float(context.get("confidence_score") or 0.0)

    reasons: list[str] = []
    score = 0.0

    if provider_delta > 0:
        score += 0.35
        reasons.append("PROVIDER_DELTA_DETECTED")
    else:
        reasons.append("PROVIDER_DELTA_NONE")

    if entities_total >= min_entities:
        score += 0.25
        reasons.append("ENTITY_SIGNAL_STRONG")
    else:
        reasons.append("ENTITY_SIGNAL_WEAK")

    if citations_total >= min_citations:
        score += 0.2
        reasons.append("CITATION_SIGNAL_STRONG")
    else:
        reasons.append("CITATION_SIGNAL_WEAK")

    if confidence_score >= min_confidence:
        score += 0.2
        reasons.append("CONFIDENCE_STRONG")
    else:
        reasons.append("CONFIDENCE_WEAK")

    should_propose = provider_delta > 0 and score >= 0.7
    if should_propose:
        reasons.append("DRIFT_SIGNAL_ACCEPTED")
    else:
        reasons.append("DRIFT_SIGNAL_BELOW_THRESHOLD")

    return {
        "quality_score": round(score, 4),
        "threshold": 0.7,
        "should_propose": should_propose,
        "provider_delta": provider_delta,
        "providers_added": providers_added,
        "providers_removed": providers_removed,
        "entities_total": entities_total,
        "citations_total": citations_total,
        "confidence_score": confidence_score,
        "reasons": reasons,
    }
