"""Retired WorkHub task evidence client.

WorkHub legacy evidence writes were dev-only and have been removed. Current
certified execution writeback uses the WorkHub v2 AIX adapter endpoints.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.hyeve_aix_service.config import settings

RETIRED_ERROR = "WORKHUB_LEGACY_TASK_EVIDENCE_CLIENT_RETIRED"


@dataclass(slots=True)
class WorkhubTaskEvidenceClient:
    base_url: str
    timeout_seconds: int
    service_token: str | None

    async def add_model_usage_evidence(self, **_kwargs: Any) -> tuple[bool, str | None, dict[str, Any] | None]:
        return False, RETIRED_ERROR, None

    async def add_model_routing_decision_evidence(
        self, **_kwargs: Any
    ) -> tuple[bool, str | None, dict[str, Any] | None]:
        return False, RETIRED_ERROR, None

    async def add_redaction_report_evidence(self, **_kwargs: Any) -> tuple[bool, str | None, dict[str, Any] | None]:
        return False, RETIRED_ERROR, None

    async def add_codeintel_provenance_evidence(self, **_kwargs: Any) -> tuple[bool, str | None, dict[str, Any] | None]:
        return False, RETIRED_ERROR, None


workhub_task_evidence_client = WorkhubTaskEvidenceClient(
    base_url=str(settings.WORKHUB_API_URL),
    timeout_seconds=settings.WORKHUB_API_TIMEOUT_SECONDS,
    service_token=settings.WORKHUB_API_TOKEN,
)
