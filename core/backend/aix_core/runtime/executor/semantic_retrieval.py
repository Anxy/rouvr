"""EX4-R3-P9-S001: Semantic retrieval capability — contract + in-process pilot.

Technology decision (recorded):
  - Preferred durable path: PostgreSQL + pgvector on Hyeve footing (ADR-015).
  - Rejected for first slice: dedicated vector database.
  - Ranked retrieval (`_rank_key`) is NOT replaced — semantic search is additive.
  - In-process pilot uses deterministic hashed bag-of-words (+ lexical expansion)
    so the capability contract is testable without gateway embeddings or pgvector.
  - Production embeddings should come from Hyeve model gateway (P4) when wired.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from typing import Any, Literal

from backend.aix_core.runtime.executor.context_builder import (
    InMemoryKnowledgeStore,
    knowledge_entry_is_current,
)
from backend.infrastructure.postgres.execution_kernel.repository import ExecutionKernelRepositoryError

_TOKEN_RE = re.compile(r"[a-z0-9]+")

EmbeddingBackend = Literal["hashed_bow_v1", "pgvector"]

# Capability / technology constants — first-class, not comments-only.
TECHNOLOGY_DECISION = {
    "capability": "semantic_retrieval",
    "preferred_durable": "postgres_pgvector",
    "rejected_first_slice": ("dedicated_vector_db",),
    "in_process_pilot": "hashed_bow_v1",
    "replaces_ranked_retrieval": False,
    "embedding_source_production": "hyeve_model_gateway",  # P4 when integrated
    "rationale": (
        "Hyeve already runs PostgreSQL; pgvector extends that footing without a "
        "parallel platform. Dedicated vector DBs add ops cost without proven "
        "workload evidence. Ranked retrieval remains for structured scope filters."
    ),
}

# Named queries where rule-based ranking is insufficient (entry criteria evidence).
RANKING_FAILURE_QUERIES: tuple[dict[str, str], ...] = (
    {
        "id": "SQ1",
        "query": "How do we enroll devices before attaching scan codes?",
        "why_ranking_fails": (
            "Capsule ranking ignores content similarity; only capability/problem/kind "
            "and supersession order. Paraphrase of a lesson is invisible to `_rank_key`."
        ),
    },
    {
        "id": "SQ2",
        "query": "device identity binding patterns",
        "why_ranking_fails": (
            "Concept search has no index; fingerprint clustering (P7) requires near-identical "
            "normalized text, not conceptual neighbourhood."
        ),
    },
    {
        "id": "SQ3",
        "query": "idempotent registration before barcodes",
        "why_ranking_fails": (
            "Cross-problem reuse: same-capability filter helps only when IDs match; "
            "semantically related lessons on other problems stay demoted or omitted."
        ),
    },
)

# Pilot-only lexical expansion so paraphrase SQ1 is demonstrable without a model.
# Production path replaces this with gateway embeddings + pgvector.
_PILOT_SYNONYMS: dict[str, tuple[str, ...]] = {
    "enroll": ("register", "registration", "enrolling"),
    "enrollment": ("registration", "register"),
    "register": ("enroll", "enrollment", "registration"),
    "registration": ("enroll", "enrollment", "register"),
    "scan": ("barcode", "barcodes"),
    "scans": ("barcode", "barcodes"),
    "barcode": ("scan", "code"),
    "barcodes": ("scan", "code"),
    "codes": ("barcode", "barcodes"),
    "identity": ("device", "registration", "barcode"),
    "binding": ("attach", "before", "barcode"),
    "attach": ("binding", "before"),
    "attaching": ("binding", "before"),
    "devices": ("device",),
    "device": ("devices",),
}

DEFAULT_EMBED_DIMS = 256


def _require_tenant(tenant_id: str) -> str:
    normalized = str(tenant_id or "").strip()
    if not normalized:
        raise ExecutionKernelRepositoryError("tenant_id is required (fail closed)")
    return normalized


def tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(str(text or "").lower())


def expand_tokens(tokens: list[str]) -> list[str]:
    """Pilot lexical expansion — not a substitute for real embeddings."""
    out: list[str] = list(tokens)
    for tok in tokens:
        out.extend(_PILOT_SYNONYMS.get(tok, ()))
    return out


def embed_text(text: str, *, dims: int = DEFAULT_EMBED_DIMS) -> list[float]:
    """Deterministic hashed bag-of-words embedding (L2-normalised).

    Stable across processes; suitable for tests and local pilots.
    Not a neural embedding — swap for gateway vectors + pgvector in durable path.
    """
    if dims < 8:
        raise ExecutionKernelRepositoryError("dims must be >= 8")
    vec = [0.0] * dims
    tokens = expand_tokens(tokenize(text))
    if not tokens:
        return vec
    for tok in tokens:
        digest = hashlib.sha256(tok.encode("utf-8")).digest()
        idx = int.from_bytes(digest[:4], "big") % dims
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vec[idx] += sign
    norm = math.sqrt(sum(v * v for v in vec))
    if norm <= 0.0:
        return vec
    return [v / norm for v in vec]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    return float(sum(x * y for x, y in zip(a, b, strict=True)))


@dataclass(frozen=True)
class SemanticHit:
    knowledge_entry_id: str
    content: str
    knowledge_kind: str
    capability_id: str | None
    problem_id: str | None
    score: float
    backend: str
    current: bool


def semantic_hit_to_dict(hit: SemanticHit) -> dict[str, Any]:
    return {
        "knowledge_entry_id": hit.knowledge_entry_id,
        "content": hit.content,
        "knowledge_kind": hit.knowledge_kind,
        "capability_id": hit.capability_id,
        "problem_id": hit.problem_id,
        "score": hit.score,
        "backend": hit.backend,
        "current": hit.current,
    }


def search_semantic(
    knowledge: InMemoryKnowledgeStore,
    *,
    tenant_id: str,
    query: str,
    limit: int = 5,
    min_score: float = 0.05,
    capability_id: str | None = None,
    knowledge_kinds: list[str] | tuple[str, ...] | None = None,
    dims: int = DEFAULT_EMBED_DIMS,
    backend: EmbeddingBackend = "hashed_bow_v1",
) -> list[SemanticHit]:
    """Semantic similarity over the Knowledge corpus. Additive to ranked retrieval."""
    tenant_id = _require_tenant(tenant_id)
    query = str(query or "").strip()
    if not query:
        raise ExecutionKernelRepositoryError("query is required")
    if limit < 1:
        raise ExecutionKernelRepositoryError("limit must be >= 1")
    if backend != "hashed_bow_v1":
        raise ExecutionKernelRepositoryError(
            f"backend={backend!r} not available in-process; durable target is postgres_pgvector"
        )

    qvec = embed_text(query, dims=dims)
    kinds = set(knowledge_kinds) if knowledge_kinds else None
    hits: list[SemanticHit] = []
    for entry in knowledge._entries:  # noqa: SLF001 — corpus scan
        if entry.tenant_id != tenant_id:
            continue
        if entry.status != "ACTIVE":
            continue
        if kinds is not None and entry.knowledge_kind not in kinds:
            continue
        if capability_id and entry.capability_id and entry.capability_id != capability_id:
            if capability_id not in entry.applicable_capability_ids:
                continue
        score = cosine_similarity(qvec, embed_text(entry.content, dims=dims))
        if score < min_score:
            continue
        hits.append(
            SemanticHit(
                knowledge_entry_id=entry.knowledge_entry_id,
                content=entry.content,
                knowledge_kind=entry.knowledge_kind,
                capability_id=entry.capability_id,
                problem_id=entry.problem_id,
                score=round(score, 6),
                backend=backend,
                current=knowledge_entry_is_current(entry),
            )
        )
    hits.sort(key=lambda h: (-h.score, h.knowledge_entry_id))
    return hits[:limit]


def technology_decision_dict() -> dict[str, Any]:
    return {
        **TECHNOLOGY_DECISION,
        "ranking_failure_queries": list(RANKING_FAILURE_QUERIES),
        "embedding_dims_pilot": DEFAULT_EMBED_DIMS,
    }


__all__ = [
    "TECHNOLOGY_DECISION",
    "RANKING_FAILURE_QUERIES",
    "DEFAULT_EMBED_DIMS",
    "SemanticHit",
    "embed_text",
    "cosine_similarity",
    "search_semantic",
    "semantic_hit_to_dict",
    "technology_decision_dict",
]
