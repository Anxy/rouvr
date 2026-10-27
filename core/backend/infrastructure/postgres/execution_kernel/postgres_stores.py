"""EX2-S006: Postgres-backed projection, dependency, and checkpoint stores."""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

import asyncpg  # type: ignore[import-untyped]

from backend.infrastructure.postgres.execution_kernel.db_pool import acquire_pooled_connection
from backend.aix_core.runtime.executor.checkpoint import REPOSITORY_STATE_FIELDS, Checkpoint
from backend.aix_core.runtime.executor.capability_registry import (
    InMemoryCapabilityRegistry,
    RegisteredCapability,
)
from backend.aix_core.runtime.executor.context_builder import (
    KNOWLEDGE_KINDS,
    InMemoryKnowledgeStore,
    KnowledgeEntry,
)
from backend.aix_core.runtime.executor.dependency_graph import DependencyEdgeEvent
from backend.aix_core.runtime.executor.knowledge_graph import (
    EDGE_TYPES,
    NODE_TYPES,
    InMemoryKnowledgeGraph,
    KnowledgeGraphEdge,
)
from backend.aix_core.runtime.executor.projection import TERMINAL_COMPLETE, ProjectionRow
from backend.infrastructure.postgres.execution_kernel.repository import (
    ExecutionEvent,
    ExecutionKernelRepositoryError,
)
from backend.infrastructure.postgres.execution_kernel.schema import (
    CHECKPOINT_TABLE,
    CAPABILITY_REGISTRY_TABLE,
    DEPENDENCY_EVENTS_TABLE,
    KNOWLEDGE_GRAPH_EDGES_TABLE,
    KNOWLEDGE_TABLE,
    PROJECTION_TABLE,
    ensure_full_execution_kernel_schema,
)


def _require_tenant(tenant_id: str) -> str:
    normalized = str(tenant_id or "").strip()
    if not normalized:
        raise ExecutionKernelRepositoryError("tenant_id is required (fail closed)")
    return normalized


