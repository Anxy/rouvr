"""EX5-S7: Knowledge Evolution — "Knowledge never permanently stale."

EX4-S002 could already *detect* staleness (knowledge_entry_is_stale) but
never surfaced a worklist of what needs attention, and there was no
convenience action to move an entry back from stale to current — once
something went stale, nothing in the system ever acted on it again. This
closes both halves: a review worklist, and a revalidation action.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.infrastructure.postgres.execution_kernel.repository import ExecutionKernelRepositoryError
from backend.aix_core.runtime.executor.execution_kernel_adapter.service import KernelService


def _past_iso() -> str:
    return (datetime.now(timezone.utc) - timedelta(days=1)).isoformat().replace("+00:00", "Z")


def _future_iso() -> str:
    return (datetime.now(timezone.utc) + timedelta(days=30)).isoformat().replace("+00:00", "Z")


@pytest.mark.asyncio
async def test_stale_entry_appears_in_worklist_current_entry_does_not() -> None:
    svc = KernelService()
    await svc.upsert_knowledge(
        tenant_id="t1", knowledge_entry_id="kl_stale", knowledge_kind="architecture",
        content="Old guidance, past its review date.", command_id="c1",
        capability_id="cap_1", review_due_at=_past_iso(),
    )
    await svc.upsert_knowledge(
        tenant_id="t1", knowledge_entry_id="kl_fresh", knowledge_kind="architecture",
        content="Current guidance, reviewed recently.", command_id="c2",
        capability_id="cap_1", review_due_at=_future_iso(),
    )

    worklist = await svc.list_stale_knowledge(tenant_id="t1")
    ids = {item["knowledge_entry_id"] for item in worklist}
    assert "kl_stale" in ids
    assert "kl_fresh" not in ids


@pytest.mark.asyncio
async def test_revalidate_removes_entry_from_worklist_and_restores_ranking() -> None:
    svc = KernelService()
    await svc.upsert_knowledge(
        tenant_id="t1", knowledge_entry_id="kl_1", knowledge_kind="lesson_learned",
        content="This needs a re-check.", command_id="c1",
        capability_id="cap_1", review_due_at=_past_iso(),
    )
    before = await svc.list_stale_knowledge(tenant_id="t1")
    assert any(i["knowledge_entry_id"] == "kl_1" for i in before)

    revalidated = await svc.revalidate_knowledge(
        tenant_id="t1", knowledge_entry_id="kl_1", command_id="revalidate_1",
        review_due_at=_future_iso(),
    )
    assert revalidated["review_due_at"] is not None

    after = await svc.list_stale_knowledge(tenant_id="t1")
    assert not any(i["knowledge_entry_id"] == "kl_1" for i in after), (
        "a revalidated entry must leave the review worklist — the whole "
        "point is that staleness is reversible, not permanent"
    )

    # And it re-enters current, rankable query results — not just off the worklist.
    current = await svc.query_organizational_knowledge(tenant_id="t1", capability_id="cap_1")
    match = next(c for c in current if c["knowledge_entry_id"] == "kl_1")
    assert match["stale"] is False


@pytest.mark.asyncio
async def test_revalidate_preserves_content_and_kind_caller_need_not_resupply_them() -> None:
    """The actual convenience this sprint adds: a caller revalidating an
    entry does not need to already know/resupply its full content — only
    the entry_id and the new dates."""
    svc = KernelService()
    await svc.upsert_knowledge(
        tenant_id="t1", knowledge_entry_id="kl_1", knowledge_kind="pattern",
        content="Exact original content that must survive revalidation untouched.",
        command_id="c1", capability_id="cap_1", review_due_at=_past_iso(),
    )
    revalidated = await svc.revalidate_knowledge(
        tenant_id="t1", knowledge_entry_id="kl_1", command_id="revalidate_1",
        review_due_at=_future_iso(),
    )
    assert revalidated["knowledge_kind"] == "pattern"
    assert revalidated["content"] == "Exact original content that must survive revalidation untouched."
    assert revalidated["revision"] == 2, "revalidation is still a real, versioned event, not a silent mutation"


@pytest.mark.asyncio
async def test_revalidate_nonexistent_entry_fails_closed() -> None:
    svc = KernelService()
    with pytest.raises(ExecutionKernelRepositoryError, match="not found"):
        await svc.revalidate_knowledge(tenant_id="t1", knowledge_entry_id="never_existed", command_id="c1")


@pytest.mark.asyncio
async def test_superseded_entries_never_appear_in_the_worklist() -> None:
    """A superseded entry going 'stale' needs no one's review — it was
    already deliberately replaced, not accidentally forgotten."""
    svc = KernelService()
    await svc.upsert_knowledge(
        tenant_id="t1", knowledge_entry_id="kl_old", knowledge_kind="architecture",
        content="Superseded guidance.", command_id="c1",
        capability_id="cap_1", review_due_at=_past_iso(),
    )
    await svc.upsert_knowledge(
        tenant_id="t1", knowledge_entry_id="kl_new", knowledge_kind="architecture",
        content="Replacement guidance.", command_id="c2", capability_id="cap_1",
    )
    await svc.upsert_knowledge(
        tenant_id="t1", knowledge_entry_id="kl_old", knowledge_kind="architecture",
        content="Superseded guidance.", command_id="c3",
        capability_id="cap_1", review_due_at=_past_iso(), superseded_by_entry_id="kl_new",
    )
    worklist = await svc.list_stale_knowledge(tenant_id="t1")
    assert not any(i["knowledge_entry_id"] == "kl_old" for i in worklist)
