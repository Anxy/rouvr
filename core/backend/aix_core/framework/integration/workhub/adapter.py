"""AIX Framework WorkHub Read and Write Adapters.

Sprint: F8 WorkHub Framework Boundary (F8-P2)
Grounded in: WORKHUB-AUTHORITY-BOUNDARY.md, WorkHubBoundary, WorkHubTaskClient.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from backend.aix_core.framework.integration.workhub.boundary import (
    WorkHubBoundary,
    WorkHubOperation,
)


@dataclass(frozen=True)
class WorkHubTaskContext:
    """Strongly-typed task context loaded from WorkHub."""

    project_id: str
    task_number: int
    title: str
    description: str
    status: str
    correlation_id: str
    ai_context_markdown: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class TaskContextReader:
    """Canonical reader adapter for loading authoritative task context from WorkHub."""

    def __init__(self, client: Any = None) -> None:
        self._client = client

    async def load_task_context(
        self,
        *,
        project_id: str,
        task_number: int,
        correlation_id: str | None = None,
        tenant_id: str | None = None,
    ) -> WorkHubTaskContext | None:
        decision = WorkHubBoundary.evaluate_operation(WorkHubOperation.TASK_READ)
        if not decision.allowed:
            raise PermissionError(f"Task read denied by WorkHub boundary: {decision.reason}")

        cid = correlation_id or str(uuid4())

        if self._client is None:
            # Fallback / mock context
            return WorkHubTaskContext(
                project_id=project_id,
                task_number=task_number,
                title=f"Task #{task_number}",
                description="Loaded context",
                status="IN_PROGRESS",
                correlation_id=cid,
            )

        task_data = await self._client.get_task_by_number(
            project_id=project_id,
            task_number=task_number,
            tenant_id=tenant_id,
        )
        if not task_data:
            return None

        ai_ctx = None
        if hasattr(self._client, "get_task_ai_context_markdown"):
            try:
                ai_ctx = await self._client.get_task_ai_context_markdown(
                    project_id=project_id,
                    task_number=task_number,
                    tenant_id=tenant_id,
                )
            except Exception:
                ai_ctx = None

        return WorkHubTaskContext(
            project_id=project_id,
            task_number=task_number,
            title=str(task_data.get("title") or f"Task #{task_number}"),
            description=str(task_data.get("description") or ""),
            status=str(task_data.get("status") or "OPEN"),
            correlation_id=cid,
            ai_context_markdown=ai_ctx,
            metadata=task_data,
        )


class TaskEvidenceWriter:
    """Canonical writer adapter for attaching execution evidence to WorkHub tasks."""

    def __init__(self, client: Any = None) -> None:
        self._client = client

    async def attach_evidence(
        self,
        *,
        project_id: str,
        task_number: int,
        evidence_bundle: dict[str, Any],
        correlation_id: str | None = None,
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        decision = WorkHubBoundary.evaluate_operation(WorkHubOperation.EVIDENCE_ATTACH)
        if not decision.allowed:
            raise PermissionError(f"Evidence attach denied by WorkHub boundary: {decision.reason}")

        cid = correlation_id or str(uuid4())

        if self._client is not None and hasattr(self._client, "attach_evidence"):
            return await self._client.attach_evidence(
                project_id=project_id,
                task_number=task_number,
                evidence=evidence_bundle,
                correlation_id=cid,
                tenant_id=tenant_id,
            )

        return {
            "status": "RECORDED",
            "task_number": task_number,
            "correlation_id": cid,
            "evidence_keys": list(evidence_bundle.keys()),
        }


class TaskCloseoutWriter:
    """Governed writer adapter for transitioning task lifecycle upon cycle completion."""

    def __init__(self, client: Any = None) -> None:
        self._client = client

    async def mark_task_done(
        self,
        *,
        project_id: str,
        task_number: int,
        resolution_notes: str,
        mark_done_flag: bool = False,
        correlation_id: str | None = None,
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        decision = WorkHubBoundary.evaluate_operation(
            WorkHubOperation.STATE_TRANSITION,
            mark_done_flag=mark_done_flag,
        )
        if not decision.allowed:
            raise PermissionError(
                f"WorkHub lifecycle state transition denied: {decision.reason} "
                f"(requires {decision.requires_flag}=True)"
            )

        cid = correlation_id or str(uuid4())

        if self._client is not None and hasattr(self._client, "mark_task_done"):
            return await self._client.mark_task_done(
                project_id=project_id,
                task_number=task_number,
                resolution_notes=resolution_notes,
                correlation_id=cid,
                tenant_id=tenant_id,
            )

        return {
            "status": "DONE",
            "task_number": task_number,
            "correlation_id": cid,
            "notes": resolution_notes,
        }
