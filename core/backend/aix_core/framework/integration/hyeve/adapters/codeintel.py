"""AIX Framework Hyeve CodeIntel Capability Adapter.

Sprint: F9 Hyeve Capability Layer (F9-P3)
Grounded in: CodeintelQueryClient, hyeve-codeintel-service manifest.
"""

from __future__ import annotations

from typing import Any

from backend.clients.codeintel_query_client import CodeintelQueryClient
from backend.hyeve_aix_service.config import settings


class CodeIntelCapabilityAdapter:
    """Canonical capability adapter querying CodeIntel symbol index and context."""

    def __init__(self, client: CodeintelQueryClient | None = None) -> None:
        self._client = client or CodeintelQueryClient(
            base_url=str(settings.CODEINTEL_API_URL),
            timeout_seconds=int(settings.CODEINTEL_API_TIMEOUT_SECONDS),
            service_token=settings.CODEINTEL_API_TOKEN,
        )

    async def get_code_context(
        self,
        *,
        tenant_id: str,
        project_id: str,
        entity_query: str | None,
        retrieval_policy: dict[str, Any] | None = None,
        limit: int = 5,
    ) -> tuple[dict[str, Any], str | None]:
        """Query CodeIntel symbol index and contextual relationships."""
        return await self._client.get_context(
            tenant_id=tenant_id,
            project_id=project_id,
            entity_query=entity_query,
            retrieval_policy=retrieval_policy,
            limit=limit,
        )

    async def query_symbols(
        self,
        *,
        tenant_id: str,
        project_id: str,
        symbol_query: str,
        limit: int = 5,
    ) -> tuple[list[dict[str, Any]], str | None]:
        """Extract matched entity symbols from CodeIntel query.

        Returns:
            A tuple of (symbols, error) where error is None on success or a
            backend failure string on error.
        """
        context, error = await self.get_code_context(
            tenant_id=tenant_id,
            project_id=project_id,
            entity_query=symbol_query,
            limit=limit,
        )
        if error:
            return [], error
        entities = context.get("entities")
        if isinstance(entities, list):
            return ([e for e in entities if isinstance(e, dict)], None)
        return ([], None)
