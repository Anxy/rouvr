"""Hydration resolvers: CodeIntel + WorkHub pointer resolution (Stage 5 wiring).

Replaces the hydrate stub's empty `resolved` with real lookups once
configured. Both resolvers take injected fetchers (hermetic tests);
production wiring supplies HTTP clients with service auth at deploy.
Unconfigured resolvers behave exactly like the stub (empty lists) —
no behavior change until wired.

Tenant isolation is enforced here, not just at the API: artifacts or
mandates from another tenant are dropped even if a fetcher returns them.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable


async def resolve_codeintel(
    fetcher: Callable[[str, str], Awaitable[list[dict[str, Any]]]] | None,
    *,
    tenant_id: str,
    project_id: str,
    kinds: list[str],
) -> list[dict[str, Any]]:
    """Resolve artifact pointers via CodeIntel read-by-kind (pure I/O edge)."""
    if fetcher is None:
        return []
    resolved: list[dict[str, Any]] = []
    for kind in kinds:
        for artifact in await fetcher(project_id, kind):
            if not isinstance(artifact, dict):
                continue
            if artifact.get("tenant_id") != tenant_id:
                continue
            resolved.append({
                "id": artifact.get("id"),
                "kind": artifact.get("artifact_kind"),
                "title": artifact.get("title"),
            })
    return resolved


async def resolve_workhub(
    fetcher: Callable[[], Awaitable[list[dict[str, Any]]]] | None,
    *,
    tenant_id: str,
) -> list[str]:
    """Resolve open mandate IDs via WorkHub v2 list (pure I/O edge)."""
    if fetcher is None:
        return []
    ids: list[str] = []
    for task in await fetcher():
        if not isinstance(task, dict):
            continue
        if task.get("tenant_id", tenant_id) != tenant_id:
            continue
        tid = task.get("id") or task.get("task_id")
        if tid:
            ids.append(str(tid))
    return ids