def _payload(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        return json.loads(value)
    if isinstance(value, dict):
        return dict(value)
    return dict(value)


class PostgresProjectionStore:
    def __init__(self, database_url: str, *, ensure_schema: bool = True, command_timeout_seconds: float = 5.0) -> None:
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
            raise ExecutionKernelRepositoryError("projection store connection failed") from exc

    async def apply_event(self, event: ExecutionEvent) -> ProjectionRow:
        tenant_id = _require_tenant(event.tenant_id)
        payload = dict(event.payload)
        status = str(payload.get("status") or payload.get("phase") or "").strip() or "UNKNOWN"
        parent_id = payload.get("parent_id") or payload.get("problem_id") or payload.get("capability_id") or payload.get("project_id")
        parent_id = str(parent_id).strip() if parent_id else None
        conn = await self._connect(tenant_id=tenant_id)
        try:
            await conn.execute(
                f"""
                INSERT INTO {PROJECTION_TABLE}
                    (tenant_id, aggregate_type, aggregate_id, sequence_number, status, parent_id, payload, state_current)
                VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, TRUE)
                ON CONFLICT (tenant_id, aggregate_type, aggregate_id)
                DO UPDATE SET
                    sequence_number = EXCLUDED.sequence_number,
                    status = EXCLUDED.status,
                    parent_id = EXCLUDED.parent_id,
                    payload = EXCLUDED.payload,
                    state_current = TRUE,
                    updated_at = now()
                """,
                tenant_id,
                event.aggregate_type,
                event.aggregate_id,
                event.sequence_number,
                status,
                parent_id,
                json.dumps(payload, sort_keys=True),
            )
        except (TimeoutError, OSError, asyncpg.PostgresError) as exc:
            raise ExecutionKernelRepositoryError("projection upsert failed") from exc
        finally:
            await conn.close()
        return ProjectionRow(
            tenant_id=tenant_id,
            aggregate_type=event.aggregate_type,
            aggregate_id=event.aggregate_id,
            sequence_number=event.sequence_number,
            status=status,
            payload=payload,
            parent_id=parent_id,
            state_current=True,
        )

    async def get(self, *, tenant_id: str, aggregate_type: str, aggregate_id: str) -> ProjectionRow | None:
        tenant_id = _require_tenant(tenant_id)
        conn = await self._connect(tenant_id=tenant_id)
        try:
            row = await conn.fetchrow(
                f"""
                SELECT * FROM {PROJECTION_TABLE}
                WHERE tenant_id = $1 AND aggregate_type = $2 AND aggregate_id = $3 AND state_current = TRUE
                """,
                tenant_id,
                aggregate_type,
                aggregate_id,
            )
        except (TimeoutError, OSError, asyncpg.PostgresError) as exc:
            raise ExecutionKernelRepositoryError("projection read failed") from exc
        finally:
            await conn.close()
        if row is None:
            return None
        return ProjectionRow(
            tenant_id=row["tenant_id"],
            aggregate_type=row["aggregate_type"],
            aggregate_id=row["aggregate_id"],
            sequence_number=row["sequence_number"],
            status=row["status"],
            payload=_payload(row["payload"]),
            parent_id=row["parent_id"],
            state_current=bool(row["state_current"]),
        )

    async def list_children(self, *, tenant_id: str, aggregate_type: str, parent_id: str) -> list[ProjectionRow]:
        tenant_id = _require_tenant(tenant_id)
        conn = await self._connect(tenant_id=tenant_id)
        try:
            rows = await conn.fetch(
                f"""
                SELECT * FROM {PROJECTION_TABLE}
                WHERE tenant_id = $1 AND aggregate_type = $2 AND parent_id = $3 AND state_current = TRUE
                ORDER BY aggregate_id ASC
                """,
                tenant_id,
                aggregate_type,
                parent_id,
            )
        except (TimeoutError, OSError, asyncpg.PostgresError) as exc:
            raise ExecutionKernelRepositoryError("projection list failed") from exc
        finally:
            await conn.close()
        return [
            ProjectionRow(
                tenant_id=r["tenant_id"],
                aggregate_type=r["aggregate_type"],
                aggregate_id=r["aggregate_id"],
                sequence_number=r["sequence_number"],
                status=r["status"],
                payload=_payload(r["payload"]),
                parent_id=r["parent_id"],
                state_current=bool(r["state_current"]),
            )
            for r in rows
        ]

    async def list_current(self, *, tenant_id: str, aggregate_type: str) -> list[ProjectionRow]:
        """EX5-S1: list current projection rows by aggregate type (portfolio intelligence)."""
        tenant_id = _require_tenant(tenant_id)
        conn = await self._connect(tenant_id=tenant_id)
        try:
            rows = await conn.fetch(
                f"""
                SELECT * FROM {PROJECTION_TABLE}
                WHERE tenant_id = $1 AND aggregate_type = $2 AND state_current = TRUE
                ORDER BY aggregate_id ASC
                """,
                tenant_id,
                aggregate_type,
            )
        except (TimeoutError, OSError, asyncpg.PostgresError) as exc:
            raise ExecutionKernelRepositoryError("projection list_current failed") from exc
        finally:
            await conn.close()
        return [
            ProjectionRow(
                tenant_id=r["tenant_id"],
                aggregate_type=r["aggregate_type"],
                aggregate_id=r["aggregate_id"],
                sequence_number=r["sequence_number"],
                status=r["status"],
                payload=_payload(r["payload"]),
                parent_id=r["parent_id"],
                state_current=bool(r["state_current"]),
            )
            for r in rows
        ]

    async def get_many(
        self, *, tenant_id: str, aggregate_type: str, aggregate_ids: list[str]
    ) -> dict[str, ProjectionRow]:
        """AIX-EXECUTION-006-S3: batched form of `get` — one round trip for N
        aggregate ids instead of N. Used by next_actionable_work_items to
        replace its per-dependency `get()` loop."""
        tenant_id = _require_tenant(tenant_id)
        ids = sorted({str(a).strip() for a in aggregate_ids if str(a).strip()})
        if not ids:
            return {}
        conn = await self._connect(tenant_id=tenant_id)
        try:
            rows = await conn.fetch(
                f"""
                SELECT * FROM {PROJECTION_TABLE}
                WHERE tenant_id = $1 AND aggregate_type = $2
                  AND aggregate_id = ANY($3::text[]) AND state_current = TRUE
                """,
                tenant_id,
                aggregate_type,
                ids,
            )
        except (TimeoutError, OSError, asyncpg.PostgresError) as exc:
            raise ExecutionKernelRepositoryError("projection get_many failed") from exc
        finally:
            await conn.close()
        return {
            r["aggregate_id"]: ProjectionRow(
                tenant_id=r["tenant_id"],
                aggregate_type=r["aggregate_type"],
                aggregate_id=r["aggregate_id"],
                sequence_number=r["sequence_number"],
                status=r["status"],
                payload=_payload(r["payload"]),
                parent_id=r["parent_id"],
                state_current=bool(r["state_current"]),
            )
            for r in rows
        }

    async def watermark(self, *, tenant_id: str) -> int:
        tenant_id = _require_tenant(tenant_id)
        conn = await self._connect(tenant_id=tenant_id)
        try:
            value = await conn.fetchval(
                f"SELECT COALESCE(MAX(sequence_number), 0) FROM {PROJECTION_TABLE} WHERE tenant_id = $1",
                tenant_id,
            )
        except (TimeoutError, OSError, asyncpg.PostgresError) as exc:
            raise ExecutionKernelRepositoryError("projection watermark failed") from exc
        finally:
            await conn.close()
        return int(value or 0)


class PostgresDependencyGraph:
    def __init__(self, database_url: str, *, ensure_schema: bool = True, command_timeout_seconds: float = 5.0) -> None:
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
            raise ExecutionKernelRepositoryError("dependency store connection failed") from exc

    async def add_dependency(
        self,
        *,
        tenant_id: str,
        work_item_id: str,
        depends_on_work_item_id: str,
        command_id: str,
    ) -> DependencyEdgeEvent:
        return await self._write_edge(
            tenant_id=tenant_id,
            work_item_id=work_item_id,
            depends_on_work_item_id=depends_on_work_item_id,
            command_id=command_id,
            event_type="DEPENDENCY_ADDED",
        )

    async def remove_dependency(
        self,
        *,
        tenant_id: str,
        work_item_id: str,
        depends_on_work_item_id: str,
        command_id: str,
    ) -> DependencyEdgeEvent:
        return await self._write_edge(
            tenant_id=tenant_id,
            work_item_id=work_item_id,
            depends_on_work_item_id=depends_on_work_item_id,
            command_id=command_id,
            event_type="DEPENDENCY_REMOVED",
        )

    async def _write_edge(
        self,
        *,
        tenant_id: str,
        work_item_id: str,
        depends_on_work_item_id: str,
        command_id: str,
        event_type: str,
    ) -> DependencyEdgeEvent:
        tenant_id = _require_tenant(tenant_id)
        if not work_item_id.strip() or not depends_on_work_item_id.strip():
            raise ExecutionKernelRepositoryError("work_item ids required")
        if work_item_id == depends_on_work_item_id:
            raise ExecutionKernelRepositoryError("self-dependency forbidden")
        conn = await self._connect(tenant_id=tenant_id)
        try:
            existing = await conn.fetchrow(
                f"SELECT * FROM {DEPENDENCY_EVENTS_TABLE} WHERE tenant_id = $1 AND command_id = $2",
                tenant_id,
                command_id,
            )
            if existing is not None:
                return DependencyEdgeEvent(
                    event_id=existing["event_id"],
                    tenant_id=existing["tenant_id"],
                    work_item_id=existing["work_item_id"],
                    depends_on_work_item_id=existing["depends_on_work_item_id"],
                    event_type=existing["event_type"],
                    command_id=existing["command_id"],
                )
            event_id = str(uuid4())
            await conn.execute(
                f"""
                INSERT INTO {DEPENDENCY_EVENTS_TABLE}
                    (event_id, tenant_id, work_item_id, depends_on_work_item_id, event_type, command_id)
                VALUES ($1, $2, $3, $4, $5, $6)
                """,
                event_id,
                tenant_id,
                work_item_id,
                depends_on_work_item_id,
                event_type,
                command_id,
            )
            return DependencyEdgeEvent(
                event_id=event_id,
                tenant_id=tenant_id,
                work_item_id=work_item_id,
                depends_on_work_item_id=depends_on_work_item_id,
                event_type=event_type,
                command_id=command_id,
            )
        except asyncpg.UniqueViolationError:
            existing = await conn.fetchrow(
                f"SELECT * FROM {DEPENDENCY_EVENTS_TABLE} WHERE tenant_id = $1 AND command_id = $2",
                tenant_id,
                command_id,
            )
            if existing is None:
                raise ExecutionKernelRepositoryError("dependency unique violation without row") from None
            return DependencyEdgeEvent(
                event_id=existing["event_id"],
                tenant_id=existing["tenant_id"],
                work_item_id=existing["work_item_id"],
                depends_on_work_item_id=existing["depends_on_work_item_id"],
                event_type=existing["event_type"],
                command_id=existing["command_id"],
            )
        except (TimeoutError, OSError, asyncpg.PostgresError) as exc:
            raise ExecutionKernelRepositoryError("dependency write failed") from exc
        finally:
            await conn.close()

    async def active_dependencies(self, *, tenant_id: str, work_item_id: str) -> set[str]:
        tenant_id = _require_tenant(tenant_id)
        conn = await self._connect(tenant_id=tenant_id)
        try:
            rows = await conn.fetch(
                f"""
                SELECT depends_on_work_item_id, event_type
                FROM {DEPENDENCY_EVENTS_TABLE}
                WHERE tenant_id = $1 AND work_item_id = $2
                ORDER BY created_at ASC, event_id ASC
                """,
                tenant_id,
                work_item_id,
            )
        except (TimeoutError, OSError, asyncpg.PostgresError) as exc:
            raise ExecutionKernelRepositoryError("dependency read failed") from exc
        finally:
            await conn.close()
        active: set[str] = set()
        for row in rows:
            if row["event_type"] == "DEPENDENCY_ADDED":
                active.add(row["depends_on_work_item_id"])
            else:
                active.discard(row["depends_on_work_item_id"])
        return active

    async def batch_active_dependencies(
        self, *, tenant_id: str, work_item_ids: list[str]
    ) -> dict[str, set[str]]:
        """AIX-EXECUTION-006-S3: batched form of `active_dependencies` — one
        round trip for N work items instead of N. A zero-trust audit found
        next_actionable_work_items doing K+K·D round trips (one per child,
        one more per active dependency); this collapses the dependency half
        of that into a single query."""
        tenant_id = _require_tenant(tenant_id)
        ids = sorted({str(w).strip() for w in work_item_ids if str(w).strip()})
        result: dict[str, set[str]] = {w: set() for w in ids}
        if not ids:
            return result
        conn = await self._connect(tenant_id=tenant_id)
        try:
            rows = await conn.fetch(
                f"""
                SELECT work_item_id, depends_on_work_item_id, event_type
                FROM {DEPENDENCY_EVENTS_TABLE}
                WHERE tenant_id = $1 AND work_item_id = ANY($2::text[])
                ORDER BY created_at ASC, event_id ASC
                """,
                tenant_id,
                ids,
            )
        except (TimeoutError, OSError, asyncpg.PostgresError) as exc:
            raise ExecutionKernelRepositoryError("dependency batch read failed") from exc
        finally:
            await conn.close()
        for row in rows:
            active = result.setdefault(row["work_item_id"], set())
            if row["event_type"] == "DEPENDENCY_ADDED":
                active.add(row["depends_on_work_item_id"])
            else:
                active.discard(row["depends_on_work_item_id"])
        return result

    async def next_actionable_work_items(
        self,
        *,
        tenant_id: str,
        problem_id: str,
        projection: PostgresProjectionStore,
    ) -> list[str]:
        tenant_id = _require_tenant(tenant_id)
        children = await projection.list_children(
            tenant_id=tenant_id, aggregate_type="work_item", parent_id=problem_id
        )
        pending = [row for row in children if row.status == "PENDING"]
        if not pending:
            return []

        deps_by_item = await self.batch_active_dependencies(
            tenant_id=tenant_id, work_item_ids=[row.aggregate_id for row in pending]
        )
        all_dep_ids = {dep_id for deps in deps_by_item.values() for dep_id in deps}
        dep_status = await projection.get_many(
            tenant_id=tenant_id, aggregate_type="work_item", aggregate_ids=list(all_dep_ids)
        )

        actionable: list[str] = []
        for row in pending:
            deps = deps_by_item.get(row.aggregate_id, set())
            if not deps:
                actionable.append(row.aggregate_id)
                continue
            ready = all(
                (dep := dep_status.get(dep_id)) is not None and dep.status in TERMINAL_COMPLETE
                for dep_id in deps
            )
            if ready:
                actionable.append(row.aggregate_id)
        return sorted(actionable)


class PostgresCheckpointStore:
    def __init__(self, database_url: str, *, ensure_schema: bool = True, command_timeout_seconds: float = 5.0) -> None:
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
            raise ExecutionKernelRepositoryError("checkpoint store connection failed") from exc

    async def capture(
        self,
        *,
        tenant_id: str,
        problem_id: str,
        projection: PostgresProjectionStore,
        repository_state: dict[str, Any],
        open_proposals: list[dict[str, Any]] | None = None,
        evidence_refs: list[str] | None = None,
        decision_refs: list[str] | None = None,
    ) -> Checkpoint:
        tenant_id = _require_tenant(tenant_id)
        missing = [f for f in REPOSITORY_STATE_FIELDS if f not in repository_state]
        if missing:
            raise ExecutionKernelRepositoryError(f"repository_state missing fields: {missing}")
        children = await projection.list_children(
            tenant_id=tenant_id, aggregate_type="work_item", parent_id=problem_id
        )
        work_items = [
            {
                "work_item_id": row.aggregate_id,
                "status": row.status,
                "sequence_number": row.sequence_number,
            }
            for row in children
        ]
        checkpoint_id = str(uuid4())
        conn = await self._connect()
        try:
            async with conn.transaction():
                await conn.execute(
                    f"""
                    UPDATE {CHECKPOINT_TABLE}
                    SET status = 'SUPERSEDED', superseded_by = $1
                    WHERE tenant_id = $2 AND problem_id = $3 AND status = 'CAPTURED'
                    """,
                    checkpoint_id,
                    tenant_id,
                    problem_id,
                )
                await conn.execute(
                    f"""
                    INSERT INTO {CHECKPOINT_TABLE}
                        (checkpoint_id, tenant_id, problem_id, status, work_items, open_proposals,
                         evidence_refs, decision_refs, repository_state)
                    VALUES ($1, $2, $3, 'CAPTURED', $4::jsonb, $5::jsonb, $6::jsonb, $7::jsonb, $8::jsonb)
                    """,
                    checkpoint_id,
                    tenant_id,
                    problem_id,
                    json.dumps(work_items),
                    json.dumps(list(open_proposals or [])),
                    json.dumps(list(evidence_refs or [])),
                    json.dumps(list(decision_refs or [])),
                    json.dumps({k: repository_state[k] for k in REPOSITORY_STATE_FIELDS}),
                )
                row = await conn.fetchrow(
                    f"SELECT * FROM {CHECKPOINT_TABLE} WHERE checkpoint_id = $1",
                    checkpoint_id,
                )
        except (TimeoutError, OSError, asyncpg.PostgresError) as exc:
            raise ExecutionKernelRepositoryError("checkpoint capture failed") from exc
        finally:
            await conn.close()
        assert row is not None
        return _checkpoint_from_row(row)

    async def current(self, *, tenant_id: str, problem_id: str) -> Checkpoint | None:
        tenant_id = _require_tenant(tenant_id)
        conn = await self._connect()
        try:
            row = await conn.fetchrow(
                f"""
                SELECT * FROM {CHECKPOINT_TABLE}
                WHERE tenant_id = $1 AND problem_id = $2 AND status = 'CAPTURED'
                ORDER BY created_at DESC
                LIMIT 1
                """,
                tenant_id,
                problem_id,
            )
        except (TimeoutError, OSError, asyncpg.PostgresError) as exc:
            raise ExecutionKernelRepositoryError("checkpoint read failed") from exc
        finally:
            await conn.close()
        if row is None:
            return None
        return _checkpoint_from_row(row)


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, str):
        value = json.loads(value)
    return list(value or [])


