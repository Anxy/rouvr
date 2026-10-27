"""Retired WorkHub knowledge client."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.hyeve_aix_service.config import settings

RETIRED_ERROR = "WORKHUB_LEGACY_KNOWLEDGE_CLIENT_RETIRED"


@dataclass(slots=True)
class WorkhubKnowledgeClient:
    base_url: str
    timeout_seconds: int
    service_token: str | None

    async def search_knowledge(self, **_kwargs: Any) -> tuple[list[dict], str | None]:
        return [], RETIRED_ERROR

    def search_knowledge_sync(self, **_kwargs: Any) -> tuple[list[dict], str | None]:
        return [], RETIRED_ERROR

    async def create_knowledge(self, **_kwargs: Any) -> tuple[str | None, str | None]:
        return None, RETIRED_ERROR

    async def update_knowledge(self, **_kwargs: Any) -> tuple[bool, str | None]:
        return False, RETIRED_ERROR

    async def add_task_comment(self, **_kwargs: Any) -> tuple[bool, str | None]:
        return False, RETIRED_ERROR


workhub_knowledge_client = WorkhubKnowledgeClient(
    base_url=str(settings.WORKHUB_API_URL),
    timeout_seconds=settings.WORKHUB_API_TIMEOUT_SECONDS,
    service_token=settings.WORKHUB_API_TOKEN,
)
