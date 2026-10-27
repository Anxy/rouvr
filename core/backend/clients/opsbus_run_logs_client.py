"""OpsBus run logs telemetry writer client."""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from backend.hyeve_aix_service.config import settings
from backend.shared.request_header_utils import build_service_headers


@dataclass
class OpsbusRunLogsClient:
    base_url: str
    timeout_seconds: int
    service_token: str | None

    async def _post_run_log(
        self,
        *,
        tenant_id: str,
        project_id: str,
        run_id: str,
        correlation_id: str,
        payload: dict,
    ) -> tuple[bool, str | None]:
        if not self.service_token:
            return False, "OPSBUS_API_TOKEN_MISSING"
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(
                    f"{self.base_url.rstrip('/')}/api/v1/runs/{run_id}/logs",
                    json=payload,
                    headers=build_service_headers(
                        service_token=self.service_token,
                        project_id=project_id,
                        correlation_id=correlation_id,
                        require_auth=True,
                        require_tenant=False,
                    ),
                )
                if response.status_code == 404:
                    retry_run_id = await self._dispatch_shadow_run(
                        client=client,
                        tenant_id=tenant_id,
                        project_id=project_id,
                        logical_run_id=run_id,
                        correlation_id=correlation_id,
                    )
                    if retry_run_id:
                        metadata_payload = dict(payload.get("metadata_json") or {})
                        payload["metadata_json"] = {
                            **metadata_payload,
                            "logical_run_id": run_id,
                            "dispatched_run_id": retry_run_id,
                        }
                        retry = await client.post(
                            f"{self.base_url.rstrip('/')}/api/v1/runs/{retry_run_id}/logs",
                            json=payload,
                            headers=build_service_headers(
                                service_token=self.service_token,
                                project_id=project_id,
                                correlation_id=correlation_id,
                                require_auth=True,
                                require_tenant=False,
                            ),
                        )
                        retry.raise_for_status()
                        return True, None
                response.raise_for_status()
            return True, None
        except Exception as exc:  # pragma: no cover
            return False, str(exc)

    async def list_run_logs(
        self,
        *,
        tenant_id: str,
        project_id: str,
        run_id: str,
        correlation_id: str,
        limit: int = 200,
        offset: int = 0,
    ) -> tuple[dict, str | None]:
        _ = tenant_id
        if not self.service_token:
            return {}, "OPSBUS_API_TOKEN_MISSING"
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.get(
                    f"{self.base_url.rstrip('/')}/api/v1/runs/{run_id}/logs",
                    params={
                        "project_id": project_id,
                        "limit": max(1, min(int(limit), 1000)),
                        "offset": max(0, int(offset)),
                    },
                    headers=build_service_headers(
                        service_token=self.service_token,
                        project_id=project_id,
                        correlation_id=correlation_id,
                        require_auth=True,
                        require_tenant=False,
                    ),
                )
                response.raise_for_status()
            payload = response.json() if isinstance(response.json(), dict) else {}
            data = payload.get("data")
            return (data if isinstance(data, dict) else {}), None
        except Exception as exc:  # pragma: no cover
            return {}, str(exc)

    async def log_model_usage(
        self,
        *,
        tenant_id: str,
        project_id: str,
        run_id: str,
        correlation_id: str,
        actor_id: str,
        usage_block: dict,
    ) -> tuple[bool, str | None]:
        metadata_payload = {"model_usage": usage_block}
        payload = {
            "project_id": project_id,
            "log_level": "info",
            "message": "AIX model usage telemetry",
            "metadata_json": metadata_payload,
        }
        return await self._post_run_log(
            tenant_id=tenant_id,
            project_id=project_id,
            run_id=run_id,
            correlation_id=correlation_id,
            payload=payload,
        )

    async def log_run_event(
        self,
        *,
        tenant_id: str,
        project_id: str,
        run_id: str,
        correlation_id: str,
        event_name: str,
        event_payload: dict,
    ) -> tuple[bool, str | None]:
        payload = {
            "project_id": project_id,
            "log_level": "info",
            "message": event_name,
            "metadata_json": {
                "event_name": event_name,
                "event_payload": event_payload,
            },
        }
        return await self._post_run_log(
            tenant_id=tenant_id,
            project_id=project_id,
            run_id=run_id,
            correlation_id=correlation_id,
            payload=payload,
        )

    async def _dispatch_shadow_run(
        self,
        *,
        client: httpx.AsyncClient,
        tenant_id: str,
        project_id: str,
        logical_run_id: str,
        correlation_id: str,
    ) -> str | None:
        dispatch_payload = {
            "project_id": project_id,
            "source_type": "aix_writeback",
            "source_id": logical_run_id,
            "priority": "normal",
            "payload": {"logical_run_id": logical_run_id, "tenant_id": tenant_id},
            "correlation_id": correlation_id,
            "idempotency_key": f"aix-writeback-shadow:{logical_run_id}",
        }
        response = await client.post(
            f"{self.base_url.rstrip('/')}/api/v1/dispatch",
            json=dispatch_payload,
            headers=build_service_headers(
                service_token=self.service_token,
                project_id=project_id,
                correlation_id=correlation_id,
                require_auth=True,
                require_tenant=False,
            ),
        )
        response.raise_for_status()
        data = response.json().get("data") if isinstance(response.json(), dict) else None
        if not isinstance(data, dict):
            return None
        value = data.get("id")
        return str(value).strip() if value else None


opsbus_run_logs_client = OpsbusRunLogsClient(
    base_url=str(settings.OPSBUS_API_URL),
    timeout_seconds=settings.OPSBUS_API_TIMEOUT_SECONDS,
    service_token=settings.OPSBUS_API_TOKEN,
)