def _checkpoint_from_row(row: asyncpg.Record) -> Checkpoint:
    return Checkpoint(
        checkpoint_id=row["checkpoint_id"],
        tenant_id=row["tenant_id"],
        problem_id=row["problem_id"],
        status=row["status"],
        work_items=_as_list(row["work_items"]),
        open_proposals=_as_list(row["open_proposals"]),
        evidence_refs=_as_list(row["evidence_refs"]),
        decision_refs=_as_list(row["decision_refs"]),
        repository_state=_payload(row["repository_state"]),
        created_at=row["created_at"].isoformat(),
        superseded_by=row["superseded_by"],
    )


def _knowledge_graph_edge_from_row(row: asyncpg.Record) -> KnowledgeGraphEdge:
    return KnowledgeGraphEdge(
        edge_id=row["edge_id"],
        tenant_id=row["tenant_id"],
        edge_type=row["edge_type"],
        from_type=row["from_type"],
        from_id=row["from_id"],
        to_type=row["to_type"],
        to_id=row["to_id"],
        command_id=row["command_id"],
        status=row["status"],
        authority_context=row["authority_context"],
        provenance=tuple(str(value) for value in _as_list(row["provenance"])),
    )


def _registered_capability_from_row(row: asyncpg.Record) -> RegisteredCapability:
    return RegisteredCapability(
        capability_key=row["capability_key"],
        version=row["version"],
        tenant_id=row["tenant_id"],
        display_name=row["display_name"],
        description=row["description"],
        input_contract=_payload(row["input_contract"]),
        output_contract=_payload(row["output_contract"]),
        authority_requirements=_payload(row["authority_requirements"]),
        executor_binding=row["executor_binding"],
        verification_method=row["verification_method"],
        dependencies=tuple(str(value) for value in _as_list(row["dependencies"])),
        cost_class=row["cost_class"],
        risk_class=row["risk_class"],
        status=row["status"],
    )


