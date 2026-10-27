from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import httpx

from backend.hyeve_aix_service.config import settings
from backend.shared.request_header_utils import build_service_headers
from backend.aix_core.framework.integration.workhub.boundary import (
    WorkHubBoundary,
    WorkHubOperation,
)


class WorkHubAuthorityDenied(PermissionError):
    """Raised when WorkHubBoundary denies a write operation."""

    def __init__(self, operation: str, reason: str, requires_flag: str | None = None) -> None:
        self.operation = operation
        self.reason = reason
        self.requires_flag = requires_flag
        super().__init__(f"{operation}: {reason}")


def _workhub_flags() -> tuple[bool, bool, bool]:
    """(create_task_flag, mark_done_flag, lifecycle_writes_enabled)."""
    return (
        bool(getattr(settings, "OBJECTIVE_CYCLE_CREATE_WORKHUB_TASK", False)),
        bool(getattr(settings, "OBJECTIVE_CYCLE_MARK_WORKHUB_DONE", False)),
        bool(getattr(settings, "AIX_ALLOW_WORKHUB_LIFECYCLE_WRITES", False)),
    )


def assert_workhub_write_allowed(operation: WorkHubOperation | str) -> None:
    """Enforce WorkHubBoundary on live write paths (fail closed)."""
    create_flag, mark_done_flag, lifecycle = _workhub_flags()
    decision = WorkHubBoundary.evaluate_operation(
        operation,
        create_task_flag=create_flag or lifecycle,
        mark_done_flag=mark_done_flag or lifecycle,
        lifecycle_writes_enabled=lifecycle,
    )
    if not decision.allowed:
        raise WorkHubAuthorityDenied(
            operation=str(getattr(decision.operation, "value", decision.operation)),
            reason=decision.reason,
            requires_flag=decision.requires_flag,
        )


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_items(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def _tenant_from_service_token(token: str | None) -> str | None:
    raw = str(token or "").strip()
    if not raw:
        return None
    if raw.lower().startswith("bearer "):
        raw = raw[7:].strip()
    parts = raw.split(".")
    if len(parts) < 2:
        return None
    try:
        padded = parts[1] + ("=" * (-len(parts[1]) % 4))
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("utf-8")))
    except Exception:  # noqa: BLE001 - best-effort JWT decode
        return None
    return str(payload.get("tenant_id") or "").strip() or None


