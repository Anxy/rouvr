"""EX2-S004: Context Builder — assemble model-agnostic capsules (Packet 6).

EX4-S001: external contract serialization helper (`capsule_to_contract_dict`).
EX4-S002: knowledge freshness / supersession / retention fields on KnowledgeEntry.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

from backend.aix_core.runtime.executor.dependency_graph import InMemoryDependencyGraph
from backend.aix_core.runtime.executor.projection import InMemoryProjectionStore
from backend.infrastructure.postgres.execution_kernel.repository import ExecutionKernelRepositoryError

SCHEMA_VERSION = "aix.execution-context-capsule.v1"
KNOWLEDGE_KINDS = frozenset(
    {"architecture", "lesson_learned", "pattern", "business_knowledge", "technical_knowledge"}
)
KnowledgeConfidence = Literal["unknown", "low", "medium", "high"]
KNOWLEDGE_CONFIDENCE = frozenset({"unknown", "low", "medium", "high"})


def _require_tenant(tenant_id: str) -> str:
    normalized = str(tenant_id or "").strip()
    if not normalized:
        raise ExecutionKernelRepositoryError("tenant_id is required (fail closed)")
    return normalized


def _normalize_content(content: str) -> str:
    return " ".join(str(content).split())


def estimate_tokens(content: str) -> int:
    return max(1, math.ceil(len(content.encode("utf-8")) / 4))


def canonical_content_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class KnowledgeEntry:
    knowledge_entry_id: str
    knowledge_kind: str
    capability_id: str | None
    problem_id: str | None
    content: str
    applicable_capability_ids: tuple[str, ...] = ()
    tenant_id: str = ""
    project_id: str | None = None
    status: str = "ACTIVE"
    revision: int = 1
    # EX4-S002 — freshness / supersession / retention (nullable; never TTL-delete)
    valid_from: str | None = None
    valid_until: str | None = None
    superseded_by_entry_id: str | None = None
    confidence: KnowledgeConfidence = "unknown"
    review_due_at: str | None = None


def _parse_iso_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ExecutionKernelRepositoryError(f"invalid datetime={value!r}") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def knowledge_entry_is_stale(entry: KnowledgeEntry, *, now: datetime | None = None) -> bool:
    """Stale = past valid_until or past review_due_at. Never means deleted."""
    clock = now or datetime.now(timezone.utc)
    valid_until = _parse_iso_utc(entry.valid_until)
    if valid_until is not None and clock > valid_until:
        return True
    review_due = _parse_iso_utc(entry.review_due_at)
    if review_due is not None and clock > review_due:
        return True
    return False


def knowledge_entry_is_current(entry: KnowledgeEntry) -> bool:
    """Current for capsule ranking: ACTIVE and not superseded by another entry."""
    if entry.status != "ACTIVE":
        return False
    if entry.superseded_by_entry_id:
        return False
    return True


@dataclass
class InMemoryKnowledgeStore:
    _entries: list[KnowledgeEntry] = field(default_factory=list)

    def upsert(self, entry: KnowledgeEntry) -> None:
        if entry.knowledge_kind not in KNOWLEDGE_KINDS:
            raise ExecutionKernelRepositoryError(f"invalid knowledge_kind={entry.knowledge_kind!r}")
        if entry.status not in {"ACTIVE", "SUPERSEDED", "DEPRECATED"}:
            raise ExecutionKernelRepositoryError(f"invalid knowledge status={entry.status!r}")
        if entry.confidence not in KNOWLEDGE_CONFIDENCE:
            raise ExecutionKernelRepositoryError(f"invalid knowledge confidence={entry.confidence!r}")
        # Validate datetime fields early (fail closed on bad input).
        _parse_iso_utc(entry.valid_from)
        _parse_iso_utc(entry.valid_until)
        _parse_iso_utc(entry.review_due_at)
        self._entries = [
            e
            for e in self._entries
            if not (e.tenant_id == entry.tenant_id and e.knowledge_entry_id == entry.knowledge_entry_id)
        ]
        self._entries.append(entry)

    def get(self, *, tenant_id: str, knowledge_entry_id: str) -> KnowledgeEntry | None:
        """Direct retrieval by ID — includes superseded/deprecated/stale entries."""
        tenant_id = str(tenant_id or "").strip()
        knowledge_entry_id = str(knowledge_entry_id or "").strip()
        for e in self._entries:
            if e.tenant_id == tenant_id and e.knowledge_entry_id == knowledge_entry_id:
                return e
        return None

    def watermark(self, *, tenant_id: str = "") -> int:
        if not tenant_id:
            return sum(e.revision for e in self._entries)
        return sum(e.revision for e in self._entries if e.tenant_id == tenant_id)

    def candidates_for_capability(self, *, capability_id: str, tenant_id: str = "") -> list[KnowledgeEntry]:
        out: list[KnowledgeEntry] = []
        for e in self._entries:
            # Include ACTIVE current entries and ACTIVE-but-superseded entries so
            # ranking can prove superseded never outranks its replacement; stale
            # (time-based) ACTIVE entries remain candidates and are flagged in-capsule.
            if e.status != "ACTIVE":
                continue
            if tenant_id and e.tenant_id and e.tenant_id != tenant_id:
                continue
            if e.capability_id == capability_id:
                out.append(e)
            elif capability_id in e.applicable_capability_ids:
                out.append(e)
        return out


def _rank_key(entry: KnowledgeEntry, *, capability_id: str, problem_id: str) -> tuple:
    # EX4-S002: superseded entries always sort after current ones.
    superseded = 0 if knowledge_entry_is_current(entry) else 1
    same_cap = 0 if entry.capability_id == capability_id else 1
    same_problem = 0 if entry.problem_id == problem_id else 1
    pattern_cross = 0 if entry.knowledge_kind == "pattern" and capability_id in entry.applicable_capability_ids else 1
    general = 0 if entry.knowledge_kind == "lesson_learned" else 1
    return (superseded, same_cap, same_problem, pattern_cross, general, entry.knowledge_entry_id)


@dataclass(frozen=True)
class ContextCapsule:
    schema_version: str
    tenant_id: str
    project: dict[str, Any]
    capability: dict[str, Any]
    problem: dict[str, Any]
    work_item: dict[str, Any]
    dependencies: list[dict[str, Any]]
    repository: dict[str, Any]
    knowledge: list[dict[str, Any]]
    decisions: list[dict[str, Any]]
    constraints: list[Any]
    acceptance: list[Any]
    omitted_knowledge_ids: list[str]
    max_tokens: int
    estimated_tokens: int
    capsule_content_sha256: str

    def as_hash_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "tenant_id": self.tenant_id,
            "project": self.project,
            "capability": self.capability,
            "problem": self.problem,
            "work_item": self.work_item,
            "dependencies": self.dependencies,
            "repository": self.repository,
            "knowledge": self.knowledge,
            "decisions": self.decisions,
            "constraints": self.constraints,
            "acceptance": self.acceptance,
            "omitted_knowledge_ids": self.omitted_knowledge_ids,
            "max_tokens": self.max_tokens,
            "estimated_tokens": self.estimated_tokens,
        }


class ContextBuilder:
    def __init__(
        self,
        *,
        projection: InMemoryProjectionStore,
        dependencies: InMemoryDependencyGraph,
        knowledge: InMemoryKnowledgeStore,
    ) -> None:
        self._projection = projection
        self._dependencies = dependencies
        self._knowledge = knowledge
        self._cache: dict[tuple[str, str, int, int, int], ContextCapsule] = {}

    def build(
        self,
        *,
        tenant_id: str,
        work_item_id: str,
        max_tokens: int = 4000,
        decisions: list[dict[str, Any]] | None = None,
        constraints: list[Any] | None = None,
        acceptance: list[Any] | None = None,
        repository: dict[str, Any] | None = None,
    ) -> ContextCapsule:
        tenant_id = _require_tenant(tenant_id)
        if max_tokens < 1:
            raise ExecutionKernelRepositoryError("max_tokens must be positive")

        wi = self._projection.get(tenant_id=tenant_id, aggregate_type="work_item", aggregate_id=work_item_id)
        if wi is None:
            raise ExecutionKernelRepositoryError(f"work_item not found: {work_item_id}")

        problem_id = wi.parent_id or str(wi.payload.get("parent_id") or "")
        problem = self._projection.get(tenant_id=tenant_id, aggregate_type="problem", aggregate_id=problem_id)
        capability_id = (problem.parent_id if problem else None) or str(
            (problem.payload.get("parent_id") if problem else None) or wi.payload.get("capability_id") or ""
        )
        capability = self._projection.get(
            tenant_id=tenant_id, aggregate_type="capability", aggregate_id=capability_id
        ) if capability_id else None
        project_id = (capability.parent_id if capability else None) or str(
            (capability.payload.get("parent_id") if capability else None) or ""
        )
        project = self._projection.get(tenant_id=tenant_id, aggregate_type="project", aggregate_id=project_id) if project_id else None

        watermark = self._projection.watermark(tenant_id=tenant_id)
        knowledge_watermark = self._knowledge.watermark(tenant_id=tenant_id)
        repo_fp = canonical_content_hash({"repository": dict(repository or {})})[:16]
        cache_key = (tenant_id, work_item_id, watermark, knowledge_watermark, max_tokens, repo_fp)
        if cache_key in self._cache:
            return self._cache[cache_key]

        dep_ids = sorted(self._dependencies.active_dependencies(tenant_id=tenant_id, work_item_id=work_item_id))
        dep_rows: list[dict[str, Any]] = []
        for dep_id in dep_ids:
            dep = self._projection.get(tenant_id=tenant_id, aggregate_type="work_item", aggregate_id=dep_id)
            dep_rows.append(
                {
                    "work_item_id": dep_id,
                    "status": dep.status if dep else "UNKNOWN",
                    "evidence_refs": list((dep.payload.get("evidence_refs") if dep else None) or []),
                }
            )

        ranked = sorted(
            self._knowledge.candidates_for_capability(
                capability_id=capability_id or "", tenant_id=tenant_id
            ),
            key=lambda e: _rank_key(e, capability_id=capability_id or "", problem_id=problem_id or ""),
        )

        # Required sections token budget first (rough).
        project_obj = {
            "project_id": project_id,
            "vision_summary": _normalize_content(str((project.payload.get("vision_summary") if project else "") or "")),
            "requirements_summary": _normalize_content(
                str((project.payload.get("requirements_summary") if project else "") or "")
            ),
        }
        capability_obj = {
            "capability_id": capability_id,
            "name": _normalize_content(str((capability.payload.get("name") if capability else "") or "")),
            "roadmap_position": (capability.payload.get("roadmap_position") if capability else None),
        }
        problem_obj = {
            "problem_id": problem_id,
            "status": problem.status if problem else "UNKNOWN",
            "charter_summary": _normalize_content(str((problem.payload.get("charter_summary") if problem else "") or "")),
        }
        work_item_obj = {
            "work_item_id": work_item_id,
            "status": wi.status,
            "title": _normalize_content(str(wi.payload.get("title") or "")),
            "payload": {k: v for k, v in wi.payload.items() if k not in {"secret", "secrets", "api_key", "token"}},
        }
        # Strip secrets if present
        for forbidden in ("secret", "secrets", "api_key", "token", "password"):
            work_item_obj["payload"].pop(forbidden, None)

        base_blob = json.dumps(
            {
                "project": project_obj,
                "capability": capability_obj,
                "problem": problem_obj,
                "work_item": work_item_obj,
                "dependencies": dep_rows,
            },
            sort_keys=True,
        )
        used = estimate_tokens(base_blob)
        if used > max_tokens:
            raise ExecutionKernelRepositoryError(
                f"REQUIRED_CONTEXT_EXCEEDS_TOKEN_CAP required={used} cap={max_tokens}"
            )

        knowledge_items: list[dict[str, Any]] = []
        omitted: list[str] = []
        for entry in ranked:
            content = _normalize_content(entry.content)
            item: dict[str, Any] = {
                "knowledge_entry_id": entry.knowledge_entry_id,
                "knowledge_kind": entry.knowledge_kind,
                "content": content,
            }
            # EX4-S002: flag stale in results; never delete. Omit key when fresh to keep hashes stable.
            if knowledge_entry_is_stale(entry):
                item["stale"] = True
            if entry.superseded_by_entry_id:
                item["superseded_by_entry_id"] = entry.superseded_by_entry_id
            cost = estimate_tokens(json.dumps(item, sort_keys=True))
            if used + cost > max_tokens:
                omitted.append(entry.knowledge_entry_id)
                continue
            knowledge_items.append(item)
            used += cost

        omitted_sorted = sorted(omitted)
        repo_obj = dict(repository or {})
        decisions_obj = list(decisions or [])
        constraints_obj = list(constraints or [])
        acceptance_obj = list(acceptance or [])

        hash_input = {
            "schema_version": SCHEMA_VERSION,
            "tenant_id": tenant_id,
            "project": project_obj,
            "capability": capability_obj,
            "problem": problem_obj,
            "work_item": work_item_obj,
            "dependencies": dep_rows,
            "repository": repo_obj,
            "knowledge": knowledge_items,
            "decisions": decisions_obj,
            "constraints": constraints_obj,
            "acceptance": acceptance_obj,
            "omitted_knowledge_ids": omitted_sorted,
            "max_tokens": max_tokens,
            "estimated_tokens": used,
        }
        digest = canonical_content_hash(hash_input)
        capsule = ContextCapsule(
            schema_version=SCHEMA_VERSION,
            tenant_id=tenant_id,
            project=project_obj,
            capability=capability_obj,
            problem=problem_obj,
            work_item=work_item_obj,
            dependencies=dep_rows,
            repository=repo_obj,
            knowledge=knowledge_items,
            decisions=decisions_obj,
            constraints=constraints_obj,
            acceptance=acceptance_obj,
            omitted_knowledge_ids=omitted_sorted,
            max_tokens=max_tokens,
            estimated_tokens=used,
            capsule_content_sha256=digest,
        )
        self._cache[cache_key] = capsule
        return capsule


def capsule_to_contract_dict(capsule: ContextCapsule) -> dict[str, Any]:
    """EX4-S001: external contract payload — every hash field + digest."""
    payload = capsule.as_hash_payload()
    payload["capsule_content_sha256"] = capsule.capsule_content_sha256
    return payload


__all__ = [
    "SCHEMA_VERSION",
    "KNOWLEDGE_KINDS",
    "KNOWLEDGE_CONFIDENCE",
    "KnowledgeEntry",
    "InMemoryKnowledgeStore",
    "ContextCapsule",
    "ContextBuilder",
    "canonical_content_hash",
    "estimate_tokens",
    "capsule_to_contract_dict",
    "knowledge_entry_is_stale",
    "knowledge_entry_is_current",
]