class PostgresCapabilityRegistry:
    """Durable, tenant-scoped registry for reusable AIX execution capabilities."""

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
            raise ExecutionKernelRepositoryError("capability registry connection failed") from exc

    async def register(self, entry: RegisteredCapability) -> RegisteredCapability:
        validator = InMemoryCapabilityRegistry()
        normalized = validator.register(entry)
        conn = await self._connect()
        try:
            row = await conn.fetchrow(
                f"""
                INSERT INTO {CAPABILITY_REGISTRY_TABLE} (
                    tenant_id, capability_key, version, display_name, description,
                    input_contract, output_contract, authority_requirements,
                    executor_binding, verification_method, dependencies,
                    cost_class, risk_class, status
                ) VALUES (
                    $1, $2, $3, $4, $5, $6::jsonb, $7::jsonb, $8::jsonb,
                    $9, $10, $11::jsonb, $12, $13, $14
                )
                ON CONFLICT (tenant_id, capability_key, version)
                DO UPDATE SET
                    display_name = EXCLUDED.display_name,
                    description = EXCLUDED.description,
                    input_contract = EXCLUDED.input_contract,
                    output_contract = EXCLUDED.output_contract,
                    authority_requirements = EXCLUDED.authority_requirements,
                    executor_binding = EXCLUDED.executor_binding,
                    verification_method = EXCLUDED.verification_method,
                    dependencies = EXCLUDED.dependencies,
                    cost_class = EXCLUDED.cost_class,
                    risk_class = EXCLUDED.risk_class,
                    status = EXCLUDED.status,
                    updated_at = now()
                RETURNING *
                """,
                normalized.tenant_id,
                normalized.capability_key,
                normalized.version,
                normalized.display_name,
                normalized.description,
                json.dumps(normalized.input_contract, sort_keys=True),
                json.dumps(normalized.output_contract, sort_keys=True),
                json.dumps(normalized.authority_requirements, sort_keys=True),
                normalized.executor_binding,
                normalized.verification_method,
                json.dumps(list(normalized.dependencies)),
                normalized.cost_class,
                normalized.risk_class,
                normalized.status,
            )
        except (TimeoutError, OSError, asyncpg.PostgresError) as exc:
            raise ExecutionKernelRepositoryError("capability registry write failed") from exc
        finally:
            await conn.close()
        assert row is not None
        return _registered_capability_from_row(row)

    async def get(
        self, *, tenant_id: str, capability_key: str, version: str | None = None
    ) -> RegisteredCapability | None:
        tenant_id = _require_tenant(tenant_id)
        capability_key = str(capability_key or "").strip()
        conn = await self._connect()
        try:
            if version:
                row = await conn.fetchrow(
                    f"SELECT * FROM {CAPABILITY_REGISTRY_TABLE} "
                    "WHERE tenant_id = $1 AND capability_key = $2 AND version = $3",
                    tenant_id,
                    capability_key,
                    str(version).strip(),
                )
            else:
                row = await conn.fetchrow(
                    f"SELECT * FROM {CAPABILITY_REGISTRY_TABLE} "
                    "WHERE tenant_id = $1 AND capability_key = $2 AND status = 'ACTIVE' "
                    "ORDER BY version DESC LIMIT 1",
                    tenant_id,
                    capability_key,
                )
        except (TimeoutError, OSError, asyncpg.PostgresError) as exc:
            raise ExecutionKernelRepositoryError("capability registry read failed") from exc
        finally:
            await conn.close()
        return _registered_capability_from_row(row) if row is not None else None

    async def list(
        self, *, tenant_id: str, active_only: bool = True, capability_key: str | None = None
    ) -> list[RegisteredCapability]:
        tenant_id = _require_tenant(tenant_id)
        clauses = ["tenant_id = $1"]
        values: list[Any] = [tenant_id]
        if capability_key:
            values.append(capability_key)
            clauses.append(f"capability_key = ${len(values)}")
        if active_only:
            clauses.append("status = 'ACTIVE'")
        conn = await self._connect()
        try:
            rows = await conn.fetch(
                f"SELECT * FROM {CAPABILITY_REGISTRY_TABLE} "
                f"WHERE {' AND '.join(clauses)} ORDER BY capability_key, version",
                *values,
            )
        except (TimeoutError, OSError, asyncpg.PostgresError) as exc:
            raise ExecutionKernelRepositoryError("capability registry list failed") from exc
        finally:
            await conn.close()
        return [_registered_capability_from_row(row) for row in rows]

    async def resolve_dependencies(
        self, *, tenant_id: str, capability_key: str, version: str | None = None
    ) -> list[RegisteredCapability]:
        tenant_id = _require_tenant(tenant_id)
        entries = await self.list(tenant_id=tenant_id, active_only=False)
        memory = InMemoryCapabilityRegistry()
        for entry in entries:
            memory.register(entry)
        return memory.resolve_dependencies(
            tenant_id=tenant_id, capability_key=capability_key, version=version
        )