@dataclass(slots=True)
class WorkHubTaskClient:
    base_url: str
    service_token: str | None
    closeout_service_token: str | None = None
    timeout_seconds: float = 15.0

    def _headers(
        self,
        *,
        project_id: str,
        tenant_id: str | None = None,
        correlation_id: str | None = None,
        idempotency_key: str | None = None,
        service_token: str | None = None,
    ) -> dict[str, str]:
        return build_service_headers(
            service_token=str(service_token if service_token is not None else self.service_token or "").strip() or None,
            tenant_id=tenant_id,
            project_id=project_id,
            correlation_id=correlation_id,
            require_auth=True,
            require_tenant=False,
            idempotency_key=str(idempotency_key or "").strip() or str(uuid4()),
        )

    async def get_task_by_number(
        self, *, project_id: str, task_number: int, tenant_id: str | None = None
    ) -> dict[str, Any] | None:
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.get(
                f"{self.base_url.rstrip('/')}/api/v1/projects/{project_id}/tasks/by-number/{int(task_number)}",
                headers=self._hyve_headers(project_id=project_id, tenant_id=tenant_id),
            )
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return _as_dict(response.json().get("data"))

    async def get_assignment_binding(
        self, *, project_id: str, task_number: int, tenant_id: str | None = None
    ) -> dict[str, Any] | None:
        """Fencing pins for the task's active assignment (lane-ops #23)."""
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.get(
                f"{self.base_url.rstrip('/')}/api/v1/projects/{project_id}/tasks/by-number/{int(task_number)}/assignment-binding",
                headers=self._hyve_headers(project_id=project_id, tenant_id=tenant_id),
            )
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return _as_dict(response.json().get("data") or response.json())

    async def list_task_evidence(
        self, *, project_id: str, task_id: str, tenant_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Evidence rows for attempt-lineage proof (lane-ops #23).

        The evidence GET returns {success, data: {items, total, has_more}};
        every page is walked so absence verdicts never read a truncation.
        """
        rows: list[dict[str, Any]] = []
        offset = 0
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            while True:
                response = await client.get(
                    f"{self.base_url.rstrip('/')}/api/v1/tasks/{task_id}/evidence",
                    headers=self._hyve_headers(project_id=project_id, tenant_id=tenant_id),
                    params={"limit": 500, "offset": offset},
                )
                if response.status_code == 404:
                    return []
                response.raise_for_status()
                payload = response.json()
                data = payload.get("data") if isinstance(payload, dict) else None
                if isinstance(data, dict):
                    page = data.get("items") or []
                    has_more = bool(data.get("has_more"))
                else:
                    page = payload.get("items") if isinstance(payload, dict) else payload
                    has_more = False
                page = _as_items(page)
                if not page:
                    break
                rows.extend(page)
                if not has_more:
                    break
                offset += len(page)
        return rows

    async def get_task_ai_context_markdown(
        self, *, project_id: str, task_number: int, tenant_id: str | None = None
    ) -> str | None:
        """Fetch WorkHub's pre-built ai_context briefing for this task, rendered as a
        single markdown document (WorkHub's `?include_ai_context=true&format=markdown`).

        This exists so `load_task` can hand the run a compact, already-computed briefing
        (relevant files, conventions, prior attempts on this same task, matched
        CodeIntel symbols) instead of the run needing to cold-explore the repo itself to
        rediscover the same information on every pickup. Returns `None` on any failure
        (404, timeout, WorkHub down) -- this is a best-effort optimization, never a hard
        dependency: a run must still be able to load and proceed without it, just at the
        cost of more of its own exploration.
        """
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            try:
                response = await client.get(
                    f"{self.base_url.rstrip('/')}/api/v1/projects/{project_id}/tasks/by-number/{int(task_number)}",
                    headers=self._hyve_headers(project_id=project_id, tenant_id=tenant_id),
                    params={"include_ai_context": "true", "format": "markdown"},
                )
                if response.status_code != 200:
                    return None
                return response.text
            except Exception:  # noqa: BLE001 - best-effort briefing fetch, never blocks task load
                return None

    async def list_task_execution_contracts(
        self, *, project_id: str, task_number: int, tenant_id: str | None = None
    ) -> list[dict[str, Any]]:
        task = await self.get_task_by_number(project_id=project_id, task_number=task_number, tenant_id=tenant_id)
        if not task:
            return []
        contracts = _as_items(task.get("execution_contracts"))
        if contracts:
            return contracts
        active_contract = _as_dict(task.get("active_execution_contract"))
        return [active_contract] if active_contract else []

    async def list_tasks(
        self,
        *,
        project_id: str,
        limit: int = 200,
        status: str | None = None,
        tenant_id: str | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": max(1, int(limit or 200))}
        status_text = str(status or "").strip()
        if status_text:
            params["status"] = status_text
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.get(
                f"{self.base_url.rstrip('/')}/api/v1/projects/{project_id}/tasks",
                headers=self._hyve_headers(project_id=project_id, tenant_id=tenant_id),
                params=params,
            )
            if response.status_code == 404:
                return []
            response.raise_for_status()
            return _as_items(_as_dict(response.json().get("data")).get("items") or response.json().get("data"))

    def _list_tasks(
        self,
        *,
        project_id: str,
        limit: int = 200,
        status: str | None = None,
        tenant_id: str | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": max(1, int(limit or 200))}
        status_text = str(status or "").strip()
        if status_text:
            params["status"] = status_text
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.get(
                f"{self.base_url.rstrip('/')}/api/v1/projects/{project_id}/tasks",
                headers=self._hyve_headers(project_id=project_id, tenant_id=tenant_id),
                params=params,
            )
            if response.status_code == 404:
                return []
            response.raise_for_status()
            return _as_items(_as_dict(response.json().get("data")).get("items") or response.json().get("data"))

    async def create_task(
        self,
        *,
        project_id: str,
        title: str,
        description: str | None = None,
        priority: str = "medium",
        status_value: str = "queued",
        tenant_id: str | None = None,
        idempotency_key: str | None = None,
        metadata_json: dict[str, Any] | None = None,
        tags_json: list[str] | None = None,
    ) -> dict[str, Any]:
        """Create a WorkHub task via POST /api/v1/tasks.

        WorkHub create forbids canonical X-Tenant-ID but requires X-Hyeve-Tenant-Id.
        """
        assert_workhub_write_allowed(WorkHubOperation.TASK_CREATE)
        payload: dict[str, Any] = {
            "project_id": project_id,
            "title": title,
            "description": description,
            "priority": priority,
            "status": status_value,
            "verifier_required": True,
            "evidence_required": True,
            "allow_weak_closure": False,
            "metadata_json": dict(metadata_json or {}),
            "tags_json": list(tags_json or []),
        }
        # Never send X-Tenant-ID (contract rejects it). Send X-Hyeve-Tenant-Id instead.
        headers = self._headers(
            project_id=project_id,
            tenant_id=None,
            idempotency_key=idempotency_key,
        )
        # Prefer JWT tenant (WorkHub SoR); gateway cycle tenant ids like tenant-live are not WorkHub tenants.
        resolved_tenant = _tenant_from_service_token(self.service_token) or str(tenant_id or "").strip()
        if resolved_tenant:
            headers["X-Hyeve-Tenant-Id"] = resolved_tenant
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(
                f"{self.base_url.rstrip('/')}/api/v1/tasks",
                headers=headers,
                json=payload,
            )
            if response.is_error:
                detail = response.text[:800]
                raise httpx.HTTPStatusError(
                    f"{response.status_code} {response.reason_phrase}: {detail}",
                    request=response.request,
                    response=response,
                )
            body = _as_dict(response.json())
            return _as_dict(body.get("data") or body)

    async def get_aix_adapter_read_contract(
        self,
        *,
        project_id: str,
        task_id: str,
        tenant_id: str | None = None,
        correlation_id: str | None = None,
    ) -> tuple[bool, str | None, dict[str, Any] | None]:
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            try:
                response = await client.get(
                    f"{self.base_url.rstrip('/')}/api/workhub/projects/{project_id}/aix/tasks/{task_id}/read-contract",
                    headers=self._headers(project_id=project_id, tenant_id=tenant_id, correlation_id=correlation_id),
                )
                response.raise_for_status()
                return True, None, _as_dict(response.json().get("data"))
            except Exception as exc:
                return False, str(exc), None

    async def attach_aix_adapter_evidence(
        self,
        *,
        project_id: str,
        task_id: str,
        evidence_bundle: dict[str, Any],
        idempotency_key: str,
        tenant_id: str | None = None,
        correlation_id: str | None = None,
    ) -> tuple[bool, str | None, dict[str, Any] | None]:
        assert_workhub_write_allowed(WorkHubOperation.EVIDENCE_ATTACH)
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            try:
                response = await client.post(
                    f"{self.base_url.rstrip('/')}/api/workhub/projects/{project_id}/aix/tasks/{task_id}/evidence",
                    headers=self._headers(
                        project_id=project_id,
                        tenant_id=tenant_id,
                        correlation_id=correlation_id,
                        idempotency_key=idempotency_key,
                    ),
                    json=dict(evidence_bundle),
                )
                response.raise_for_status()
                return True, None, _as_dict(response.json().get("data"))
            except Exception as exc:
                return False, str(exc), None

    async def create_aix_adapter_verification(
        self,
        *,
        project_id: str,
        task_id: str,
        verification_record: dict[str, Any],
        idempotency_key: str,
        tenant_id: str | None = None,
        correlation_id: str | None = None,
    ) -> tuple[bool, str | None, dict[str, Any] | None]:
        assert_workhub_write_allowed(WorkHubOperation.VERIFICATION_RECORD)
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            try:
                response = await client.post(
                    f"{self.base_url.rstrip('/')}/api/workhub/projects/{project_id}/aix/tasks/{task_id}/verifications",
                    headers=self._headers(
                        project_id=project_id,
                        tenant_id=tenant_id,
                        correlation_id=correlation_id,
                        idempotency_key=idempotency_key,
                    ),
                    json=dict(verification_record),
                )
                response.raise_for_status()
                return True, None, _as_dict(response.json().get("data"))
            except Exception as exc:
                return False, str(exc), None

    async def closeout_aix_adapter_task(
        self,
        *,
        project_id: str,
        task_id: str,
        closeout_request: dict[str, Any],
        idempotency_key: str,
        tenant_id: str | None = None,
        correlation_id: str | None = None,
    ) -> tuple[bool, str | None, dict[str, Any] | None]:
        assert_workhub_write_allowed(WorkHubOperation.TASK_CLOSE)
        closeout_token = str(self.closeout_service_token or "").strip()
        if not closeout_token:
            return False, "WORKHUB_CLOSEOUT_API_TOKEN_MISSING", None
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            try:
                response = await client.post(
                    f"{self.base_url.rstrip('/')}/api/workhub/projects/{project_id}/aix/tasks/{task_id}/closeout",
                    headers=self._headers(
                        project_id=project_id,
                        tenant_id=tenant_id,
                        correlation_id=correlation_id,
                        idempotency_key=idempotency_key,
                        service_token=closeout_token,
                    ),
                    json=dict(closeout_request),
                )
                response.raise_for_status()
                return True, None, _as_dict(response.json().get("data"))
            except Exception as exc:
                return False, str(exc), None

    async def transition_aix_adapter_task(
        self,
        *,
        project_id: str,
        task_id: str,
        transition_request: dict[str, Any],
        idempotency_key: str,
        tenant_id: str | None = None,
        correlation_id: str | None = None,
    ) -> tuple[bool, str | None, dict[str, Any] | None]:
        assert_workhub_write_allowed(WorkHubOperation.STATE_TRANSITION)
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            try:
                response = await client.post(
                    f"{self.base_url.rstrip('/')}/api/workhub/projects/{project_id}/aix/tasks/{task_id}/transitions",
                    headers=self._headers(
                        project_id=project_id,
                        tenant_id=tenant_id,
                        correlation_id=correlation_id,
                        idempotency_key=idempotency_key,
                    ),
                    json=dict(transition_request),
                )
                response.raise_for_status()
                return True, None, _as_dict(response.json().get("data"))
            except Exception as exc:
                return False, str(exc), None

    def _hyve_headers(
        self,
        *,
        project_id: str,
        tenant_id: str | None = None,
        correlation_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, str]:
        """Headers for WorkHub v1 APIs that forbid X-Tenant-ID but require X-Hyeve-Tenant-Id."""
        headers = self._headers(
            project_id=project_id,
            tenant_id=None,
            correlation_id=correlation_id,
            idempotency_key=idempotency_key,
        )
        resolved = _tenant_from_service_token(self.service_token) or str(tenant_id or "").strip()
        if resolved:
            headers["X-Hyeve-Tenant-Id"] = resolved
        return headers

    async def get_task(
        self,
        *,
        project_id: str,
        task_id: str,
        tenant_id: str | None = None,
        task_number: int | None = None,
    ) -> dict[str, Any] | None:
        if task_number is not None:
            by_number = await self.get_task_by_number(
                project_id=project_id,
                task_number=int(task_number),
                tenant_id=tenant_id,
            )
            if by_number:
                return by_number
        # WorkHub has no GET /tasks/{id}; scan project task list.
        items = await self.list_tasks(project_id=project_id, limit=500, tenant_id=tenant_id)
        want = str(task_id).strip()
        for item in items:
            if str(item.get("id") or item.get("task_id") or "").strip() == want:
                return item
        return None

    async def transition_task(
        self,
        *,
        project_id: str,
        task_id: str,
        status_value: str,
        reason: str | None = None,
        tenant_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        assert_workhub_write_allowed(WorkHubOperation.STATE_TRANSITION)
        payload: dict[str, Any] = {"status": status_value, "project_id": project_id}
        if reason:
            payload["reason"] = reason
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(
                f"{self.base_url.rstrip('/')}/api/v1/tasks/{task_id}/transition",
                headers=self._hyve_headers(
                    project_id=project_id,
                    tenant_id=tenant_id,
                    idempotency_key=idempotency_key,
                ),
                json=payload,
            )
            if response.is_error:
                detail = response.text[:800]
                raise httpx.HTTPStatusError(
                    f"{response.status_code} {response.reason_phrase}: {detail}",
                    request=response.request,
                    response=response,
                )
            return _as_dict(response.json().get("data") or response.json())

    async def add_task_evidence(
        self,
        *,
        project_id: str,
        task_id: str,
        evidence_ref: str,
        evidence_type: str = "aix_objective_cycle",
        evidence_json: dict[str, Any] | None = None,
        tenant_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        assert_workhub_write_allowed(WorkHubOperation.EVIDENCE_ATTACH)
        payload: dict[str, Any] = {
            "project_id": project_id,
            "evidence_ref": evidence_ref,
            "evidence_type": evidence_type,
            "evidence_json": dict(evidence_json or {}),
        }
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(
                f"{self.base_url.rstrip('/')}/api/v1/tasks/{task_id}/evidence",
                headers=self._hyve_headers(
                    project_id=project_id,
                    tenant_id=tenant_id,
                    idempotency_key=idempotency_key,
                ),
                json=payload,
            )
            if response.is_error:
                detail = response.text[:800]
                raise httpx.HTTPStatusError(
                    f"{response.status_code} {response.reason_phrase}: {detail}",
                    request=response.request,
                    response=response,
                )
            return _as_dict(response.json().get("data") or response.json())

    async def evaluate_task_verification(
        self,
        *,
        project_id: str,
        task_id: str,
        decision: str = "pass",
        reasons: list[str] | None = None,
        checks: list[dict[str, Any]] | None = None,
        deviation_score: float = 0.0,
        tenant_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        assert_workhub_write_allowed(WorkHubOperation.VERIFICATION_RECORD)
        payload: dict[str, Any] = {
            "project_id": project_id,
            "decision": decision,
            "deviation_score": deviation_score,
            "reasons": list(reasons or []),
            "checks": list(checks or []),
            "must_fix": [],
        }
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(
                f"{self.base_url.rstrip('/')}/api/v1/tasks/{task_id}/verification/evaluate",
                headers=self._hyve_headers(
                    project_id=project_id,
                    tenant_id=tenant_id,
                    idempotency_key=idempotency_key,
                ),
                json=payload,
            )
            if response.is_error:
                detail = response.text[:800]
                raise httpx.HTTPStatusError(
                    f"{response.status_code} {response.reason_phrase}: {detail}",
                    request=response.request,
                    response=response,
                )
            return _as_dict(response.json().get("data") or response.json())


workhub_task_client = WorkHubTaskClient(
    base_url=str(settings.WORKHUB_API_URL),
    service_token=settings.WORKHUB_API_TOKEN,
    closeout_service_token=settings.WORKHUB_CLOSEOUT_API_TOKEN,
)
