"""EX5-S1: Durable Postgres stores for P8–P12 intelligence surfaces.

Mirrors in-memory KernelService APIs so PostgresKernelService has full parity
and the router never returns HTTP 501 for these features.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import asyncpg  # type: ignore[import-untyped]

from backend.infrastructure.postgres.execution_kernel.db_pool import acquire_pooled_connection
from backend.aix_core.runtime.executor.context_builder import (
    InMemoryKnowledgeStore,
    knowledge_entry_is_current,
)
from backend.aix_core.runtime.executor.knowledge_graph import InMemoryKnowledgeGraph
from backend.aix_core.runtime.executor.learning_lifecycle import (
    PROPOSAL_LEVEL_FOR_SCOPE,
    REQUIRES_EXPLICIT_POLICY,
    SCOPES,
    STAGES,
    LearningRecord,
    _FORWARD,
)
from backend.aix_core.runtime.executor.organizational_reasoning import (
    golden_scenarios,
    reason_organizationally,
)
from backend.aix_core.runtime.executor.planning_engine import (
    autonomy_decision_to_dict,
    bounded_autonomy_for_mutation,
    build_plan_mutation_proposal_payload,
    decompose_plan,
    detect_replan_triggers,
    evaluate_plan_quality,
    evaluate_plan_quality_with_learning,
    interpret_objective,
    simulate_plan,
)
from backend.infrastructure.postgres.execution_kernel.repository import ExecutionKernelRepositoryError
from backend.infrastructure.postgres.execution_kernel.schema import (
    ENTERPRISE_AUDIT_TABLE,
    EXECUTION_ROUTE_AUDIT_TABLE,
    EXECUTION_ROUTE_TABLE,
    KNOWLEDGE_EMBEDDINGS_TABLE,
    LEARNING_LIFECYCLE_TABLE,
    ensure_full_execution_kernel_schema,
)
from backend.aix_core.runtime.executor.execution_route import (
    ExecutionRouteContract,
    RoleRoute,
)
from backend.aix_core.runtime.executor.semantic_retrieval import (
    DEFAULT_EMBED_DIMS,
    SemanticHit,
    cosine_similarity,
    embed_text,
    search_semantic,
    semantic_hit_to_dict,
    technology_decision_dict,
)


async def pgvector_extension_available(conn: asyncpg.Connection) -> bool:
    """EX5-S5: runtime capability check — is the `vector` extension actually
    installed on this Postgres server? Never assumed; the native pgvector
    query path is only used when this returns True. Confirmed False on the
    local dev database this session (pgvector is not installed here) —
    installing a native Postgres extension is a system-level change this
    session does not make unilaterally."""
    row = await conn.fetchrow(
        "SELECT 1 FROM pg_extension WHERE extname = 'vector'"
    )
    return row is not None


def _require_tenant(tenant_id: str) -> str:
    normalized = str(tenant_id or "").strip()
    if not normalized:
        raise ExecutionKernelRepositoryError("tenant_id is required (fail closed)")
    return normalized


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, str):
        return list(json.loads(value))
    if isinstance(value, (list, tuple)):
        return list(value)
    return list(value)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _learning_from_row(row: asyncpg.Record) -> LearningRecord:
    return LearningRecord(
        learning_id=row["learning_id"],
        tenant_id=row["tenant_id"],
        stage=row["stage"],
        summary=row["summary"],
        scope=row["scope"],
        confidence=row["confidence"],
        influence_level=row["influence_level"],
        source_work_item_id=row["source_work_item_id"],
        source_knowledge_ids=tuple(str(x) for x in _as_list(row["source_knowledge_ids"])),
        capability_id=row["capability_id"],
        problem_id=row["problem_id"],
        proposal_id=row["proposal_id"],
        required_proposal_level=int(row["required_proposal_level"]),
        approved_by_actor=row["approved_by_actor"],
        active_knowledge_entry_id=row["active_knowledge_entry_id"],
        auto_promote_allowed=bool(row["auto_promote_allowed"]),
    )


class PostgresLearningStore:
    """Durable learning lifecycle (P8) — same transitions as InMemoryLearningLifecycleStore."""

    def __init__(
        self,
        database_url: str,
        *,
        ensure_schema: bool = True,
        command_timeout_seconds: float = 5.0,
    ) -> None:
        if not database_url:
            raise ExecutionKernelRepositoryError("database_url is required")
        self._database_url = database_url
        self._ensure_schema = ensure_schema
        self._command_timeout_seconds = command_timeout_seconds

    async def _connect(self) -> asyncpg.Connection:
        try:
            return await acquire_pooled_connection(
                self._database_url,
                ensure_schema=self._ensure_schema,
                ensure_schema_fn=ensure_full_execution_kernel_schema,
                command_timeout=self._command_timeout_seconds,
            )
        except (TimeoutError, OSError, asyncpg.PostgresError) as exc:
            raise ExecutionKernelRepositoryError("learning store connection failed") from exc

    async def _save(self, conn: asyncpg.Connection, rec: LearningRecord) -> LearningRecord:
        await conn.execute(
            f"""
            INSERT INTO {LEARNING_LIFECYCLE_TABLE} (
                learning_id, tenant_id, stage, summary, scope, confidence, influence_level,
                source_work_item_id, source_knowledge_ids, capability_id, problem_id,
                proposal_id, required_proposal_level, approved_by_actor,
                active_knowledge_entry_id, auto_promote_allowed, updated_at
            ) VALUES (
                $1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb,$10,$11,$12,$13,$14,$15,$16, now()
            )
            ON CONFLICT (tenant_id, learning_id) DO UPDATE SET
                stage = EXCLUDED.stage,
                summary = EXCLUDED.summary,
                scope = EXCLUDED.scope,
                confidence = EXCLUDED.confidence,
                influence_level = EXCLUDED.influence_level,
                source_work_item_id = EXCLUDED.source_work_item_id,
                source_knowledge_ids = EXCLUDED.source_knowledge_ids,
                capability_id = EXCLUDED.capability_id,
                problem_id = EXCLUDED.problem_id,
                proposal_id = EXCLUDED.proposal_id,
                required_proposal_level = EXCLUDED.required_proposal_level,
                approved_by_actor = EXCLUDED.approved_by_actor,
                active_knowledge_entry_id = EXCLUDED.active_knowledge_entry_id,
                auto_promote_allowed = EXCLUDED.auto_promote_allowed,
                updated_at = now()
            """,
            rec.learning_id,
            rec.tenant_id,
            rec.stage,
            rec.summary,
            rec.scope,
            rec.confidence,
            rec.influence_level,
            rec.source_work_item_id,
            json.dumps(list(rec.source_knowledge_ids)),
            rec.capability_id,
            rec.problem_id,
            rec.proposal_id,
            rec.required_proposal_level,
            rec.approved_by_actor,
            rec.active_knowledge_entry_id,
            rec.auto_promote_allowed,
        )
        return rec

    async def observe(
        self,
        *,
        tenant_id: str,
        summary: str,
        source_work_item_id: str | None = None,
        source_knowledge_ids: list[str] | tuple[str, ...] | None = None,
        capability_id: str | None = None,
        problem_id: str | None = None,
        learning_id: str | None = None,
    ) -> LearningRecord:
        tenant_id = _require_tenant(tenant_id)
        summary = str(summary or "").strip()
        if not summary:
            raise ExecutionKernelRepositoryError("summary is required")
        rec = LearningRecord(
            learning_id=str(learning_id or f"lrn_{uuid4().hex[:12]}"),
            tenant_id=tenant_id,
            stage="OBSERVATION",
            summary=summary,
            source_work_item_id=source_work_item_id,
            source_knowledge_ids=tuple(source_knowledge_ids or ()),
            capability_id=capability_id,
            problem_id=problem_id,
            required_proposal_level=PROPOSAL_LEVEL_FOR_SCOPE["local"],
        )
        conn = await self._connect()
        try:
            async with conn.transaction():
                return await self._save(conn, rec)
        except (TimeoutError, OSError, asyncpg.PostgresError) as exc:
            raise ExecutionKernelRepositoryError("learning observe failed") from exc
        finally:
            await conn.close()

    async def get(self, *, tenant_id: str, learning_id: str) -> LearningRecord | None:
        tenant_id = _require_tenant(tenant_id)
        conn = await self._connect()
        try:
            row = await conn.fetchrow(
                f"SELECT * FROM {LEARNING_LIFECYCLE_TABLE} WHERE tenant_id = $1 AND learning_id = $2",
                tenant_id,
                learning_id,
            )
        except (TimeoutError, OSError, asyncpg.PostgresError) as exc:
            raise ExecutionKernelRepositoryError("learning get failed") from exc
        finally:
            await conn.close()
        return _learning_from_row(row) if row else None

    async def list(
        self, *, tenant_id: str, stage: str | None = None, active_only: bool = False
    ) -> list[LearningRecord]:
        tenant_id = _require_tenant(tenant_id)
        conn = await self._connect()
        try:
            rows = await conn.fetch(
                f"SELECT * FROM {LEARNING_LIFECYCLE_TABLE} WHERE tenant_id = $1 ORDER BY learning_id ASC",
                tenant_id,
            )
        except (TimeoutError, OSError, asyncpg.PostgresError) as exc:
            raise ExecutionKernelRepositoryError("learning list failed") from exc
        finally:
            await conn.close()
        out: list[LearningRecord] = []
        for row in rows:
            rec = _learning_from_row(row)
            if stage and rec.stage != stage:
                continue
            if active_only and rec.stage not in {"ACTIVE_KNOWLEDGE", "MONITORED_USE", "REVALIDATION"}:
                continue
            out.append(rec)
        return out

    async def _require(self, tenant_id: str, learning_id: str) -> LearningRecord:
        rec = await self.get(tenant_id=tenant_id, learning_id=learning_id)
        if rec is None:
            raise ExecutionKernelRepositoryError(f"learning record not found: {learning_id}")
        return rec

    async def classify_scope(
        self, *, tenant_id: str, learning_id: str, scope: str
    ) -> LearningRecord:
        from dataclasses import replace

        if scope not in SCOPES:
            raise ExecutionKernelRepositoryError(f"invalid scope={scope!r}")
        rec = await self._require(tenant_id, learning_id)
        level = PROPOSAL_LEVEL_FOR_SCOPE[scope]
        updated = replace(
            rec,
            scope=scope,
            required_proposal_level=level,
            auto_promote_allowed=False if scope == "organisation" else rec.auto_promote_allowed,
        )
        conn = await self._connect()
        try:
            async with conn.transaction():
                return await self._save(conn, updated)
        finally:
            await conn.close()

    async def set_confidence(
        self, *, tenant_id: str, learning_id: str, confidence: str
    ) -> LearningRecord:
        from dataclasses import replace

        if confidence not in {"unknown", "low", "medium", "high"}:
            raise ExecutionKernelRepositoryError(f"invalid confidence={confidence!r}")
        rec = await self._require(tenant_id, learning_id)
        updated = replace(rec, confidence=confidence)
        conn = await self._connect()
        try:
            async with conn.transaction():
                return await self._save(conn, updated)
        finally:
            await conn.close()

    async def set_influence_level(
        self, *, tenant_id: str, learning_id: str, influence_level: str
    ) -> LearningRecord:
        from dataclasses import replace
        from backend.aix_core.runtime.executor.learning_lifecycle import INFLUENCE_LEVELS

        if influence_level not in INFLUENCE_LEVELS:
            raise ExecutionKernelRepositoryError(f"invalid influence_level={influence_level!r}")
        rec = await self._require(tenant_id, learning_id)
        if influence_level in REQUIRES_EXPLICIT_POLICY and not rec.auto_promote_allowed:
            raise ExecutionKernelRepositoryError(
                "influence_level requires explicit auto_promote policy (not enabled by default)"
            )
        if rec.scope == "organisation" and influence_level in REQUIRES_EXPLICIT_POLICY:
            raise ExecutionKernelRepositoryError(
                "organisation scope cannot use automatic influence levels"
            )
        updated = replace(rec, influence_level=influence_level)
        conn = await self._connect()
        try:
            async with conn.transaction():
                return await self._save(conn, updated)
        finally:
            await conn.close()

    async def advance(self, *, tenant_id: str, learning_id: str, to_stage: str) -> LearningRecord:
        from dataclasses import replace

        rec = await self._require(tenant_id, learning_id)
        if to_stage not in STAGES:
            raise ExecutionKernelRepositoryError(f"invalid stage={to_stage!r}")
        allowed = _FORWARD.get(rec.stage, ())
        if to_stage not in allowed:
            raise ExecutionKernelRepositoryError(
                f"illegal transition {rec.stage} -> {to_stage}"
            )
        updated = replace(rec, stage=to_stage)
        conn = await self._connect()
        try:
            async with conn.transaction():
                return await self._save(conn, updated)
        finally:
            await conn.close()

    async def approve_activation(
        self,
        *,
        tenant_id: str,
        learning_id: str,
        actor_id: str,
        proposal_level: int,
        proposal_id: str | None = None,
        active_knowledge_entry_id: str | None = None,
    ) -> LearningRecord:
        from dataclasses import replace

        rec = await self._require(tenant_id, learning_id)
        if rec.stage != "AWAITING_APPROVAL":
            raise ExecutionKernelRepositoryError(
                f"approve requires AWAITING_APPROVAL, got {rec.stage}"
            )
        if proposal_level < rec.required_proposal_level:
            raise ExecutionKernelRepositoryError(
                f"proposal_level {proposal_level} < required {rec.required_proposal_level} for scope={rec.scope}"
            )
        if rec.scope == "organisation" and proposal_level < 4:
            raise ExecutionKernelRepositoryError(
                "organisation-scoped learning requires Proposal level 4"
            )
        actor_id = str(actor_id or "").strip()
        if not actor_id:
            raise ExecutionKernelRepositoryError("actor_id is required for approval")
        updated = replace(
            rec,
            stage="ACTIVE_KNOWLEDGE",
            approved_by_actor=actor_id,
            proposal_id=proposal_id,
            active_knowledge_entry_id=active_knowledge_entry_id,
            influence_level="recommendation_to_planner"
            if rec.influence_level == "retrieved_for_consideration"
            else rec.influence_level,
        )
        conn = await self._connect()
        try:
            async with conn.transaction():
                return await self._save(conn, updated)
        finally:
            await conn.close()

    async def reject(self, *, tenant_id: str, learning_id: str) -> LearningRecord:
        from dataclasses import replace

        rec = await self._require(tenant_id, learning_id)
        if rec.stage in {"SUPERSEDED", "REJECTED"}:
            return rec
        updated = replace(rec, stage="REJECTED")
        conn = await self._connect()
        try:
            async with conn.transaction():
                return await self._save(conn, updated)
        finally:
            await conn.close()


class PostgresSemanticStore:
    """P9 durable embedding index (hashed BoW pilot; pgvector is Sprint 5)."""

    def __init__(
        self,
        database_url: str,
        *,
        ensure_schema: bool = True,
        command_timeout_seconds: float = 5.0,
    ) -> None:
        if not database_url:
            raise ExecutionKernelRepositoryError("database_url is required")
        self._database_url = database_url
        self._ensure_schema = ensure_schema
        self._command_timeout_seconds = command_timeout_seconds

    async def _connect(self) -> asyncpg.Connection:
        try:
            return await acquire_pooled_connection(
                self._database_url,
                ensure_schema=self._ensure_schema,
                ensure_schema_fn=ensure_full_execution_kernel_schema,
                command_timeout=self._command_timeout_seconds,
            )
        except (TimeoutError, OSError, asyncpg.PostgresError) as exc:
            raise ExecutionKernelRepositoryError("semantic store connection failed") from exc

    async def upsert_embedding(
        self,
        *,
        tenant_id: str,
        knowledge_entry_id: str,
        content: str,
        embedding_model: str = "hashed_bow_v1",
        dims: int = DEFAULT_EMBED_DIMS,
    ) -> dict[str, Any]:
        tenant_id = _require_tenant(tenant_id)
        vector = embed_text(content, dims=dims)
        content_sha = hashlib.sha256(str(content or "").encode("utf-8")).hexdigest()
        conn = await self._connect()
        try:
            await conn.execute(
                f"""
                INSERT INTO {KNOWLEDGE_EMBEDDINGS_TABLE}
                    (tenant_id, knowledge_entry_id, embedding_model, dims, embedding, content_sha256, backend, updated_at)
                VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, now())
                ON CONFLICT (tenant_id, knowledge_entry_id, embedding_model) DO UPDATE SET
                    dims = EXCLUDED.dims,
                    embedding = EXCLUDED.embedding,
                    content_sha256 = EXCLUDED.content_sha256,
                    backend = EXCLUDED.backend,
                    updated_at = now()
                """,
                tenant_id,
                knowledge_entry_id,
                embedding_model,
                dims,
                json.dumps(vector),
                content_sha,
                embedding_model,
            )
        except (TimeoutError, OSError, asyncpg.PostgresError) as exc:
            raise ExecutionKernelRepositoryError("semantic upsert failed") from exc
        finally:
            await conn.close()
        return {
            "tenant_id": tenant_id,
            "knowledge_entry_id": knowledge_entry_id,
            "embedding_model": embedding_model,
            "dims": dims,
            "content_sha256": content_sha,
            "backend": embedding_model,
        }

    async def search(
        self,
        knowledge: InMemoryKnowledgeStore,
        *,
        tenant_id: str,
        query: str,
        limit: int = 5,
        min_score: float = 0.05,
        capability_id: str | None = None,
        knowledge_kinds: list[str] | None = None,
        dims: int = DEFAULT_EMBED_DIMS,
    ) -> list[dict[str, Any]]:
        """EX5-S5: durable search — reads embedding vectors back from the
        persisted `aix_execution_knowledge_embeddings` table (proving they
        survive a restart, not just recomputing hashed BoW fresh from the
        in-memory knowledge corpus each call), and cross-references
        kind/capability/current-status metadata from `knowledge`. Falls
        back to `search_semantic()`'s in-process recomputation only if no
        durable embedding rows exist yet for this tenant (e.g. before any
        `upsert_embedding` call has ever run)."""
        tenant_id = _require_tenant(tenant_id)
        query = str(query or "").strip()
        if not query:
            raise ExecutionKernelRepositoryError("query is required")
        conn = await self._connect()
        try:
            rows = await conn.fetch(
                f"""
                SELECT knowledge_entry_id, embedding, dims, backend
                FROM {KNOWLEDGE_EMBEDDINGS_TABLE}
                WHERE tenant_id = $1
                """,
                tenant_id,
            )
        except (TimeoutError, OSError, asyncpg.PostgresError) as exc:
            raise ExecutionKernelRepositoryError("semantic search failed") from exc
        finally:
            await conn.close()

        if not rows:
            # No durable embeddings persisted yet — fall back rather than
            # silently return empty results.
            return [
                semantic_hit_to_dict(h)
                for h in search_semantic(
                    knowledge, tenant_id=tenant_id, query=query, limit=limit,
                    min_score=min_score, capability_id=capability_id, knowledge_kinds=knowledge_kinds,
                )
            ]

        qvec = embed_text(query, dims=dims)
        kinds = set(knowledge_kinds) if knowledge_kinds else None
        hits: list[SemanticHit] = []
        for row in rows:
            entry = knowledge.get(tenant_id=tenant_id, knowledge_entry_id=row["knowledge_entry_id"])
            if entry is None or entry.status != "ACTIVE":
                continue
            if kinds is not None and entry.knowledge_kind not in kinds:
                continue
            if capability_id and entry.capability_id and entry.capability_id != capability_id:
                if capability_id not in entry.applicable_capability_ids:
                    continue
            stored_vector = json.loads(row["embedding"]) if isinstance(row["embedding"], str) else row["embedding"]
            if len(stored_vector) != len(qvec):
                continue  # dims mismatch (different embedding_model/version) — skip, don't crash
            score = cosine_similarity(qvec, stored_vector)
            if score < min_score:
                continue
            hits.append(
                SemanticHit(
                    knowledge_entry_id=entry.knowledge_entry_id, content=entry.content,
                    knowledge_kind=entry.knowledge_kind, capability_id=entry.capability_id,
                    problem_id=entry.problem_id, score=round(score, 6),
                    backend=f"postgres_durable/{row['backend']}", current=knowledge_entry_is_current(entry),
                )
            )
        hits.sort(key=lambda h: (-h.score, h.knowledge_entry_id))
        return [semantic_hit_to_dict(h) for h in hits[:limit]]

    def technology_decision(self) -> dict[str, Any]:
        return technology_decision_dict()


class PostgresPlanningStore:
    """P10 planning facade — draft plans are pure; mutations go through proposals."""

    def interpret(self, *, objective: str) -> dict[str, Any]:
        return interpret_objective(objective)

    def decompose(
        self,
        *,
        objective: str,
        max_work_items: int = 12,
        relevant_learning: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """EX5-S6: relevant_learning, when given, feeds
        evaluate_plan_quality_with_learning (Level 2 governance — a
        quality-score recommendation, never a silent decomposition
        rewrite)."""
        plan = decompose_plan(objective=objective, max_work_items=max_work_items)
        quality = evaluate_plan_quality_with_learning(plan, relevant_learning=relevant_learning)
        simulation = simulate_plan(plan)
        return {**plan, "quality": quality, "simulation": simulation}

    def autonomy(
        self,
        *,
        mutation_kind: str,
        problem_capability_id: str | None = None,
        affected_capabilities: list[str] | None = None,
        requests_new_capability: bool = False,
        charter_change: bool = False,
    ) -> dict[str, Any]:
        return autonomy_decision_to_dict(
            bounded_autonomy_for_mutation(
                mutation_kind,
                problem_capability_id=problem_capability_id,
                affected_capabilities=affected_capabilities,
                requests_new_capability=requests_new_capability,
                charter_change=charter_change,
            )
        )

    def replan_triggers(self, **kwargs: Any) -> list[dict[str, Any]]:
        return detect_replan_triggers(**kwargs)

    def mutation_payload(self, **kwargs: Any) -> dict[str, Any]:
        return build_plan_mutation_proposal_payload(**kwargs)

    def bounded_autonomy(self, *args: Any, **kwargs: Any) -> Any:
        return bounded_autonomy_for_mutation(*args, **kwargs)


class PostgresReasoningStore:
    """P11 reasoning facade over materialized knowledge + graph."""

    def golden_scenarios(self) -> list[dict[str, str]]:
        return golden_scenarios()

    def reason(
        self,
        knowledge: InMemoryKnowledgeStore,
        graph: InMemoryKnowledgeGraph,
        **kwargs: Any,
    ) -> dict[str, Any]:
        return reason_organizationally(knowledge, graph, **kwargs)


class PostgresEnterpriseStore:
    """P12 enterprise audit + scenario helpers."""

    def __init__(
        self,
        database_url: str,
        *,
        ensure_schema: bool = True,
        command_timeout_seconds: float = 5.0,
    ) -> None:
        if not database_url:
            raise ExecutionKernelRepositoryError("database_url is required")
        self._database_url = database_url
        self._ensure_schema = ensure_schema
        self._command_timeout_seconds = command_timeout_seconds

    async def _connect(self) -> asyncpg.Connection:
        try:
            return await acquire_pooled_connection(
                self._database_url,
                ensure_schema=self._ensure_schema,
                ensure_schema_fn=ensure_full_execution_kernel_schema,
                command_timeout=self._command_timeout_seconds,
            )
        except (TimeoutError, OSError, asyncpg.PostgresError) as exc:
            raise ExecutionKernelRepositoryError("enterprise store connection failed") from exc

    async def append(
        self, *, tenant_id: str, event_type: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        tenant_id = _require_tenant(tenant_id)
        event_id = f"eia_{uuid4().hex[:12]}"
        conn = await self._connect()
        try:
            # AIX-EXECUTION-006-S6: this previously passed _utc_now()'s
            # formatted str straight into a $N::timestamptz bind param,
            # which asyncpg rejects client-side (it type-checks bind values
            # against the column type before sending, unlike raw SQL string
            # interpolation) — every call to this method failed. Masked
            # until now because the one test that reached this code path
            # always hit an earlier, unrelated fixed-ID collision first.
            # now() computed server-side and read back avoids the mismatch.
            row = await conn.fetchrow(
                f"""
                INSERT INTO {ENTERPRISE_AUDIT_TABLE}
                    (event_id, tenant_id, event_type, payload, recorded_at)
                VALUES ($1, $2, $3, $4::jsonb, now())
                RETURNING recorded_at
                """,
                event_id,
                tenant_id,
                event_type,
                json.dumps(payload),
            )
        except (TimeoutError, OSError, asyncpg.PostgresError) as exc:
            raise ExecutionKernelRepositoryError("enterprise audit append failed") from exc
        finally:
            await conn.close()
        recorded_at = row["recorded_at"]
        return {
            "event_id": event_id,
            "tenant_id": tenant_id,
            "event_type": event_type,
            "payload": payload,
            "recorded_at": recorded_at.isoformat().replace("+00:00", "Z"),
        }

    async def list(self, *, tenant_id: str) -> list[dict[str, Any]]:
        tenant_id = _require_tenant(tenant_id)
        conn = await self._connect()
        try:
            rows = await conn.fetch(
                f"""
                SELECT event_id, tenant_id, event_type, payload, recorded_at
                FROM {ENTERPRISE_AUDIT_TABLE}
                WHERE tenant_id = $1
                ORDER BY recorded_at ASC, event_id ASC
                """,
                tenant_id,
            )
        except (TimeoutError, OSError, asyncpg.PostgresError) as exc:
            raise ExecutionKernelRepositoryError("enterprise audit list failed") from exc
        finally:
            await conn.close()
        out: list[dict[str, Any]] = []
        for row in rows:
            payload = row["payload"]
            if isinstance(payload, str):
                payload = json.loads(payload)
            recorded = row["recorded_at"]
            if hasattr(recorded, "isoformat"):
                recorded_at = recorded.isoformat().replace("+00:00", "Z")
            else:
                recorded_at = str(recorded)
            out.append(
                {
                    "event_id": row["event_id"],
                    "tenant_id": row["tenant_id"],
                    "event_type": row["event_type"],
                    "payload": dict(payload or {}),
                    "recorded_at": recorded_at,
                }
            )
        return out


class PostgresExecutionRouteStore:
    """EX5-S2: durable execution route contracts + audit (survives process restart)."""

    def __init__(
        self,
        database_url: str,
        *,
        ensure_schema: bool = True,
        command_timeout_seconds: float = 5.0,
    ) -> None:
        if not database_url:
            raise ExecutionKernelRepositoryError("database_url is required")
        self._database_url = database_url
        self._ensure_schema = ensure_schema
        self._command_timeout_seconds = command_timeout_seconds

    async def _connect(self, *, tenant_id: str | None = None) -> asyncpg.Connection:
        try:
            return await acquire_pooled_connection(
                self._database_url,
                ensure_schema=self._ensure_schema,
                ensure_schema_fn=ensure_full_execution_kernel_schema,
                command_timeout=self._command_timeout_seconds,
                tenant_id=tenant_id,
            )
        except (TimeoutError, OSError, asyncpg.PostgresError) as exc:
            raise ExecutionKernelRepositoryError("execution route store connection failed") from exc

    def _contract_from_row(self, row: asyncpg.Record) -> ExecutionRouteContract:
        executor_raw = row["executor_raw"]
        if isinstance(executor_raw, str):
            executor_raw = json.loads(executor_raw)
        verifier_raw = row["verifier_raw"]
        if isinstance(verifier_raw, str):
            verifier_raw = json.loads(verifier_raw) if verifier_raw else None
        bound = row["bound_at"]
        bound_at = bound.isoformat().replace("+00:00", "Z") if hasattr(bound, "isoformat") else str(bound)
        verifier = None
        if row["verifier_provider"] and row["verifier_model_id"]:
            verifier = RoleRoute(
                role="verifier",
                provider=row["verifier_provider"],
                model_id=row["verifier_model_id"],
                raw=dict(verifier_raw or {}),
            )
        return ExecutionRouteContract(
            route_id=row["route_id"],
            tenant_id=row["tenant_id"],
            work_item_id=row["work_item_id"],
            executor=RoleRoute(
                role="executor",
                provider=row["executor_provider"],
                model_id=row["executor_model_id"],
                raw=dict(executor_raw or {}),
            ),
            verifier=verifier,
            status=row["status"],
            bound_at=bound_at,
            cycle_id=row["cycle_id"],
            superseded_by=row["superseded_by"],
            switch_reason=row["switch_reason"],
        )

    async def _audit(
        self,
        conn: asyncpg.Connection,
        *,
        tenant_id: str,
        event_type: str,
        work_item_id: str,
        route_id: str,
        payload: dict[str, Any],
    ) -> None:
        await conn.execute(
            f"""
            INSERT INTO {EXECUTION_ROUTE_AUDIT_TABLE}
                (event_id, tenant_id, event_type, work_item_id, route_id, payload, recorded_at)
            VALUES ($1, $2, $3, $4, $5, $6::jsonb, now())
            """,
            f"rae_{uuid4().hex[:12]}",
            tenant_id,
            event_type,
            work_item_id,
            route_id,
            json.dumps(payload),
        )

    async def bind(self, contract: ExecutionRouteContract) -> ExecutionRouteContract:
        conn = await self._connect(tenant_id=contract.tenant_id)
        try:
            async with conn.transaction():
                active = await conn.fetchrow(
                    f"""
                    SELECT * FROM {EXECUTION_ROUTE_TABLE}
                    WHERE tenant_id = $1 AND work_item_id = $2 AND status = 'ACTIVE'
                    ORDER BY bound_at DESC LIMIT 1
                    """,
                    contract.tenant_id,
                    contract.work_item_id,
                )
                if active is not None:
                    await conn.execute(
                        f"""
                        UPDATE {EXECUTION_ROUTE_TABLE}
                        SET status = 'SUPERSEDED', superseded_by = $3
                        WHERE tenant_id = $1 AND route_id = $2
                        """,
                        contract.tenant_id,
                        active["route_id"],
                        contract.route_id,
                    )
                    await self._audit(
                        conn,
                        tenant_id=contract.tenant_id,
                        event_type="ROUTE_SUPERSEDED",
                        work_item_id=contract.work_item_id,
                        route_id=active["route_id"],
                        payload={"superseded_by": contract.route_id},
                    )
                await conn.execute(
                    f"""
                    INSERT INTO {EXECUTION_ROUTE_TABLE} (
                        route_id, tenant_id, work_item_id, status,
                        executor_provider, executor_model_id,
                        verifier_provider, verifier_model_id,
                        cycle_id, superseded_by, switch_reason,
                        executor_raw, verifier_raw, bound_at
                    ) VALUES (
                        $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12::jsonb,$13::jsonb, now()
                    )
                    """,
                    contract.route_id,
                    contract.tenant_id,
                    contract.work_item_id,
                    contract.status,
                    contract.executor.provider,
                    contract.executor.model_id,
                    contract.verifier.provider if contract.verifier else None,
                    contract.verifier.model_id if contract.verifier else None,
                    contract.cycle_id,
                    contract.superseded_by,
                    contract.switch_reason,
                    json.dumps(contract.executor.raw),
                    json.dumps(contract.verifier.raw) if contract.verifier else None,
                )
                await self._audit(
                    conn,
                    tenant_id=contract.tenant_id,
                    event_type="ROUTE_BOUND",
                    work_item_id=contract.work_item_id,
                    route_id=contract.route_id,
                    payload={
                        "executor": f"{contract.executor.provider}/{contract.executor.model_id}",
                        "status": contract.status,
                        "switch_reason": contract.switch_reason,
                    },
                )
        except (TimeoutError, OSError, asyncpg.PostgresError) as exc:
            raise ExecutionKernelRepositoryError("execution route bind failed") from exc
        finally:
            await conn.close()
        return contract

    async def get_active(
        self, *, tenant_id: str, work_item_id: str
    ) -> ExecutionRouteContract | None:
        tenant_id = _require_tenant(tenant_id)
        conn = await self._connect(tenant_id=tenant_id)
        try:
            row = await conn.fetchrow(
                f"""
                SELECT * FROM {EXECUTION_ROUTE_TABLE}
                WHERE tenant_id = $1 AND work_item_id = $2
                  AND status IN ('ACTIVE', 'FALLBACK')
                ORDER BY bound_at DESC LIMIT 1
                """,
                tenant_id,
                work_item_id,
            )
        except (TimeoutError, OSError, asyncpg.PostgresError) as exc:
            raise ExecutionKernelRepositoryError("execution route get failed") from exc
        finally:
            await conn.close()
        return self._contract_from_row(row) if row else None

    async def history(
        self, *, tenant_id: str, work_item_id: str | None = None
    ) -> list[ExecutionRouteContract]:
        tenant_id = _require_tenant(tenant_id)
        conn = await self._connect(tenant_id=tenant_id)
        try:
            if work_item_id:
                rows = await conn.fetch(
                    f"""
                    SELECT * FROM {EXECUTION_ROUTE_TABLE}
                    WHERE tenant_id = $1 AND work_item_id = $2
                    ORDER BY bound_at ASC
                    """,
                    tenant_id,
                    work_item_id,
                )
            else:
                rows = await conn.fetch(
                    f"""
                    SELECT * FROM {EXECUTION_ROUTE_TABLE}
                    WHERE tenant_id = $1
                    ORDER BY bound_at ASC
                    """,
                    tenant_id,
                )
        except (TimeoutError, OSError, asyncpg.PostgresError) as exc:
            raise ExecutionKernelRepositoryError("execution route history failed") from exc
        finally:
            await conn.close()
        return [self._contract_from_row(r) for r in rows]

    async def list_audit(
        self, *, tenant_id: str, work_item_id: str | None = None
    ) -> list[dict[str, Any]]:
        tenant_id = _require_tenant(tenant_id)
        conn = await self._connect(tenant_id=tenant_id)
        try:
            if work_item_id:
                rows = await conn.fetch(
                    f"""
                    SELECT * FROM {EXECUTION_ROUTE_AUDIT_TABLE}
                    WHERE tenant_id = $1 AND work_item_id = $2
                    ORDER BY recorded_at ASC
                    """,
                    tenant_id,
                    work_item_id,
                )
            else:
                rows = await conn.fetch(
                    f"""
                    SELECT * FROM {EXECUTION_ROUTE_AUDIT_TABLE}
                    WHERE tenant_id = $1
                    ORDER BY recorded_at ASC
                    """,
                    tenant_id,
                )
        except (TimeoutError, OSError, asyncpg.PostgresError) as exc:
            raise ExecutionKernelRepositoryError("execution route audit list failed") from exc
        finally:
            await conn.close()
        out: list[dict[str, Any]] = []
        for row in rows:
            payload = row["payload"]
            if isinstance(payload, str):
                payload = json.loads(payload)
            recorded = row["recorded_at"]
            recorded_at = (
                recorded.isoformat().replace("+00:00", "Z")
                if hasattr(recorded, "isoformat")
                else str(recorded)
            )
            out.append(
                {
                    "event_id": row["event_id"],
                    "tenant_id": row["tenant_id"],
                    "event_type": row["event_type"],
                    "work_item_id": row["work_item_id"],
                    "route_id": row["route_id"],
                    "payload": dict(payload or {}),
                    "recorded_at": recorded_at,
                }
            )
        return out

    async def switch(
        self,
        *,
        tenant_id: str,
        work_item_id: str,
        model_routes: dict[str, Any],
        reason: str,
        cycle_id: str | None = None,
        as_fallback: bool = False,
    ) -> ExecutionRouteContract:
        from backend.aix_core.runtime.executor.execution_route import contract_from_model_routes

        reason = str(reason or "").strip()
        if not reason:
            raise ExecutionKernelRepositoryError("switch reason is required")
        status = "FALLBACK" if as_fallback else "ACTIVE"
        contract = contract_from_model_routes(
            tenant_id=tenant_id,
            work_item_id=work_item_id,
            model_routes=model_routes,
            cycle_id=cycle_id,
            status=status,
            switch_reason=reason,
        )
        bound = await self.bind(contract)
        conn = await self._connect(tenant_id=tenant_id)
        try:
            await self._audit(
                conn,
                tenant_id=tenant_id,
                event_type="ROUTE_FALLBACK" if as_fallback else "ROUTE_SWITCHED",
                work_item_id=work_item_id,
                route_id=bound.route_id,
                payload={"reason": reason},
            )
        finally:
            await conn.close()
        return bound


# Aliases matching programme deliverable names
PostgresLearningLifecycleStore = PostgresLearningStore

__all__ = [
    "PostgresLearningStore",
    "PostgresLearningLifecycleStore",
    "PostgresSemanticStore",
    "PostgresPlanningStore",
    "PostgresReasoningStore",
    "PostgresEnterpriseStore",
    "PostgresExecutionRouteStore",
]