class PostgresKnowledgeGraphStore:
    """Tenant-scoped Postgres adjacency store for governed knowledge edges."""

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
            raise ExecutionKernelRepositoryError("knowledge graph connection failed") from exc

    async def add_edge(
        self,
        *,
        tenant_id: str,
        edge_type: str,
        from_type: str,
        from_id: str,
        to_type: str,
        to_id: str,
        command_id: str,
        edge_id: str | None = None,
        authority_context: str | None = None,
        provenance: list[str] | tuple[str, ...] | None = None,
        supersede_matching_active: bool = False,
    ) -> KnowledgeGraphEdge:
        tenant_id = _require_tenant(tenant_id)
        command_id = str(command_id or "").strip()
        from_id = str(from_id or "").strip()
        to_id = str(to_id or "").strip()
        if not command_id:
            raise ExecutionKernelRepositoryError("command_id is required")
        if edge_type not in EDGE_TYPES:
            raise ExecutionKernelRepositoryError(f"invalid edge_type={edge_type!r}")
        if from_type not in NODE_TYPES or to_type not in NODE_TYPES:
            raise ExecutionKernelRepositoryError("invalid node type")
        if not from_id or not to_id:
            raise ExecutionKernelRepositoryError("from_id and to_id are required")

        conn = await self._connect()
        try:
            async with conn.transaction():
                existing = await conn.fetchrow(
                    f"SELECT * FROM {KNOWLEDGE_GRAPH_EDGES_TABLE} "
                    "WHERE tenant_id = $1 AND command_id = $2",
                    tenant_id,
                    command_id,
                )
                if existing is not None:
                    return _knowledge_graph_edge_from_row(existing)
                if supersede_matching_active:
                    await conn.execute(
                        f"""
                        UPDATE {KNOWLEDGE_GRAPH_EDGES_TABLE}
                        SET status = 'SUPERSEDED'
                        WHERE tenant_id = $1 AND edge_type = $2
                          AND from_type = $3 AND from_id = $4
                          AND to_type = $5 AND to_id = $6
                          AND status = 'ACTIVE'
                        """,
                        tenant_id,
                        edge_type,
                        from_type,
                        from_id,
                        to_type,
                        to_id,
                    )
                inserted = await conn.fetchrow(
                    f"""
                    INSERT INTO {KNOWLEDGE_GRAPH_EDGES_TABLE} (
                        edge_id, tenant_id, edge_type, from_type, from_id,
                        to_type, to_id, status, command_id, authority_context, provenance
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, 'ACTIVE', $8, $9, $10::jsonb)
                    RETURNING *
                    """,
                    str(edge_id or f"kge_{uuid4().hex}"),
                    tenant_id,
                    edge_type,
                    from_type,
                    from_id,
                    to_type,
                    to_id,
                    command_id,
                    authority_context,
                    json.dumps(list(provenance or ())),
                )
        except ExecutionKernelRepositoryError:
            raise
        except (TimeoutError, OSError, asyncpg.PostgresError) as exc:
            raise ExecutionKernelRepositoryError("knowledge graph append failed") from exc
        finally:
            await conn.close()
        assert inserted is not None
        return _knowledge_graph_edge_from_row(inserted)

    async def get_edges(
        self,
        *,
        tenant_id: str,
        edge_type: str | None = None,
        from_type: str | None = None,
        from_id: str | None = None,
        to_type: str | None = None,
        to_id: str | None = None,
        active_only: bool = True,
    ) -> list[KnowledgeGraphEdge]:
        tenant_id = _require_tenant(tenant_id)
        clauses = ["tenant_id = $1"]
        values: list[Any] = [tenant_id]
        for column, value in (
            ("edge_type", edge_type),
            ("from_type", from_type),
            ("from_id", from_id),
            ("to_type", to_type),
            ("to_id", to_id),
        ):
            if value is not None:
                values.append(value)
                clauses.append(f"{column} = ${len(values)}")
        if active_only:
            clauses.append("status = 'ACTIVE'")
        conn = await self._connect()
        try:
            rows = await conn.fetch(
                f"SELECT * FROM {KNOWLEDGE_GRAPH_EDGES_TABLE} "
                f"WHERE {' AND '.join(clauses)} ORDER BY created_at, edge_id",
                *values,
            )
        except (TimeoutError, OSError, asyncpg.PostgresError) as exc:
            raise ExecutionKernelRepositoryError("knowledge graph read failed") from exc
        finally:
            await conn.close()
        return [_knowledge_graph_edge_from_row(row) for row in rows]

    async def get_edge(self, *, tenant_id: str, edge_id: str) -> KnowledgeGraphEdge | None:
        tenant_id = _require_tenant(tenant_id)
        conn = await self._connect()
        try:
            row = await conn.fetchrow(
                f"SELECT * FROM {KNOWLEDGE_GRAPH_EDGES_TABLE} "
                "WHERE tenant_id = $1 AND edge_id = $2",
                tenant_id,
                str(edge_id or "").strip(),
            )
        except (TimeoutError, OSError, asyncpg.PostgresError) as exc:
            raise ExecutionKernelRepositoryError("knowledge graph read failed") from exc
        finally:
            await conn.close()
        return _knowledge_graph_edge_from_row(row) if row is not None else None

    async def supersede_edge(self, *, tenant_id: str, edge_id: str) -> KnowledgeGraphEdge:
        tenant_id = _require_tenant(tenant_id)
        edge_id = str(edge_id or "").strip()
        if not edge_id:
            raise ExecutionKernelRepositoryError("edge_id is required")
        conn = await self._connect()
        try:
            row = await conn.fetchrow(
                f"""
                UPDATE {KNOWLEDGE_GRAPH_EDGES_TABLE}
                SET status = 'SUPERSEDED'
                WHERE tenant_id = $1 AND edge_id = $2
                RETURNING *
                """,
                tenant_id,
                edge_id,
            )
        except (TimeoutError, OSError, asyncpg.PostgresError) as exc:
            raise ExecutionKernelRepositoryError("knowledge graph supersede failed") from exc
        finally:
            await conn.close()
        if row is None:
            raise ExecutionKernelRepositoryError(f"knowledge graph edge not found: {edge_id}")
        return _knowledge_graph_edge_from_row(row)

    async def list_edges(self, **filters: Any) -> list[KnowledgeGraphEdge]:
        return await self.get_edges(**filters)

    async def traverse(
        self,
        *,
        tenant_id: str,
        start_type: str,
        start_id: str,
        edge_types: list[str] | tuple[str, ...] | None = None,
        direction: str = "out",
        max_depth: int = 2,
    ) -> list[dict[str, Any]]:
        tenant_id = _require_tenant(tenant_id)
        edges = await self.get_edges(tenant_id=tenant_id, active_only=True)
        graph = InMemoryKnowledgeGraph(_edges=edges)
        return graph.traverse(
            tenant_id=tenant_id,
            start_type=start_type,
            start_id=start_id,
            edge_types=edge_types,
            direction=direction,  # type: ignore[arg-type]
            max_depth=max_depth,
        )


