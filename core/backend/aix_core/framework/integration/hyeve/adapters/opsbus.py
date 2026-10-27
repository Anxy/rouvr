"""AIX Framework Hyeve OpsBus Capability Adapter.

Sprint: F9 Hyeve Capability Layer (F9-P4)
Grounded in: OpsbusRunLogsClient, hyeve-opsbus-service manifest.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from backend.clients.opsbus_run_logs_client import OpsbusRunLogsClient
from backend.hyeve_aix_service.config import settings


class OpsBusCapabilityAdapter:
    """Canonical capability adapter posting run logs and telemetry to OpsBus with correlation."""

    def __init__(self, client: OpsbusRunLogsClient | None = None) -> None:
        self._client = client or OpsbusRunLogsClient(
            base_url=str(settings.HYEVE_OPSBUS_SERVICE_URL or "http://opsbus:8040"),
            timeout_seconds=int(settings.AIX_OPSBUS_HTTP_TIMEOUT_SECONDS or 10),
            service_token=settings.HYEVE_AIX_SERVICE_OPSBUS_API_TOKEN,
        )

    async def log_event(
        self,
        *,
        tenant_id: str,
        project_id: str,
        run_id: str,
        correlation_id: str | None,
        event_name: str,
        payload: dict[str, Any],
    ) -> tuple[bool, str | None]:
        """Post a correlated run log event to OpsBus (direct HTTP telemetry)."""
        cid = correlation_id or f"corr-{uuid4().hex}"
        record_payload = {
            "event_name": event_name,
            "correlation_id": cid,
            "data": payload,
        }
        return await self._client._post_run_log(
            tenant_id=tenant_id,
            project_id=project_id,
            run_id=run_id,
            correlation_id=cid,
            payload=record_payload,
        )
