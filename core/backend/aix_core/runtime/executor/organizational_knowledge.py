"""EX4-R2-P3-S001: bounded organizational-knowledge behaviours.

This is a deterministic query layer over the existing Knowledge Store and P2
edges. It does not synthesize answers, add knowledge kinds, or create a second
knowledge data plane.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from backend.aix_core.runtime.executor.context_builder import (
    KNOWLEDGE_CONFIDENCE,
    InMemoryKnowledgeStore,
    KnowledgeEntry,
    knowledge_entry_is_current,
    knowledge_entry_is_stale,
)
from backend.aix_core.runtime.executor.knowledge_graph import InMemoryKnowledgeGraph, edge_to_dict
from backend.aix_core.runtime.executor.pattern_engine import content_fingerprint
from backend.infrastructure.postgres.execution_kernel.repository import ExecutionKernelRepositoryError

_CONFIDENCE_RANK = {"unknown": 0, "low": 1, "medium": 2, "high": 3}


def _require_tenant(tenant_id: str) -> str:
    normalized = str(tenant_id or "").strip()
    if not normalized:
        raise ExecutionKernelRepositoryError("tenant_id is required (fail closed)")
    return normalized


def _matches_capability(entry: KnowledgeEntry, capability_id: str | None) -> bool:
    if not capability_id:
        return True
    return entry.capability_id == capability_id or capability_id in entry.applicable_capability_ids


def query_organizational_knowledge(
    knowledge: InMemoryKnowledgeStore,
    graph: InMemoryKnowledgeGraph,
    *,
    tenant_id: str,
    capability_id: str | None = None,
    project_id: str | None = None,
    minimum_confidence: str = "unknown",
    include_stale: bool = False,
) -> list[dict[str, Any]]:
    """Return current governed Knowledge with relationship provenance."""
    tenant_id = _require_tenant(tenant_id)
    if minimum_confidence not in KNOWLEDGE_CONFIDENCE:
        raise ExecutionKernelRepositoryError(
            f"invalid minimum_confidence={minimum_confidence!r}"
        )
    results: list[dict[str, Any]] = []
    for entry in knowledge._entries:  # noqa: SLF001 - bounded corpus query
        if entry.tenant_id != tenant_id or not knowledge_entry_is_current(entry):
            continue
        if not include_stale and knowledge_entry_is_stale(entry):
            continue
        if not _matches_capability(entry, capability_id):
            continue
        if project_id and entry.project_id != project_id:
            continue
        if _CONFIDENCE_RANK[entry.confidence] < _CONFIDENCE_RANK[minimum_confidence]:
            continue
        outgoing = graph.get_edges(
            tenant_id=tenant_id,
            from_type="knowledge",
            from_id=entry.knowledge_entry_id,
            active_only=True,
        )
        incoming = graph.get_edges(
            tenant_id=tenant_id,
            to_type="knowledge",
            to_id=entry.knowledge_entry_id,
            active_only=True,
        )
        provenance_edges = sorted(
            (edge_to_dict(edge) for edge in outgoing + incoming),
            key=lambda edge: edge["edge_id"],
        )
        results.append(
            {
                "knowledge_entry_id": entry.knowledge_entry_id,
                "knowledge_kind": entry.knowledge_kind,
                "capability_id": entry.capability_id,
                "problem_id": entry.problem_id,
                "project_id": entry.project_id,
                "content": entry.content,
                "confidence": entry.confidence,
                "stale": knowledge_entry_is_stale(entry),
                "provenance_edges": provenance_edges,
            }
        )
    return sorted(results, key=lambda item: item["knowledge_entry_id"])


def list_stale_knowledge(
    knowledge: InMemoryKnowledgeStore,
    *,
    tenant_id: str,
    capability_id: str | None = None,
) -> list[dict[str, Any]]:
    """EX5-S7 (Knowledge Evolution): the worklist half of 'never
    permanently stale' — knowledge_entry_is_stale() (EX4-S002) could
    already *detect* staleness, but nothing ever surfaced a queryable list
    of what actually needs attention. Only ACTIVE entries are returned —
    a SUPERSEDED/DEPRECATED entry going stale needs no one's review, it's
    already been replaced or retired on purpose."""
    tenant_id = _require_tenant(tenant_id)
    results: list[dict[str, Any]] = []
    for entry in knowledge._entries:  # noqa: SLF001 - bounded corpus query
        if entry.tenant_id != tenant_id or not knowledge_entry_is_current(entry):
            # Deliberately uses knowledge_entry_is_current (ACTIVE and not
            # superseded), not a bare status=="ACTIVE" check — a DEPRECATED
            # or SUPERSEDED entry going past its date needs no one's
            # review, it was already retired/replaced on purpose.
            continue
        if not knowledge_entry_is_stale(entry):
            continue
        if not _matches_capability(entry, capability_id):
            continue
        results.append(
            {
                "knowledge_entry_id": entry.knowledge_entry_id,
                "knowledge_kind": entry.knowledge_kind,
                "capability_id": entry.capability_id,
                "content": entry.content,
                "confidence": entry.confidence,
                "valid_until": entry.valid_until,
                "review_due_at": entry.review_due_at,
            }
        )
    return sorted(results, key=lambda item: item["knowledge_entry_id"])


def detect_missing_knowledge(
    knowledge: InMemoryKnowledgeStore,
    *,
    tenant_id: str,
    capability_id: str,
    required_kinds: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    tenant_id = _require_tenant(tenant_id)
    capability_id = str(capability_id or "").strip()
    if not capability_id:
        raise ExecutionKernelRepositoryError("capability_id is required")
    current = [
        entry
        for entry in knowledge._entries  # noqa: SLF001
        if entry.tenant_id == tenant_id
        and knowledge_entry_is_current(entry)
        and not knowledge_entry_is_stale(entry)
        and _matches_capability(entry, capability_id)
    ]
    present_kinds = sorted({entry.knowledge_kind for entry in current})
    required = tuple(dict.fromkeys(required_kinds or ()))
    missing_kinds = sorted(set(required) - set(present_kinds))
    return {
        "tenant_id": tenant_id,
        "capability_id": capability_id,
        "has_current_knowledge": bool(current),
        "present_kinds": present_kinds,
        "missing_kinds": missing_kinds,
        "missing": not current or bool(missing_kinds),
    }


def reconcile_cross_project_knowledge(
    knowledge: InMemoryKnowledgeStore,
    *,
    tenant_id: str,
) -> list[dict[str, Any]]:
    """Find identical current knowledge reused across two or more Projects."""
    tenant_id = _require_tenant(tenant_id)
    groups: dict[str, list[KnowledgeEntry]] = defaultdict(list)
    for entry in knowledge._entries:  # noqa: SLF001
        if entry.tenant_id != tenant_id or not entry.project_id:
            continue
        if not knowledge_entry_is_current(entry) or knowledge_entry_is_stale(entry):
            continue
        fingerprint = content_fingerprint(entry.content, capability_id=entry.capability_id)
        groups[fingerprint].append(entry)

    reconciled: list[dict[str, Any]] = []
    for fingerprint, entries in groups.items():
        project_ids = sorted({entry.project_id for entry in entries if entry.project_id})
        if len(project_ids) < 2:
            continue
        reconciled.append(
            {
                "fingerprint": fingerprint,
                "project_ids": project_ids,
                "knowledge_entry_ids": sorted(entry.knowledge_entry_id for entry in entries),
                "sample_content": sorted(entries, key=lambda entry: entry.knowledge_entry_id)[0].content,
            }
        )
    return sorted(reconciled, key=lambda item: item["fingerprint"])


__all__ = [
    "query_organizational_knowledge",
    "list_stale_knowledge",
    "detect_missing_knowledge",
    "reconcile_cross_project_knowledge",
]