class PostgresKnowledgeStore:
    """Append-only Knowledge Store (Packet 5 Create/Update/Archive)."""

    def __init__(self, database_url: str, *, ensure_schema: bool = True, command_timeout_seconds: float = 5.0) -> None:
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
            raise ExecutionKernelRepositoryError("knowledge store connection failed") from exc

    async def create(
        self,
        *,
        tenant_id: str,
        knowledge_entry_id: str,
        knowledge_kind: str,
        content: str,
        command_id: str,
        capability_id: str | None = None,
        problem_id: str | None = None,
        project_id: str | None = None,
        applicable_capability_ids: tuple[str, ...] | list[str] = (),
        valid_from: str | None = None,
        valid_until: str | None = None,
        superseded_by_entry_id: str | None = None,
        confidence: str = "unknown",
        review_due_at: str | None = None,
    ) -> KnowledgeEntry:
        return await self._append(
            tenant_id=tenant_id,
            knowledge_entry_id=knowledge_entry_id,
            knowledge_kind=knowledge_kind,
            content=content,
            command_id=command_id,
            capability_id=capability_id,
            problem_id=problem_id,
            project_id=project_id,
            applicable_capability_ids=applicable_capability_ids,
            valid_from=valid_from,
            valid_until=valid_until,
            superseded_by_entry_id=superseded_by_entry_id,
            confidence=confidence,
            review_due_at=review_due_at,
            event_type="KNOWLEDGE_CREATED",
            status="ACTIVE",
            expect_new=True,
        )

    async def update(
        self,
        *,
        tenant_id: str,
        knowledge_entry_id: str,
        content: str,
        command_id: str,
        knowledge_kind: str | None = None,
        capability_id: str | None = None,
        problem_id: str | None = None,
        project_id: str | None = None,
        applicable_capability_ids: tuple[str, ...] | list[str] | None = None,
        valid_from: str | None = None,
        valid_until: str | None = None,
        superseded_by_entry_id: str | None = None,
        confidence: str | None = None,
        review_due_at: str | None = None,
    ) -> KnowledgeEntry:
        current = await self.get_current(tenant_id=tenant_id, knowledge_entry_id=knowledge_entry_id)
        if current is None or current.status != "ACTIVE":
            raise ExecutionKernelRepositoryError(f"knowledge entry not active: {knowledge_entry_id}")
        return await self._append(
            tenant_id=tenant_id,
            knowledge_entry_id=knowledge_entry_id,
            knowledge_kind=knowledge_kind or current.knowledge_kind,
            content=content,
            command_id=command_id,
            capability_id=capability_id if capability_id is not None else current.capability_id,
            problem_id=problem_id if problem_id is not None else current.problem_id,
            project_id=project_id if project_id is not None else current.project_id,
            applicable_capability_ids=(
                applicable_capability_ids
                if applicable_capability_ids is not None
                else current.applicable_capability_ids
            ),
            valid_from=valid_from if valid_from is not None else current.valid_from,
            valid_until=valid_until if valid_until is not None else current.valid_until,
            superseded_by_entry_id=(
                superseded_by_entry_id
                if superseded_by_entry_id is not None
                else current.superseded_by_entry_id
            ),
            confidence=confidence if confidence is not None else current.confidence,
            review_due_at=review_due_at if review_due_at is not None else current.review_due_at,
            event_type="KNOWLEDGE_UPDATED",
            status="ACTIVE",
            expect_new=False,
            supersede_previous=True,
        )

    async def archive(self, *, tenant_id: str, knowledge_entry_id: str, command_id: str) -> KnowledgeEntry:
        current = await self.get_current(tenant_id=tenant_id, knowledge_entry_id=knowledge_entry_id)
        if current is None:
            raise ExecutionKernelRepositoryError(f"knowledge entry not found: {knowledge_entry_id}")
        return await self._append(
            tenant_id=tenant_id,
            knowledge_entry_id=knowledge_entry_id,
            knowledge_kind=current.knowledge_kind,
            content=current.content,
            command_id=command_id,
            capability_id=current.capability_id,
            problem_id=current.problem_id,
            project_id=current.project_id,
            applicable_capability_ids=current.applicable_capability_ids,
            valid_from=current.valid_from,
            valid_until=current.valid_until,
            superseded_by_entry_id=current.superseded_by_entry_id,
            confidence=current.confidence,
            review_due_at=current.review_due_at,
            event_type="KNOWLEDGE_ARCHIVED",
            status="DEPRECATED",
            expect_new=False,
            supersede_previous=True,
        )

    async def _append(
        self,
        *,
        tenant_id: str,
        knowledge_entry_id: str,
        knowledge_kind: str,
        content: str,
        command_id: str,
        capability_id: str | None,
        problem_id: str | None,
        project_id: str | None,
        applicable_capability_ids: tuple[str, ...] | list[str],
        valid_from: str | None,
        valid_until: str | None,
        superseded_by_entry_id: str | None,
        confidence: str,
        review_due_at: str | None,
        event_type: str,
        status: str,
        expect_new: bool,
        supersede_previous: bool = False,
    ) -> KnowledgeEntry:
        tenant_id = _require_tenant(tenant_id)
        if knowledge_kind not in KNOWLEDGE_KINDS:
            raise ExecutionKernelRepositoryError(f"invalid knowledge_kind={knowledge_kind!r}")
        if confidence not in {"unknown", "low", "medium", "high"}:
            raise ExecutionKernelRepositoryError(f"invalid knowledge confidence={confidence!r}")
        command_id = str(command_id or "").strip()
        if not command_id:
            raise ExecutionKernelRepositoryError("command_id is required")
        applicable = list(applicable_capability_ids or [])
        conn = await self._connect()
        transaction = conn.transaction()
        try:
            await transaction.start()
            existing_cmd = await conn.fetchrow(
                f"SELECT * FROM {KNOWLEDGE_TABLE} WHERE tenant_id = $1 AND command_id = $2",
                tenant_id,
                command_id,
            )
            if existing_cmd is not None:
                await transaction.commit()
                return _knowledge_from_row(existing_cmd)

            max_rev = await conn.fetchval(
                f"""
                SELECT COALESCE(MAX(revision), 0) FROM {KNOWLEDGE_TABLE}
                WHERE tenant_id = $1 AND knowledge_entry_id = $2
                """,
                tenant_id,
                knowledge_entry_id,
            )
            if expect_new and max_rev:
                raise ExecutionKernelRepositoryError(f"knowledge entry already exists: {knowledge_entry_id}")
            if not expect_new and not max_rev:
                raise ExecutionKernelRepositoryError(f"knowledge entry not found: {knowledge_entry_id}")
            revision = int(max_rev) + 1
            if supersede_previous and max_rev:
                await conn.execute(
                    f"""
                    UPDATE {KNOWLEDGE_TABLE}
                    SET status = 'SUPERSEDED'
                    WHERE tenant_id = $1 AND knowledge_entry_id = $2 AND revision = $3 AND status = 'ACTIVE'
                    """,
                    tenant_id,
                    knowledge_entry_id,
                    int(max_rev),
                )
            event_id = f"kevt_{uuid4().hex}"
            await conn.execute(
                f"""
                INSERT INTO {KNOWLEDGE_TABLE} (
                    event_id, tenant_id, knowledge_entry_id, revision, event_type,
                    knowledge_kind, capability_id, problem_id, project_id, content,
                    applicable_capability_ids, valid_from, valid_until,
                    superseded_by_entry_id, confidence, review_due_at, status, command_id
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11::jsonb,
                    $12::timestamptz, $13::timestamptz, $14, $15,
                    $16::timestamptz, $17, $18
                )
                """,
                event_id,
                tenant_id,
                knowledge_entry_id,
                revision,
                event_type,
                knowledge_kind,
                capability_id,
                problem_id,
                project_id,
                content,
                json.dumps(applicable),
                valid_from,
                valid_until,
                superseded_by_entry_id,
                confidence,
                review_due_at,
                status,
                command_id,
            )
            await transaction.commit()
        except ExecutionKernelRepositoryError:
            await transaction.rollback()
            raise
        except (TimeoutError, OSError, asyncpg.PostgresError) as exc:
            await transaction.rollback()
            raise ExecutionKernelRepositoryError("knowledge append failed") from exc
        finally:
            await conn.close()
        return KnowledgeEntry(
            knowledge_entry_id=knowledge_entry_id,
            tenant_id=tenant_id,
            knowledge_kind=knowledge_kind,
            capability_id=capability_id,
            problem_id=problem_id,
            project_id=project_id,
            content=content,
            applicable_capability_ids=tuple(applicable),
            status=status,
            revision=revision,
            valid_from=valid_from,
            valid_until=valid_until,
            superseded_by_entry_id=superseded_by_entry_id,
            confidence=confidence,  # type: ignore[arg-type]
            review_due_at=review_due_at,
        )

    async def get_current(self, *, tenant_id: str, knowledge_entry_id: str) -> KnowledgeEntry | None:
        tenant_id = _require_tenant(tenant_id)
        conn = await self._connect()
        try:
            row = await conn.fetchrow(
                f"""
                SELECT * FROM {KNOWLEDGE_TABLE}
                WHERE tenant_id = $1 AND knowledge_entry_id = $2
                ORDER BY revision DESC
                LIMIT 1
                """,
                tenant_id,
                knowledge_entry_id,
            )
        except (TimeoutError, OSError, asyncpg.PostgresError) as exc:
            raise ExecutionKernelRepositoryError("knowledge read failed") from exc
        finally:
            await conn.close()
        if row is None:
            return None
        return _knowledge_from_row(row)

    async def candidates_for_capability(self, *, tenant_id: str, capability_id: str) -> list[KnowledgeEntry]:
        tenant_id = _require_tenant(tenant_id)
        conn = await self._connect()
        try:
            rows = await conn.fetch(
                f"""
                SELECT DISTINCT ON (knowledge_entry_id) *
                FROM {KNOWLEDGE_TABLE}
                WHERE tenant_id = $1 AND status = 'ACTIVE'
                ORDER BY knowledge_entry_id, revision DESC
                """,
                tenant_id,
            )
        except (TimeoutError, OSError, asyncpg.PostgresError) as exc:
            raise ExecutionKernelRepositoryError("knowledge candidates failed") from exc
        finally:
            await conn.close()
        out: list[KnowledgeEntry] = []
        for row in rows:
            entry = _knowledge_from_row(row)
            if entry.capability_id == capability_id or capability_id in entry.applicable_capability_ids:
                out.append(entry)
        return out

    async def watermark(self, *, tenant_id: str) -> int:
        tenant_id = _require_tenant(tenant_id)
        conn = await self._connect()
        try:
            value = await conn.fetchval(
                f"SELECT COALESCE(SUM(revision), 0) FROM {KNOWLEDGE_TABLE} WHERE tenant_id = $1",
                tenant_id,
            )
        except (TimeoutError, OSError, asyncpg.PostgresError) as exc:
            raise ExecutionKernelRepositoryError("knowledge watermark failed") from exc
        finally:
            await conn.close()
        return int(value or 0)

    async def materialize(self, *, tenant_id: str) -> InMemoryKnowledgeStore:
        tenant_id = _require_tenant(tenant_id)
        conn = await self._connect()
        try:
            rows = await conn.fetch(
                f"""
                SELECT DISTINCT ON (knowledge_entry_id) *
                FROM {KNOWLEDGE_TABLE}
                WHERE tenant_id = $1
                ORDER BY knowledge_entry_id, revision DESC
                """,
                tenant_id,
            )
        except (TimeoutError, OSError, asyncpg.PostgresError) as exc:
            raise ExecutionKernelRepositoryError("knowledge materialize failed") from exc
        finally:
            await conn.close()
        store = InMemoryKnowledgeStore()
        for row in rows:
            store.upsert(_knowledge_from_row(row))
        return store


def _knowledge_from_row(row: asyncpg.Record) -> KnowledgeEntry:
    applicable = _as_list(row["applicable_capability_ids"])
    return KnowledgeEntry(
        knowledge_entry_id=row["knowledge_entry_id"],
        tenant_id=row["tenant_id"],
        knowledge_kind=row["knowledge_kind"],
        capability_id=row["capability_id"],
        problem_id=row["problem_id"],
        project_id=row["project_id"],
        content=row["content"],
        applicable_capability_ids=tuple(str(x) for x in applicable),
        status=row["status"],
        revision=int(row["revision"]),
        valid_from=row["valid_from"].isoformat() if row["valid_from"] else None,
        valid_until=row["valid_until"].isoformat() if row["valid_until"] else None,
        superseded_by_entry_id=row["superseded_by_entry_id"],
        confidence=row["confidence"],
        review_due_at=row["review_due_at"].isoformat() if row["review_due_at"] else None,
    )


__all__ = [
    "PostgresProjectionStore",
    "PostgresDependencyGraph",
    "PostgresCheckpointStore",
    "PostgresCapabilityRegistry",
    "PostgresKnowledgeGraphStore",
    "PostgresKnowledgeStore",
]
