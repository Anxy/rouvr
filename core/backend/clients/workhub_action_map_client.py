"""Retired WorkHub action-map client."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.hyeve_aix_service.config import settings

RETIRED_ERROR = "WORKHUB_LEGACY_ACTION_MAP_CLIENT_RETIRED"


@dataclass(slots=True)
class WorkhubActionMapClient:
    base_url: str
    timeout_seconds: int
    service_token: str | None

    def invalidate_cache(self, **_kwargs: Any) -> None:
        return None

    async def get_active_action_map(self, **_kwargs: Any) -> tuple[dict[str, Any] | None, str | None]:
        return None, RETIRED_ERROR

    async def get_action_map_for_worker(
        self, **_kwargs: Any
    ) -> tuple[dict[str, Any] | None, str | None, dict[str, Any]]:
        return None, RETIRED_ERROR, {"cache_hit": False}

    async def create_proposed_action_map(self, **_kwargs: Any) -> tuple[dict[str, Any] | None, str | None]:
        return None, RETIRED_ERROR


workhub_action_map_client = WorkhubActionMapClient(
    base_url=str(settings.WORKHUB_API_URL),
    timeout_seconds=settings.WORKHUB_API_TIMEOUT_SECONDS,
    service_token=settings.WORKHUB_API_TOKEN,
)
