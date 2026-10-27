"""EX2-S002: Dependency Graph (Packet 5 §3) — append-only edges + next actionable."""

from __future__ import annotations

from dataclasses import dataclass, field
from backend.aix_core.runtime.executor.projection import TERMINAL_COMPLETE, InMemoryProjectionStore
from backend.infrastructure.postgres.execution_kernel.repository import ExecutionKernelRepositoryError
from backend.aix_core.runtime.objective.ulid_compat import new_ulid


def _require_tenant(tenant_id: str) -> str:
    normalized = str(tenant_id or "").strip()
    if not normalized:
        raise ExecutionKernelRepositoryError("tenant_id is required (fail closed)")
    return normalized


@dataclass(frozen=True)
class DependencyEdgeEvent:
    event_id: str
    tenant_id: str
    work_item_id: str
    depends_on_work_item_id: str
    event_type: str  # DEPENDENCY_ADDED | DEPENDENCY_REMOVED
    command_id: str


@dataclass
class InMemoryDependencyGraph:
    """EX5-S9: add_dependency/remove_dependency's idempotency check and
    active_dependencies() used to scan the full _events list on every
    call — for a dependency chain, calling active_dependencies once per
    candidate inside next_actionable_work_items made that O(N^2)
    (measured in EX5-S4: ~0.3s at N=1000). _command_index and
    _active_index are maintained incrementally so both are O(1)/O(active
    deps for this item) instead of O(every dependency event ever
    recorded). No behavior change — see
    test_ex5_s9_performance_scale.py for correctness parity proof."""

    _events: list[DependencyEdgeEvent] = field(default_factory=list)
    _command_index: dict[tuple[str, str], DependencyEdgeEvent] = field(default_factory=dict)
    _active_index: dict[tuple[str, str], set[str]] = field(default_factory=dict)

    def add_dependency(
        self,
        *,
        tenant_id: str,
        work_item_id: str,
        depends_on_work_item_id: str,
        command_id: str,
    ) -> DependencyEdgeEvent:
        tenant_id = _require_tenant(tenant_id)
        if not work_item_id.strip() or not depends_on_work_item_id.strip():
            raise ExecutionKernelRepositoryError("work_item ids required")
        if work_item_id == depends_on_work_item_id:
            raise ExecutionKernelRepositoryError("self-dependency forbidden")
        cmd_key = (tenant_id, command_id)
        existing = self._command_index.get(cmd_key)
        if existing is not None:
            return existing
        event = DependencyEdgeEvent(
            event_id=new_ulid(),
            tenant_id=tenant_id,
            work_item_id=work_item_id,
            depends_on_work_item_id=depends_on_work_item_id,
            event_type="DEPENDENCY_ADDED",
            command_id=command_id,
        )
        self._events.append(event)
        self._command_index[cmd_key] = event
        self._active_index.setdefault((tenant_id, work_item_id), set()).add(depends_on_work_item_id)
        return event

    def remove_dependency(
        self,
        *,
        tenant_id: str,
        work_item_id: str,
        depends_on_work_item_id: str,
        command_id: str,
    ) -> DependencyEdgeEvent:
        tenant_id = _require_tenant(tenant_id)
        cmd_key = (tenant_id, command_id)
        existing = self._command_index.get(cmd_key)
        if existing is not None:
            return existing
        event = DependencyEdgeEvent(
            event_id=new_ulid(),
            tenant_id=tenant_id,
            work_item_id=work_item_id,
            depends_on_work_item_id=depends_on_work_item_id,
            event_type="DEPENDENCY_REMOVED",
            command_id=command_id,
        )
        self._events.append(event)
        self._command_index[cmd_key] = event
        self._active_index.get((tenant_id, work_item_id), set()).discard(depends_on_work_item_id)
        return event

    def active_dependencies(self, *, tenant_id: str, work_item_id: str) -> set[str]:
        tenant_id = _require_tenant(tenant_id)
        return set(self._active_index.get((tenant_id, work_item_id), set()))

    def next_actionable_work_items(
        self,
        *,
        tenant_id: str,
        problem_id: str,
        projection: InMemoryProjectionStore,
    ) -> list[str]:
        """Work Items PENDING whose active deps are all VERIFIED/ARCHIVED."""
        tenant_id = _require_tenant(tenant_id)
        children = projection.list_children(
            tenant_id=tenant_id, aggregate_type="work_item", parent_id=problem_id
        )
        actionable: list[str] = []
        for row in children:
            if row.status != "PENDING":
                continue
            deps = self.active_dependencies(tenant_id=tenant_id, work_item_id=row.aggregate_id)
            if not deps:
                actionable.append(row.aggregate_id)
                continue
            ready = True
            for dep_id in deps:
                dep = projection.get(tenant_id=tenant_id, aggregate_type="work_item", aggregate_id=dep_id)
                if dep is None or dep.status not in TERMINAL_COMPLETE:
                    ready = False
                    break
            if ready:
                actionable.append(row.aggregate_id)
        return sorted(actionable)


__all__ = ["DependencyEdgeEvent", "InMemoryDependencyGraph"]
