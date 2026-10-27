"""Ayrpack API client for packet submit/read/search/audit flows."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import httpx

from backend.hyeve_aix_service.config import settings
from backend.shared.request_header_utils import build_service_headers


@dataclass
class AyrpackClient:
    base_url: str
    timeout_seconds: int
    service_token: str | None
    max_retries: int = 2
    retry_backoff_seconds: float = 0.2

    async def submit_packet(
        self,
        *,
        tenant_id: str,
        project_id: str,
        correlation_id: str,
        packet_payload: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> tuple[dict[str, Any] | None, str | None]:
        if not self.service_token:
            return None, "AYRPACK_API_TOKEN_MISSING"
        headers = build_service_headers(
            service_token=self.service_token,
            tenant_id=tenant_id,
            project_id=project_id,
            correlation_id=correlation_id,
            idempotency_key=idempotency_key,
            require_auth=True,
            require_tenant=True,
        )
        return await self._request_json(
            method="POST",
            path="/v1/packets",
            headers=headers,
            json_payload=packet_payload,
        )

    async def get_packet(
        self,
        *,
        tenant_id: str,
        project_id: str,
        correlation_id: str,
        packet_id: str,
    ) -> tuple[dict[str, Any] | None, str | None]:
        if not self.service_token:
            return None, "AYRPACK_API_TOKEN_MISSING"
        headers = build_service_headers(
            service_token=self.service_token,
            tenant_id=tenant_id,
            project_id=project_id,
            correlation_id=correlation_id,
            require_auth=True,
            require_tenant=True,
        )
        return await self._request_json(
            method="GET",
            path=f"/v1/packets/{packet_id}",
            headers=headers,
        )

    async def search_packets(
        self,
        *,
        tenant_id: str,
        project_id: str,
        correlation_id: str,
        query_params: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any] | None, str | None]:
        if not self.service_token:
            return None, "AYRPACK_API_TOKEN_MISSING"
        headers = build_service_headers(
            service_token=self.service_token,
            tenant_id=tenant_id,
            project_id=project_id,
            correlation_id=correlation_id,
            require_auth=True,
            require_tenant=True,
        )
        return await self._request_json(
            method="GET",
            path="/v1/packets/search",
            headers=headers,
            params=query_params or {},
        )

    async def append_audit(
        self,
        *,
        tenant_id: str,
        project_id: str,
        correlation_id: str,
        packet_id: str,
        audit_payload: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, str | None]:
        if not self.service_token:
            return None, "AYRPACK_API_TOKEN_MISSING"
        headers = build_service_headers(
            service_token=self.service_token,
            tenant_id=tenant_id,
            project_id=project_id,
            correlation_id=correlation_id,
            require_auth=True,
            require_tenant=True,
        )
        return await self._request_json(
            method="POST",
            path=f"/v1/packets/{packet_id}/audit",
            headers=headers,
            json_payload=audit_payload,
        )

    async def _request_json(
        self,
        *,
        method: str,
        path: str,
        headers: dict[str, str],
        params: dict[str, Any] | None = None,
        json_payload: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any] | None, str | None]:
        url = f"{self.base_url.rstrip('/')}{path}"
        attempt = 0
        last_error: str | None = None
        while attempt <= self.max_retries:
            try:
                async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                    response = await client.request(
                        method=method,
                        url=url,
                        headers=headers,
                        params=params,
                        json=json_payload,
                    )
                    response.raise_for_status()
                    return response.json(), None
            except httpx.HTTPStatusError as exc:
                code = exc.response.status_code
                if code < 500:
                    return None, f"AYRPACK_HTTP_{code}"
                last_error = f"AYRPACK_HTTP_{code}"
            except Exception as exc:  # pragma: no cover
                last_error = str(exc)
            attempt += 1
            if attempt <= self.max_retries:
                await asyncio.sleep(self.retry_backoff_seconds * attempt)
        return None, last_error


ayrpack_client = AyrpackClient(
    base_url=str(settings.AYRPACK_API_URL),
    timeout_seconds=settings.AYRPACK_API_TIMEOUT_SECONDS,
    service_token=settings.AYRPACK_API_TOKEN,
)
