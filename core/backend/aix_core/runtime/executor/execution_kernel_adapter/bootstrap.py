"""EX2-S012: bootstrap a Project → Capability → Problem → Work Item graph."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol
from backend.infrastructure.postgres.execution_kernel.repository import ExecutionKernelRepositoryError
from backend.aix_core.runtime.objective.ulid_compat import new_ulid


class BootstrapService(Protocol):
    async def append_and_project(
        self,
        *,
        tenant_id: str,
        aggregate_type: str,
        aggregate_id: str,
        expected_sequence_number: int,
        event_type: str,
        command_id: str,
        payload: dict[str, Any],
    ) -> Any: ...

    async def add_dependency(
        self, *, tenant_id: str, work_item_id: str, depends_on_work_item_id: str, command_id: str
    ) -> dict[str, Any]: ...

    async def get_projection(
        self, *, tenant_id: str, aggregate_type: str, aggregate_id: str
    ) -> dict[str, Any] | None: ...


@dataclass
class BootstrapWorkItem:
    title: str
    work_item_id: str | None = None
    depends_on: list[str] = field(default_factory=list)  # titles or ids of earlier items


@dataclass
class BootstrapSpec:
    vision_summary: str
    requirements_summary: str
    capability_name: str
    charter_summary: str
    work_items: list[BootstrapWorkItem]
    project_id: str | None = None
    capability_id: str | None = None
    problem_id: str | None = None
    roadmap_position: int = 1


async def bootstrap_problem_graph(
    service: BootstrapService,
    *,
    tenant_id: str,
    spec: BootstrapSpec,
    idempotency_key: str,
) -> dict[str, Any]:
    if not str(idempotency_key or "").strip():
        raise ExecutionKernelRepositoryError("idempotency_key is required")
    if not spec.work_items:
        raise ExecutionKernelRepositoryError("at least one work item is required")
    key = str(idempotency_key).strip()

    project_id = spec.project_id or f"proj_{new_ulid()[:10]}"
    capability_id = spec.capability_id or f"cap_{new_ulid()[:10]}"
    problem_id = spec.problem_id or f"pr_{new_ulid()[:10]}"

    # Fail closed if ids already exist (prevent accidental overwrite).
    for aggregate_type, aggregate_id in (
        ("project", project_id),
        ("capability", capability_id),
        ("problem", problem_id),
    ):
        existing = await service.get_projection(
            tenant_id=tenant_id, aggregate_type=aggregate_type, aggregate_id=aggregate_id
        )
        if existing is not None:
            raise ExecutionKernelRepositoryError(
                f"{aggregate_type} already exists: {aggregate_id}"
            )

    await service.append_and_project(
        tenant_id=tenant_id,
        aggregate_type="project",
        aggregate_id=project_id,
        expected_sequence_number=1,
        event_type="PROJECT_CREATED",
        command_id=f"{key}:project",
        payload={
            "status": "ACTIVE",
            "vision_summary": spec.vision_summary,
            "requirements_summary": spec.requirements_summary,
        },
    )
    await service.append_and_project(
        tenant_id=tenant_id,
        aggregate_type="capability",
        aggregate_id=capability_id,
        expected_sequence_number=1,
        event_type="CAPABILITY_CREATED",
        command_id=f"{key}:capability",
        payload={
            "status": "ACTIVE",
            "name": spec.capability_name,
            "parent_id": project_id,
            "roadmap_position": spec.roadmap_position,
        },
    )
    await service.append_and_project(
        tenant_id=tenant_id,
        aggregate_type="problem",
        aggregate_id=problem_id,
        expected_sequence_number=1,
        event_type="PROBLEM_CREATED",
        command_id=f"{key}:problem",
        payload={
            "status": "EXECUTING",
            "parent_id": capability_id,
            "charter_summary": spec.charter_summary,
        },
    )

    title_to_id: dict[str, str] = {}
    work_item_ids: list[str] = []
    for idx, item in enumerate(spec.work_items):
        wi_id = item.work_item_id or f"wi_{new_ulid()[:10]}"
        if await service.get_projection(
            tenant_id=tenant_id, aggregate_type="work_item", aggregate_id=wi_id
        ):
            raise ExecutionKernelRepositoryError(f"work_item already exists: {wi_id}")
        await service.append_and_project(
            tenant_id=tenant_id,
            aggregate_type="work_item",
            aggregate_id=wi_id,
            expected_sequence_number=1,
            event_type="WI_CREATED",
            command_id=f"{key}:wi:{idx}",
            payload={"status": "PENDING", "parent_id": problem_id, "title": item.title},
        )
        title_to_id[item.title] = wi_id
        title_to_id[wi_id] = wi_id
        work_item_ids.append(wi_id)

    edges: list[dict[str, Any]] = []
    for idx, item in enumerate(spec.work_items):
        wi_id = work_item_ids[idx]
        for dep_ref in item.depends_on:
            dep_id = title_to_id.get(dep_ref) or dep_ref
            if dep_id not in work_item_ids:
                raise ExecutionKernelRepositoryError(f"unknown dependency ref: {dep_ref}")
            edge = await service.add_dependency(
                tenant_id=tenant_id,
                work_item_id=wi_id,
                depends_on_work_item_id=dep_id,
                command_id=f"{key}:dep:{idx}:{dep_id}",
            )
            edges.append(edge)

    return {
        "project_id": project_id,
        "capability_id": capability_id,
        "problem_id": problem_id,
        "work_item_ids": work_item_ids,
        "dependencies": edges,
    }


__all__ = ["BootstrapSpec", "BootstrapWorkItem", "bootstrap_problem_graph"]
