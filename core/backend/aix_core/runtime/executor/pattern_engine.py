"""EX4-R2-P7-S001: Pattern Intelligence — corpus discovery + recommendation.

Patterns remain in the Knowledge Store (`knowledge_kind=pattern`).
No separate Pattern Repository. No automatic org-wide promotion.
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from backend.aix_core.runtime.executor.context_builder import (
    InMemoryKnowledgeStore,
    KnowledgeEntry,
    knowledge_entry_is_current,
)
from backend.aix_core.runtime.executor.knowledge_graph import InMemoryKnowledgeGraph
from backend.infrastructure.postgres.execution_kernel.repository import ExecutionKernelRepositoryError

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _require_tenant(tenant_id: str) -> str:
    normalized = str(tenant_id or "").strip()
    if not normalized:
        raise ExecutionKernelRepositoryError("tenant_id is required (fail closed)")
    return normalized


def normalize_pattern_text(content: str) -> str:
    tokens = _TOKEN_RE.findall(str(content or "").lower())
    return " ".join(tokens)


def content_fingerprint(content: str, *, capability_id: str | None = None) -> str:
    """Deterministic fingerprint for corpus grouping (not semantic similarity)."""
    base = normalize_pattern_text(content)
    scoped = f"{capability_id or ''}::{base}"
    return hashlib.sha256(scoped.encode("utf-8")).hexdigest()[:24]


@dataclass(frozen=True)
class PatternCandidate:
    fingerprint: str
    capability_id: str | None
    sample_content: str
    occurrence_count: int
    source_entry_ids: tuple[str, ...]
    already_promoted: bool
    recommendation: str  # "promote_candidate" | "already_pattern" | "insufficient_support"


@dataclass(frozen=True)
class PatternRecommendation:
    knowledge_entry_id: str
    capability_id: str | None
    content: str
    score: int
    reasons: tuple[str, ...]


def discover_pattern_candidates(
    knowledge: InMemoryKnowledgeStore,
    *,
    tenant_id: str,
    min_occurrences: int = 2,
    include_lessons: bool = True,
) -> list[PatternCandidate]:
    """Corpus mining: group lessons/patterns by fingerprint within a capability.

    Does not write Knowledge or promote automatically — returns candidates only.
    """
    tenant_id = _require_tenant(tenant_id)
    if min_occurrences < 2:
        raise ExecutionKernelRepositoryError("min_occurrences must be >= 2")

    buckets: dict[str, list[KnowledgeEntry]] = defaultdict(list)
    for entry in knowledge._entries:  # noqa: SLF001 — internal corpus scan
        if entry.tenant_id != tenant_id:
            continue
        if entry.status not in {"ACTIVE", "SUPERSEDED"}:
            continue
        if entry.knowledge_kind == "pattern":
            buckets[content_fingerprint(entry.content, capability_id=entry.capability_id)].append(entry)
        elif include_lessons and entry.knowledge_kind == "lesson_learned":
            buckets[content_fingerprint(entry.content, capability_id=entry.capability_id)].append(entry)

    out: list[PatternCandidate] = []
    for fp, entries in sorted(buckets.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        patterns = [e for e in entries if e.knowledge_kind == "pattern" and knowledge_entry_is_current(e)]
        lessons = [e for e in entries if e.knowledge_kind == "lesson_learned"]
        count = len(entries)
        already = bool(patterns)
        sample = (patterns[0].content if patterns else lessons[0].content if lessons else entries[0].content)
        cap = next((e.capability_id for e in entries if e.capability_id), None)
        if already:
            rec = "already_pattern"
        elif count >= min_occurrences:
            rec = "promote_candidate"
        else:
            rec = "insufficient_support"
            if count < min_occurrences:
                # Still report for transparency when at least 1 lesson exists and caller wants full scan
                pass
        if rec == "insufficient_support" and count < min_occurrences:
            continue
        out.append(
            PatternCandidate(
                fingerprint=fp,
                capability_id=cap,
                sample_content=sample,
                occurrence_count=count,
                source_entry_ids=tuple(e.knowledge_entry_id for e in entries),
                already_promoted=already,
                recommendation=rec,
            )
        )
    return out


def recommend_patterns(
    knowledge: InMemoryKnowledgeStore,
    graph: InMemoryKnowledgeGraph | None = None,
    *,
    tenant_id: str,
    capability_id: str,
    problem_id: str | None = None,
    limit: int = 5,
) -> list[PatternRecommendation]:
    """Recommend ACTIVE patterns for a capability; boost by USES_PATTERN / same problem."""
    tenant_id = _require_tenant(tenant_id)
    capability_id = str(capability_id or "").strip()
    if not capability_id:
        raise ExecutionKernelRepositoryError("capability_id is required")
    if limit < 1:
        raise ExecutionKernelRepositoryError("limit must be >= 1")

    use_counts: dict[str, int] = defaultdict(int)
    if graph is not None:
        for edge in graph.list_edges(tenant_id=tenant_id, edge_type="USES_PATTERN", active_only=True):
            if edge.to_type in {"pattern", "knowledge"}:
                use_counts[edge.to_id] += 1

    scored: list[PatternRecommendation] = []
    for entry in knowledge._entries:  # noqa: SLF001
        if entry.tenant_id != tenant_id:
            continue
        if entry.knowledge_kind != "pattern":
            continue
        if not knowledge_entry_is_current(entry):
            continue
        if entry.capability_id != capability_id and capability_id not in entry.applicable_capability_ids:
            continue
        score = 10
        reasons: list[str] = ["same_capability_or_applicable"]
        if problem_id and entry.problem_id == problem_id:
            score += 5
            reasons.append("same_problem")
        uses = use_counts.get(entry.knowledge_entry_id, 0)
        if uses:
            score += min(uses * 3, 15)
            reasons.append(f"uses_pattern_edges={uses}")
        scored.append(
            PatternRecommendation(
                knowledge_entry_id=entry.knowledge_entry_id,
                capability_id=entry.capability_id,
                content=entry.content,
                score=score,
                reasons=tuple(reasons),
            )
        )
    scored.sort(key=lambda r: (-r.score, r.knowledge_entry_id))
    return scored[:limit]


def candidate_to_dict(c: PatternCandidate) -> dict[str, Any]:
    return {
        "fingerprint": c.fingerprint,
        "capability_id": c.capability_id,
        "sample_content": c.sample_content,
        "occurrence_count": c.occurrence_count,
        "source_entry_ids": list(c.source_entry_ids),
        "already_promoted": c.already_promoted,
        "recommendation": c.recommendation,
    }


def recommendation_to_dict(r: PatternRecommendation) -> dict[str, Any]:
    return {
        "knowledge_entry_id": r.knowledge_entry_id,
        "capability_id": r.capability_id,
        "content": r.content,
        "score": r.score,
        "reasons": list(r.reasons),
    }


__all__ = [
    "PatternCandidate",
    "PatternRecommendation",
    "normalize_pattern_text",
    "content_fingerprint",
    "discover_pattern_candidates",
    "recommend_patterns",
    "candidate_to_dict",
    "recommendation_to_dict",
]
