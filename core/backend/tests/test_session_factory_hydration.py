"""Hermetic hydration tests: tenant filtering + unconfigured behavior."""

import asyncio

from backend.session_factory.hydration import resolve_codeintel, resolve_workhub


def run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


async def _artifacts(project_id, kind):
    assert project_id == "p"
    if kind == "context_pack_document":
        return [
            {"id": "a1", "artifact_kind": kind, "title": "t1",
             "tenant_id": "t"},
            {"id": "ax", "artifact_kind": kind, "title": "evil",
             "tenant_id": "other"},
            "garbage",
        ]
    return []


async def _tasks():
    return [{"id": "m1", "tenant_id": "t"}, {"id": "m2"},
            {"id": "mx", "tenant_id": "other"}, None]


def test_codeintel_resolves_and_filters_tenant() -> None:
    out = run(resolve_codeintel(
        _artifacts, tenant_id="t", project_id="p",
        kinds=["context_pack_document", "generated_summary"]))
    assert [a["id"] for a in out] == ["a1"]


def test_codeintel_unconfigured_is_empty() -> None:
    assert run(resolve_codeintel(
        None, tenant_id="t", project_id="p", kinds=["x"])) == []


def test_workhub_resolves_own_mandates() -> None:
    assert run(resolve_workhub(
        _tasks, tenant_id="t")) == ["m1", "m2"]
    assert run(resolve_workhub(None, tenant_id="t")) == []
