"""Retired WorkHub feedback intake client."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.hyeve_aix_service.config import settings

RETIRED_ERROR = "WORKHUB_LEGACY_FEEDBACK_INTAKE_CLIENT_RETIRED"


@dataclass(slots=True)
class WorkhubFeedbackIntakeClient:
    base_url: str
    timeout_seconds: int
    service_token: str | None

    async def create_feedback_intake_task(self, **_kwargs: Any) -> tuple[dict[str, Any] | None, str | None]:
        return None, RETIRED_ERROR


workhub_feedback_intake_client = WorkhubFeedbackIntakeClient(
    base_url=str(settings.WORKHUB_API_URL),
    timeout_seconds=settings.WORKHUB_API_TIMEOUT_SECONDS,
    service_token=settings.WORKHUB_API_TOKEN,
)
