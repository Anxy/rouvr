"""Backend protocol required by the mounted objective-cycle API router."""

from __future__ import annotations

from typing import Any, Protocol


class ObjectiveCycleApiBackend(Protocol):
    async def command(
        self,
        *,
        operation: str,
        tenant_id: str,
        actor_id: str,
        resource_id: str | None,
        body: dict[str, Any],
        idempotency_key: str,
        expected_version: int | None,
    ) -> dict[str, Any]: ...

    async def query(
        self,
        *,
        operation: str,
        tenant_id: str,
        resource_id: str | None,
        child_id: str | None,
        filters: dict[str, Any],
    ) -> dict[str, Any]: ...
