"""EX4-R4-P12-S001: Enterprise Intelligence Platform — operating product surface.

Composes mature spine programmes into portfolio views, governed improvement
cycles, and long-horizon resume scenarios. Consumes Hyeve tenant identity
(fail closed). Does not claim an "AI CEO", bypass Hyeve controls, or silently
promote organisation-wide standards.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from backend.aix_core.runtime.executor.checkpoint import InMemoryCheckpointStore, ResumeEngine
from backend.aix_core.runtime.executor.context_builder import InMemoryKnowledgeStore
from backend.aix_core.runtime.executor.knowledge_graph import InMemoryKnowledgeGraph, edge_to_dict
from backend.aix_core.runtime.objective.ulid_compat import new_ulid
from backend.aix_core.runtime.executor.learning_lifecycle import (
    InMemoryLearningLifecycleStore,
    learning_record_to_dict,
)
from backend.aix_core.runtime.executor.organizational_knowledge import (
    reconcile_cross_project_knowledge,
)
from backend.aix_core.runtime.executor.organizational_reasoning import reason_organizationally
from backend.aix_core.runtime.executor.projection import InMemoryProjectionStore, ProjectionRow
from backend.infrastructure.postgres.execution_kernel.repository import ExecutionKernelRepositoryError

ENTERPRISE_SCENARIOS = (
    {
        "id": "E1",
        "name": "multi_project_objective_coordination",
        "description": "Portfolio view across projects with conflicts and shared knowledge",
    },
    {
        "id": "E2",
        "name": "governed_org_pattern_adoption",
        "description": "Improvement cycle — org-scoped learning never auto-promotes",
    },
    {
        "id": "E3",
        "name": "long_horizon_strategic_resume",
        "description": "Resume a strategic programme from checkpoint under tenant scope",
    },
)


def _require_tenant(tenant_id: str) -> str:
    normalized = str(tenant_id or "").strip()
    if not normalized:
        raise ExecutionKernelRepositoryError("tenant_id is required (fail closed)")
    return normalized


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def enterprise_scenarios() -> list[dict[str, str]]:
    return [dict(item) for item in ENTERPRISE_SCENARIOS]


def _list_aggregates(
    projection: InMemoryProjectionStore, *, tenant_id: str, aggregate_type: str
) -> list[ProjectionRow]:
    tenant_id = _require_tenant(tenant_id)
    return [
        row
        for (t, at, _), row in projection._rows.items()  # noqa: SLF001
        if t == tenant_id and at == aggregate_type and row.state_current
    ]


def build_portfolio_intelligence_view(
    projection: InMemoryProjectionStore,
    knowledge: InMemoryKnowledgeStore,
    graph: InMemoryKnowledgeGraph,
    *,
    tenant_id: str,
) -> dict[str, Any]:
    """Portfolio intelligence — multi-project coordination snapshot (read-only)."""
    tenant_id = _require_tenant(tenant_id)
    projects = [
        {
            "project_id": row.aggregate_id,
            "status": row.status,
            "vision_summary": (row.payload or {}).get("vision_summary"),
        }
        for row in sorted(_list_aggregates(projection, tenant_id=tenant_id, aggregate_type="project"), key=lambda r: r.aggregate_id)
    ]
    capabilities = [
        {
            "capability_id": row.aggregate_id,
            "project_id": row.parent_id,
            "name": (row.payload or {}).get("name"),
            "status": row.status,
        }
        for row in sorted(
            _list_aggregates(projection, tenant_id=tenant_id, aggregate_type="capability"),
            key=lambda r: r.aggregate_id,
        )
    ]
    problems = [
        {
            "problem_id": row.aggregate_id,
            "capability_id": row.parent_id,
            "status": row.status,
            "charter_summary": (row.payload or {}).get("charter_summary"),
        }
        for row in sorted(
            _list_aggregates(projection, tenant_id=tenant_id, aggregate_type="problem"),
            key=lambda r: r.aggregate_id,
        )
    ]
    conflicts = [
        edge_to_dict(edge)
        for edge in graph.list_edges(
            tenant_id=tenant_id, edge_type="CONFLICTS_WITH", active_only=True
        )
    ]
    shared = reconcile_cross_project_knowledge(knowledge, tenant_id=tenant_id)
    return {
        "tenant_id": tenant_id,
        "view": "portfolio_intelligence",
        "projects": projects,
        "capabilities": capabilities,
        "problems": problems,
        "active_conflicts": conflicts,
        "cross_project_knowledge_groups": shared,
        "metrics": {
            "project_count": len(projects),
            "capability_count": len(capabilities),
            "problem_count": len(problems),
            "active_conflict_count": len(conflicts),
            "shared_knowledge_group_count": len(shared),
        },
        "ai_ceo_claim": False,
        "bypasses_hyeve_controls": False,
        "silent_org_promotion": False,
    }


@dataclass
class InMemoryEnterpriseAuditLog:
    """Append-only audit trail for enterprise scenarios / improvement cycles."""

    _events: list[dict[str, Any]] = field(default_factory=list)

    def append(self, *, tenant_id: str, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        tenant_id = _require_tenant(tenant_id)
        event = {
            "event_id": f"eia_{new_ulid()[:12]}",
            "tenant_id": tenant_id,
            "event_type": event_type,
            "payload": payload,
            "recorded_at": _utc_now(),
        }
        self._events.append(event)
        return event

    def list(self, *, tenant_id: str) -> list[dict[str, Any]]:
        tenant_id = _require_tenant(tenant_id)
        return [e for e in self._events if e["tenant_id"] == tenant_id]


def start_governed_improvement_cycle(
    learning: InMemoryLearningLifecycleStore,
    audit: InMemoryEnterpriseAuditLog,
    *,
    tenant_id: str,
    summary: str,
    scope: str = "organisation",
    capability_id: str | None = None,
    actor_id: str = "enterprise_operator",
) -> dict[str, Any]:
    """Organisation-wide pattern adoption under Learning lifecycle — never auto-promotes."""
    tenant_id = _require_tenant(tenant_id)
    if scope == "organisation":
        # Explicit: org-wide learning cannot silently become Active Knowledge.
        auto_promote_blocked = True
    else:
        auto_promote_blocked = False

    rec = learning.observe(
        tenant_id=tenant_id,
        summary=summary,
        capability_id=capability_id,
    )
    # Advance to AWAITING_APPROVAL with organisation scope floor (level 4).
    for stage in (
        "CANDIDATE_LESSON",
        "VALIDATION",
        "SCOPE_CLASSIFICATION",
        "CONFIDENCE",
        "AWAITING_APPROVAL",
    ):
        rec = learning.advance(tenant_id=tenant_id, learning_id=rec.learning_id, to_stage=stage)
    rec = learning.classify_scope(tenant_id=tenant_id, learning_id=rec.learning_id, scope=scope)
    rec = learning.set_confidence(tenant_id=tenant_id, learning_id=rec.learning_id, confidence="medium")

    # Prove org scope cannot auto-activate without Proposal level 4.
    blocked_activation = None
    if scope == "organisation":
        try:
            learning.approve_activation(
                tenant_id=tenant_id,
                learning_id=rec.learning_id,
                actor_id=actor_id,
                proposal_level=2,
            )
        except ExecutionKernelRepositoryError as exc:
            blocked_activation = str(exc)

    audit_event = audit.append(
        tenant_id=tenant_id,
        event_type="IMPROVEMENT_CYCLE_STARTED",
        payload={
            "learning_id": rec.learning_id,
            "scope": scope,
            "required_proposal_level": rec.required_proposal_level,
            "auto_promote_blocked": auto_promote_blocked,
            "blocked_activation": blocked_activation,
            "actor_id": actor_id,
        },
    )
    return {
        "cycle": "governed_improvement",
        "learning": learning_record_to_dict(rec),
        "auto_promote_blocked": auto_promote_blocked,
        "silent_org_promotion": False,
        "blocked_low_level_activation": blocked_activation is not None,
        "audit_event": audit_event,
    }


async def start_governed_improvement_cycle_async(
    learning: Any,
    audit: Any,
    *,
    tenant_id: str,
    summary: str,
    scope: str = "organisation",
    capability_id: str | None = None,
    actor_id: str = "enterprise_operator",
) -> dict[str, Any]:
    """Async parity for durable learning + enterprise audit stores (EX5-S1)."""
    tenant_id = _require_tenant(tenant_id)
    auto_promote_blocked = scope == "organisation"

    rec = await learning.observe(
        tenant_id=tenant_id,
        summary=summary,
        capability_id=capability_id,
    )
    for stage in (
        "CANDIDATE_LESSON",
        "VALIDATION",
        "SCOPE_CLASSIFICATION",
        "CONFIDENCE",
        "AWAITING_APPROVAL",
    ):
        rec = await learning.advance(
            tenant_id=tenant_id, learning_id=rec.learning_id, to_stage=stage
        )
    rec = await learning.classify_scope(
        tenant_id=tenant_id, learning_id=rec.learning_id, scope=scope
    )
    rec = await learning.set_confidence(
        tenant_id=tenant_id, learning_id=rec.learning_id, confidence="medium"
    )

    blocked_activation = None
    if scope == "organisation":
        try:
            await learning.approve_activation(
                tenant_id=tenant_id,
                learning_id=rec.learning_id,
                actor_id=actor_id,
                proposal_level=2,
            )
        except ExecutionKernelRepositoryError as exc:
            blocked_activation = str(exc)

    audit_event = await audit.append(
        tenant_id=tenant_id,
        event_type="IMPROVEMENT_CYCLE_STARTED",
        payload={
            "learning_id": rec.learning_id,
            "scope": scope,
            "required_proposal_level": rec.required_proposal_level,
            "auto_promote_blocked": auto_promote_blocked,
            "blocked_activation": blocked_activation,
            "actor_id": actor_id,
        },
    )
    return {
        "cycle": "governed_improvement",
        "learning": learning_record_to_dict(rec),
        "auto_promote_blocked": auto_promote_blocked,
        "silent_org_promotion": False,
        "blocked_low_level_activation": blocked_activation is not None,
        "audit_event": audit_event,
    }


def strategic_programme_resume(
    resume_engine: ResumeEngine,
    audit: InMemoryEnterpriseAuditLog,
    knowledge: InMemoryKnowledgeStore,
    graph: InMemoryKnowledgeGraph,
    *,
    tenant_id: str,
    problem_id: str,
    capability_id: str | None = None,
) -> dict[str, Any]:
    """Long-horizon resume of a strategic programme — checkpoint + pre-planning brief."""
    tenant_id = _require_tenant(tenant_id)
    resume = resume_engine.resume(tenant_id=tenant_id, problem_id=problem_id)
    brief = None
    if capability_id:
        brief = reason_organizationally(
            knowledge,
            graph,
            tenant_id=tenant_id,
            mode="pre_planning_brief",
            capability_id=capability_id,
            question="Strategic resume: what do we know?",
        )
    audit_event = audit.append(
        tenant_id=tenant_id,
        event_type="STRATEGIC_RESUME",
        payload={
            "problem_id": problem_id,
            "checkpoint_id": resume.checkpoint_id,
            "next_actionable": list(resume.next_actionable),
            "capability_id": capability_id,
        },
    )
    return {
        "scenario": "long_horizon_strategic_resume",
        "resume": {
            "problem_id": resume.problem_id,
            "checkpoint_id": resume.checkpoint_id,
            "work_items": resume.work_items,
            "next_actionable": resume.next_actionable,
            "projection_watermark": resume.projection_watermark,
            "source": resume.source,
        },
        "pre_planning_brief": brief,
        "state_mutations_from_platform": [],
        "audit_event": audit_event,
        "ai_ceo_claim": False,
    }


__all__ = [
    "ENTERPRISE_SCENARIOS",
    "InMemoryEnterpriseAuditLog",
    "enterprise_scenarios",
    "build_portfolio_intelligence_view",
    "start_governed_improvement_cycle",
    "start_governed_improvement_cycle_async",
    "strategic_programme_resume",
]
