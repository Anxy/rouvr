"""BH-backed canonical model registry client for AIX."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from aix.contracts.gateway import RegistryModel


class BhModelRegistryError(RuntimeError):
    """Raised when canonical BH model registry calls fail."""


@dataclass(frozen=True)
class BhModelRegistryClient:
    """Reads canonical models from BH `bh_ai_models` only."""

    bh_base_url: str
    token: str
    tenant_id: str
    timeout_seconds: int = 30

    def list_active_models(self, *, project_id: str | None = None, limit: int = 200) -> list[RegistryModel]:
        """Fetch active models from `/api/bh-ai-models/audit` and map into typed records."""

        query = urlencode({"limit": limit, "offset": 0})
        url = f"{self.bh_base_url.rstrip('/')}/api/bh-ai-models/audit?{query}"
        request = Request(url=url, method="GET")
        request.add_header("Authorization", f"Bearer {self.token}")
        request.add_header("X-Tenant-ID", self.tenant_id)
        request.add_header("Accept", "application/json")

        with urlopen(request, timeout=self.timeout_seconds) as response:
            payload: dict[str, Any] = json.loads(response.read().decode("utf-8"))

        data = payload.get("data")
        if not isinstance(data, list):
            raise BhModelRegistryError("Unexpected BH response shape for bh_ai_models.")

        models: list[RegistryModel] = []
        for row in data:
            if not isinstance(row, dict):
                continue
            if row.get("tenant_id") != self.tenant_id:
                continue
            if project_id and row.get("project_id") not in (None, project_id):
                continue
            if not bool(row.get("is_active", True)):
                continue
            try:
                models.append(RegistryModel.model_validate(row))
            except Exception as exc:  # pragma: no cover - defensive shape guard
                raise BhModelRegistryError(f"Invalid BH model row: {exc}") from exc
        return models
