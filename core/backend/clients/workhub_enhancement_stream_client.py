"""Retired WorkHub enhancement stream client."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.hyeve_aix_service.config import settings

RETIRED_ERROR = "WORKHUB_LEGACY_ENHANCEMENT_STREAM_CLIENT_RETIRED"


@dataclass(slots=True)
class WorkhubEnhancementStreamClient:
    base_url: str
    timeout_seconds: int
    service_token: str | None

    async def log_retrieval_override(self, **_kwargs: Any) -> tuple[bool, str | None]:
        return False, RETIRED_ERROR

    async def log_action_map_drift_proposal(self, **_kwargs: Any) -> tuple[bool, str | None]:
        return False, RETIRED_ERROR

    async def log_tenant_threshold_alert(self, **_kwargs: Any) -> tuple[bool, str | None]:
        return False, RETIRED_ERROR

    async def log_tenant_threshold_policy_update(self, **_kwargs: Any) -> tuple[bool, str | None]:
        return False, RETIRED_ERROR


workhub_enhancement_stream_client = WorkhubEnhancementStreamClient(
    base_url=str(settings.WORKHUB_API_URL),
    timeout_seconds=settings.WORKHUB_API_TIMEOUT_SECONDS,
    service_token=settings.WORKHUB_API_TOKEN,
)
