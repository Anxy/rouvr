"""EX5-S5: Semantic Intelligence — durable embedding search + pgvector capability gate.

Prior state (EX4-R3-P9-S001 / EX5-S1): PostgresSemanticStore.upsert_embedding()
persisted vectors to aix_execution_knowledge_embeddings, but .search() never
read them back — it just recomputed hashed BoW fresh from the in-memory
knowledge corpus every call, same as the in-process pilot. The durable table
existed but was functionally decorative.

This closes that gap: search() now genuinely queries the persisted table,
proven the same way EX5-S3 proved checkpoint/resume — build state with one
service instance, discard it entirely, and prove a fresh instance against
the same database finds the durable data.

pgvector itself is NOT installed on the local Postgres used for this
session (confirmed via `SELECT * FROM pg_available_extensions WHERE name =
'vector'` returning empty) — installing a native Postgres extension is a
system-level change outside what this session makes unilaterally. The
capability-detection test below proves the runtime check correctly reports
False here rather than assuming availability.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from backend.infrastructure.postgres.execution_kernel.postgres_intelligence_stores import pgvector_extension_available
from backend.aix_core.runtime.executor.execution_kernel_adapter.postgres_service import PostgresKernelService
from backend.infrastructure.postgres.execution_kernel.repository import resolve_test_database_url


@pytest.mark.asyncio
async def test_semantic_search_survives_a_simulated_process_restart() -> None:
    url = resolve_test_database_url()
    if not url:
        pytest.skip("No AIX_EXECUTION_KERNEL_TEST_DATABASE_URL / ledger test DB configured")

    suffix = uuid4().hex[:12]
    tenant_id = f"t_sem_{suffix}"
    entry_id = f"kl_{suffix}"

    service_a = PostgresKernelService(database_url=url)
    await service_a.ensure_schema()
    await service_a.upsert_knowledge(
        tenant_id=tenant_id, knowledge_entry_id=entry_id, knowledge_kind="architecture",
        content="Tenant isolation must fail closed when tenant_id is missing from a request.",
        command_id=f"kl_created_{suffix}",
    )
    await service_a.semantic.upsert_embedding(
        tenant_id=tenant_id, knowledge_entry_id=entry_id,
        content="Tenant isolation must fail closed when tenant_id is missing from a request.",
    )
    del service_a  # simulate process death — no shared state survives

    service_b = PostgresKernelService(database_url=url)
    hits = await service_b.semantic_search(
        tenant_id=tenant_id, query="what happens if tenant scoping is absent from a call", min_score=0.0,
    )
    assert any(h["knowledge_entry_id"] == entry_id for h in hits), (
        "a fresh process must find the durably-embedded entry via the persisted "
        "table, not just whatever it happens to hold in memory"
    )
    match = next(h for h in hits if h["knowledge_entry_id"] == entry_id)
    assert match["backend"].startswith("postgres_durable/"), (
        "the hit must be attributed to the durable path, proving it actually "
        "read from the embeddings table rather than silently falling back"
    )


@pytest.mark.asyncio
async def test_semantic_search_falls_back_when_no_durable_embeddings_exist_yet() -> None:
    """Before any upsert_embedding call has ever run for a tenant, search
    must not silently return nothing — it falls back to in-process
    recomputation rather than pretending the corpus is empty."""
    url = resolve_test_database_url()
    if not url:
        pytest.skip("No AIX_EXECUTION_KERNEL_TEST_DATABASE_URL / ledger test DB configured")

    suffix = uuid4().hex[:12]
    tenant_id = f"t_semfb_{suffix}"
    entry_id = f"kl_fb_{suffix}"

    service = PostgresKernelService(database_url=url)
    await service.ensure_schema()
    await service.upsert_knowledge(
        tenant_id=tenant_id, knowledge_entry_id=entry_id, knowledge_kind="pattern",
        content="Retry with exponential backoff before escalating to a fallback provider.",
        command_id=f"kl_fb_created_{suffix}",
    )
    # deliberately no upsert_embedding call

    hits = await service.semantic_search(tenant_id=tenant_id, query="backoff before escalation", min_score=0.0)
    assert any(h["knowledge_entry_id"] == entry_id for h in hits), (
        "fallback path must still find the entry via in-process recomputation"
    )


@pytest.mark.asyncio
async def test_pgvector_capability_detection_is_honest_about_this_environment() -> None:
    """This is the actual, current, honest answer for this environment —
    not assumed, checked live. If this ever flips to True (pgvector gets
    installed), the native vector-column path should be built and gated
    on this same check, not before."""
    url = resolve_test_database_url()
    if not url:
        pytest.skip("No AIX_EXECUTION_KERNEL_TEST_DATABASE_URL / ledger test DB configured")

    import asyncpg  # type: ignore[import-untyped]

    conn = await asyncpg.connect(url)
    try:
        available = await pgvector_extension_available(conn)
    finally:
        await conn.close()

    # Recorded as a fact about this environment, not a permanent assertion
    # about all environments — if pgvector is ever installed here, this
    # test should be updated, not treated as broken.
    assert available is False, (
        "pgvector was not installed on this session's local Postgres at time of "
        "writing — if this now fails, pgvector has become available and the "
        "native vector-column search path should be built next, gated on this check"
    )
