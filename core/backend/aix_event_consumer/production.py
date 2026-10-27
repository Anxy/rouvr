"""Production adapters for the maintained AIX event consumer.

The OpsBus adapter is deliberately read-only.  OpsBus currently exposes the
canonical events collection but no maintained consumer-group claim/ack API in
this repository.  A durable cursor prevents ordinary redelivery, and the
Temporal workflow receiver supplies the crash-window idempotency boundary.
"""
from __future__ import annotations

import asyncio
import json
import urllib.parse
import urllib.request
from typing import Any

from backend.infrastructure.temporal.runtime import TemporalRuntimeManager

from .temporal import TemporalAction, TemporalResult


class OpsBusHttpEventSource:
    """Read canonical OpsBus events with service authentication."""

    def __init__(
        self,
        *,
        base_url: str,
        token: str,
        tenant_id: str,
        stream_key: str | None = None,
        timeout_seconds: float = 10.0,
        page_size: int = 100,
    ) -> None:
        if not token.strip() or not tenant_id.strip():
            raise ValueError("OPSBUS_AUTH_REQUIRED")
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._tenant_id = tenant_id
        self._stream_key = stream_key
        self._timeout = timeout_seconds
        self._page_size = page_size

    def poll_events(self) -> list[dict[str, Any]]:
        query: dict[str, str | int] = {"limit": self._page_size}
        if self._stream_key:
            query["stream_key"] = self._stream_key
        url = f"{self._base_url}/api/v1/events?{urllib.parse.urlencode(query)}"
        request = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {self._token}",
                "X-Hyeve-Tenant-Id": self._tenant_id,
            },
        )
        with urllib.request.urlopen(request, timeout=self._timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
        data = body.get("data", body) if isinstance(body, dict) else body
        if isinstance(data, dict):
            data = data.get("items", data.get("events", []))
        return [dict(item) for item in data] if isinstance(data, list) else []


class TemporalClientAdapter:
    """Thread-safe synchronous adapter over the canonical Temporal runtime."""

    def __init__(self, *, manager: TemporalRuntimeManager, loop: asyncio.AbstractEventLoop) -> None:
        self._manager = manager
        self._loop = loop

    def _run(self, awaitable: Any) -> Any:
        return asyncio.run_coroutine_threadsafe(awaitable, self._loop).result()

    async def _client(self) -> Any:
        sdk = self._manager._sdk_loader()  # canonical loader/configuration
        if sdk is None:
            raise RuntimeError("TEMPORAL_SDK_NOT_INSTALLED")
        return await sdk.client_cls.connect(
            str(getattr(self._manager._settings, "TEMPORAL_ADDRESS", "127.0.0.1:7233")),
            namespace=self._manager.binding.namespace,
        )

    async def _describe(self, workflow_id: str) -> bool:
        try:
            await (await self._client()).get_workflow_handle(workflow_id).describe()
            return True
        except Exception as exc:
            if type(exc).__name__ in {"WorkflowNotFoundError", "RPCError"} and "not found" in str(exc).lower():
                return False
            raise

    def has_workflow(self, workflow_id: str) -> bool:
        return bool(self._run(self._describe(workflow_id)))

    async def _act(self, action: TemporalAction) -> TemporalResult:
        if action.mode == "start":
            result = await self._manager.canonical_start(raw=action.workflow_input or {})
            return TemporalResult(action.workflow_id, str(result.get("run_id", "")), action.task_queue, "start", True)
        handle = (await self._client()).get_workflow_handle(action.workflow_id)
        await handle.signal(action.signal_name, action.signal_payload)
        return TemporalResult(action.workflow_id, "", action.task_queue, "signal", True)

    def start_or_signal(self, action: TemporalAction) -> TemporalResult:
        return self._run(self._act(action))
