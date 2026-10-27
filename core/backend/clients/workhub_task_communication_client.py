"""Retired WorkHub task communication client."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.hyeve_aix_service.config import settings

RETIRED_ERROR = "WORKHUB_LEGACY_TASK_COMMUNICATION_CLIENT_RETIRED"


@dataclass(slots=True)
class WorkhubTaskCommunicationClient:
    base_url: str
    timeout_seconds: int
    service_token: str | None

    async def add_task_communication(self, **_kwargs: Any) -> tuple[bool, str | None, dict[str, Any] | None]:
        return False, RETIRED_ERROR, None

    async def list_task_coordination(self, **_kwargs: Any) -> tuple[list[dict[str, Any]], str | None]:
        return [], RETIRED_ERROR

    async def list_task_communication(self, **_kwargs: Any) -> tuple[list[dict[str, Any]], str | None]:
        return [], RETIRED_ERROR


workhub_task_communication_client = WorkhubTaskCommunicationClient(
    base_url=str(settings.WORKHUB_API_URL),
    timeout_seconds=settings.WORKHUB_API_TIMEOUT_SECONDS,
    service_token=settings.WORKHUB_API_TOKEN,
)

__all__ = ["WorkhubTaskCommunicationClient", "workhub_task_communication_client"]
