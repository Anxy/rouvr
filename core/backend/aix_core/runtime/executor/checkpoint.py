"""EX2-S003: Checkpoint Store + Resume Engine (Packet 5 §5 / Packet 7 §8)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from backend.aix_core.runtime.executor.dependency_graph import InMemoryDependencyGraph
from backend.aix_core.runtime.executor.projection import InMemoryProjectionStore, ProjectionRow
from backend.infrastructure.postgres.execution_kernel.repository import ExecutionKernelRepositoryError
from backend.aix_core.runtime.objective.ulid_compat import new_ulid


def _require_tenant(tenant_id: str) -> str:
    normalized = str(tenant_id or "").strip()
    if not normalized:
        raise ExecutionKernelRepositoryError("tenant_id is required (fail closed)")
    return normalized


REPOSITORY_STATE_FIELDS = (
    "repository_id",
    "branch",
    "base_commit_sha",
    "current_commit_sha",
    "working_tree_diff_reference",
    "untracked_artifact_references",
    "environment_manifest_reference",
)


@dataclass(frozen=True)
class Checkpoint:
    checkpoint_id: str
    tenant_id: str
    problem_id: str
    status: str  # CAPTURING | CAPTURED | SUPERSEDED | FAILED
    work_items: list[dict[str, Any]]
    open_proposals: list[dict[str, Any]]
    evidence_refs: list[str]
    decision_refs: list[str]
    repository_state: dict[str, Any]
    created_at: str
    superseded_by: str | None = None


@dataclass
class InMemoryCheckpointStore:
    _checkpoints: list[Checkpoint] = field(default_factory=list)

    def capture(
        self,
        *,
        tenant_id: str,
        problem_id: str,
        projection: InMemoryProjectionStore,
        repository_state: dict[str, Any],
        open_proposals: list[dict[str, Any]] | None = None,
        evidence_refs: list[str] | None = None,
        decision_refs: list[str] | None = None,
    ) -> Checkpoint:
        tenant_id = _require_tenant(tenant_id)
        missing = [f for f in REPOSITORY_STATE_FIELDS if f not in repository_state]
        if missing:
            raise ExecutionKernelRepositoryError(f"repository_state missing fields: {missing}")

        # Supersede previous CAPTURED for this problem.
        updated: list[Checkpoint] = []
        for cp in self._checkpoints:
            if (
                cp.tenant_id == tenant_id
                and cp.problem_id == problem_id
                and cp.status == "CAPTURED"
            ):
                updated.append(
                    Checkpoint(
                        checkpoint_id=cp.checkpoint_id,
                        tenant_id=cp.tenant_id,
                        problem_id=cp.problem_id,
                        status="SUPERSEDED",
                        work_items=cp.work_items,
                        open_proposals=cp.open_proposals,
                        evidence_refs=cp.evidence_refs,
                        decision_refs=cp.decision_refs,
                        repository_state=cp.repository_state,
                        created_at=cp.created_at,
                        superseded_by=None,  # filled after new id known
                    )
                )
            else:
                updated.append(cp)

        work_items = [
            {
                "work_item_id": row.aggregate_id,
                "status": row.status,
                "sequence_number": row.sequence_number,
            }
            for row in projection.list_children(
                tenant_id=tenant_id, aggregate_type="work_item", parent_id=problem_id
            )
        ]
        checkpoint_id = new_ulid()
        # Point superseded_by on previous rows.
        final: list[Checkpoint] = []
        for cp in updated:
            if (
                cp.tenant_id == tenant_id
                and cp.problem_id == problem_id
                and cp.status == "SUPERSEDED"
                and cp.superseded_by is None
            ):
                final.append(
                    Checkpoint(
                        checkpoint_id=cp.checkpoint_id,
                        tenant_id=cp.tenant_id,
                        problem_id=cp.problem_id,
                        status=cp.status,
                        work_items=cp.work_items,
                        open_proposals=cp.open_proposals,
                        evidence_refs=cp.evidence_refs,
                        decision_refs=cp.decision_refs,
                        repository_state=cp.repository_state,
                        created_at=cp.created_at,
                        superseded_by=checkpoint_id,
                    )
                )
            else:
                final.append(cp)

        captured = Checkpoint(
            checkpoint_id=checkpoint_id,
            tenant_id=tenant_id,
            problem_id=problem_id,
            status="CAPTURED",
            work_items=work_items,
            open_proposals=list(open_proposals or []),
            evidence_refs=list(evidence_refs or []),
            decision_refs=list(decision_refs or []),
            repository_state={k: repository_state[k] for k in REPOSITORY_STATE_FIELDS},
            created_at=datetime.now(timezone.utc).isoformat(),
            superseded_by=None,
        )
        final.append(captured)
        self._checkpoints = final
        return captured

    def current(self, *, tenant_id: str, problem_id: str) -> Checkpoint | None:
        tenant_id = _require_tenant(tenant_id)
        for cp in reversed(self._checkpoints):
            if cp.tenant_id == tenant_id and cp.problem_id == problem_id and cp.status == "CAPTURED":
                return cp
        return None


@dataclass(frozen=True)
class ResumeResult:
    problem_id: str
    checkpoint_id: str | None
    work_items: list[dict[str, Any]]
    next_actionable: list[str]
    projection_watermark: int
    source: str  # checkpoint | projection_only


class ResumeEngine:
    def __init__(
        self,
        *,
        projection: InMemoryProjectionStore,
        dependencies: InMemoryDependencyGraph,
        checkpoints: InMemoryCheckpointStore,
    ) -> None:
        self._projection = projection
        self._dependencies = dependencies
        self._checkpoints = checkpoints

    def resume(self, *, tenant_id: str, problem_id: str) -> ResumeResult:
        tenant_id = _require_tenant(tenant_id)
        checkpoint = self._checkpoints.current(tenant_id=tenant_id, problem_id=problem_id)
        if checkpoint is not None:
            # Rehydrate projection statuses from checkpoint if missing.
            for item in checkpoint.work_items:
                existing = self._projection.get(
                    tenant_id=tenant_id, aggregate_type="work_item", aggregate_id=item["work_item_id"]
                )
                if existing is None:
                    self._projection._rows[(tenant_id, "work_item", item["work_item_id"])] = ProjectionRow(  # noqa: SLF001
                        tenant_id=tenant_id,
                        aggregate_type="work_item",
                        aggregate_id=item["work_item_id"],
                        sequence_number=int(item.get("sequence_number") or 0),
                        status=str(item.get("status") or "PENDING"),
                        payload={"status": item.get("status"), "parent_id": problem_id},
                        parent_id=problem_id,
                    )
            next_ids = self._dependencies.next_actionable_work_items(
                tenant_id=tenant_id, problem_id=problem_id, projection=self._projection
            )
            return ResumeResult(
                problem_id=problem_id,
                checkpoint_id=checkpoint.checkpoint_id,
                work_items=list(checkpoint.work_items),
                next_actionable=next_ids,
                projection_watermark=self._projection.watermark(tenant_id=tenant_id),
                source="checkpoint",
            )

        children = self._projection.list_children(
            tenant_id=tenant_id, aggregate_type="work_item", parent_id=problem_id
        )
        work_items = [
            {"work_item_id": r.aggregate_id, "status": r.status, "sequence_number": r.sequence_number}
            for r in children
        ]
        next_ids = self._dependencies.next_actionable_work_items(
            tenant_id=tenant_id, problem_id=problem_id, projection=self._projection
        )
        return ResumeResult(
            problem_id=problem_id,
            checkpoint_id=None,
            work_items=work_items,
            next_actionable=next_ids,
            projection_watermark=self._projection.watermark(tenant_id=tenant_id),
            source="projection_only",
        )


__all__ = [
    "REPOSITORY_STATE_FIELDS",
    "Checkpoint",
    "InMemoryCheckpointStore",
    "ResumeResult",
    "ResumeEngine",
]
