"""EX4-R3-P8-S001: Continuous Learning — governed promotion lifecycle.

Observation → … → Active Knowledge → Monitored Use → Revalidation/Supersession
No silent organisation-wide promotion. Planner heuristic auto-updates require
explicit governance ladder clearance (not enabled by default).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Literal
from backend.infrastructure.postgres.execution_kernel.repository import ExecutionKernelRepositoryError
from backend.aix_core.runtime.objective.ulid_compat import new_ulid

LearningStage = Literal[
    "OBSERVATION",
    "CANDIDATE_LESSON",
    "VALIDATION",
    "SCOPE_CLASSIFICATION",
    "CONFIDENCE",
    "AWAITING_APPROVAL",
    "ACTIVE_KNOWLEDGE",
    "MONITORED_USE",
    "REVALIDATION",
    "SUPERSEDED",
    "REJECTED",
]

LearningScope = Literal["local", "capability", "organisation"]
InfluenceLevel = Literal[
    "retrieved_for_consideration",
    "recommendation_to_planner",
    "automatic_heuristic_update",
    "policy_modified",
    "autonomous_planning_behaviour_changed",
]

STAGES = frozenset(
    {
        "OBSERVATION",
        "CANDIDATE_LESSON",
        "VALIDATION",
        "SCOPE_CLASSIFICATION",
        "CONFIDENCE",
        "AWAITING_APPROVAL",
        "ACTIVE_KNOWLEDGE",
        "MONITORED_USE",
        "REVALIDATION",
        "SUPERSEDED",
        "REJECTED",
    }
)
SCOPES = frozenset({"local", "capability", "organisation"})
INFLUENCE_LEVELS = frozenset(
    {
        "retrieved_for_consideration",
        "recommendation_to_planner",
        "automatic_heuristic_update",
        "policy_modified",
        "autonomous_planning_behaviour_changed",
    }
)

# Minimum Proposal level required to approve activation at a given scope.
# Organisation-wide never auto-approves.
PROPOSAL_LEVEL_FOR_SCOPE: dict[str, int] = {
    "local": 1,
    "capability": 2,
    "organisation": 4,
}

# Influence levels that may NOT be applied automatically in this packet.
REQUIRES_EXPLICIT_POLICY = frozenset(
    {
        "automatic_heuristic_update",
        "policy_modified",
        "autonomous_planning_behaviour_changed",
    }
)

_FORWARD: dict[str, tuple[str, ...]] = {
    "OBSERVATION": ("CANDIDATE_LESSON", "REJECTED"),
    "CANDIDATE_LESSON": ("VALIDATION", "REJECTED"),
    "VALIDATION": ("SCOPE_CLASSIFICATION", "REJECTED"),
    "SCOPE_CLASSIFICATION": ("CONFIDENCE", "REJECTED"),
    "CONFIDENCE": ("AWAITING_APPROVAL", "REJECTED"),
    "AWAITING_APPROVAL": ("ACTIVE_KNOWLEDGE", "REJECTED"),
    "ACTIVE_KNOWLEDGE": ("MONITORED_USE", "SUPERSEDED", "REJECTED"),
    "MONITORED_USE": ("REVALIDATION", "SUPERSEDED"),
    "REVALIDATION": ("ACTIVE_KNOWLEDGE", "SUPERSEDED", "REJECTED"),
}


def _require_tenant(tenant_id: str) -> str:
    normalized = str(tenant_id or "").strip()
    if not normalized:
        raise ExecutionKernelRepositoryError("tenant_id is required (fail closed)")
    return normalized


@dataclass(frozen=True)
class LearningRecord:
    learning_id: str
    tenant_id: str
    stage: str
    summary: str
    scope: str = "local"
    confidence: str = "unknown"  # unknown|low|medium|high
    influence_level: str = "retrieved_for_consideration"
    source_work_item_id: str | None = None
    source_knowledge_ids: tuple[str, ...] = ()
    capability_id: str | None = None
    problem_id: str | None = None
    proposal_id: str | None = None
    required_proposal_level: int = 1
    approved_by_actor: str | None = None
    active_knowledge_entry_id: str | None = None
    auto_promote_allowed: bool = False  # organisation never; others only if explicitly set


@dataclass
class InMemoryLearningLifecycleStore:
    _records: dict[tuple[str, str], LearningRecord] = field(default_factory=dict)

    def observe(
        self,
        *,
        tenant_id: str,
        summary: str,
        source_work_item_id: str | None = None,
        source_knowledge_ids: list[str] | tuple[str, ...] | None = None,
        capability_id: str | None = None,
        problem_id: str | None = None,
        learning_id: str | None = None,
    ) -> LearningRecord:
        tenant_id = _require_tenant(tenant_id)
        summary = str(summary or "").strip()
        if not summary:
            raise ExecutionKernelRepositoryError("summary is required")
        rec = LearningRecord(
            learning_id=str(learning_id or f"lrn_{new_ulid()[:12]}"),
            tenant_id=tenant_id,
            stage="OBSERVATION",
            summary=summary,
            source_work_item_id=source_work_item_id,
            source_knowledge_ids=tuple(source_knowledge_ids or ()),
            capability_id=capability_id,
            problem_id=problem_id,
            required_proposal_level=PROPOSAL_LEVEL_FOR_SCOPE["local"],
        )
        self._records[(tenant_id, rec.learning_id)] = rec
        return rec

    def get(self, *, tenant_id: str, learning_id: str) -> LearningRecord | None:
        tenant_id = _require_tenant(tenant_id)
        return self._records.get((tenant_id, learning_id))

    def list(
        self, *, tenant_id: str, stage: str | None = None, active_only: bool = False
    ) -> list[LearningRecord]:
        tenant_id = _require_tenant(tenant_id)
        out: list[LearningRecord] = []
        for (t, _), rec in self._records.items():
            if t != tenant_id:
                continue
            if stage and rec.stage != stage:
                continue
            if active_only and rec.stage not in {"ACTIVE_KNOWLEDGE", "MONITORED_USE", "REVALIDATION"}:
                continue
            out.append(rec)
        return sorted(out, key=lambda r: r.learning_id)

    def classify_scope(
        self, *, tenant_id: str, learning_id: str, scope: str
    ) -> LearningRecord:
        rec = self._require(tenant_id, learning_id)
        if scope not in SCOPES:
            raise ExecutionKernelRepositoryError(f"invalid scope={scope!r}")
        if rec.stage not in {"SCOPE_CLASSIFICATION", "VALIDATION", "CANDIDATE_LESSON", "CONFIDENCE"}:
            # Allow setting scope when arriving at/before SCOPE_CLASSIFICATION
            if rec.stage not in {"OBSERVATION"} and rec.stage not in _FORWARD:
                raise ExecutionKernelRepositoryError(f"cannot classify scope in stage={rec.stage}")
        level = PROPOSAL_LEVEL_FOR_SCOPE[scope]
        updated = replace(
            rec,
            scope=scope,
            required_proposal_level=level,
            # Organisation never gets silent auto-promote
            auto_promote_allowed=False if scope == "organisation" else rec.auto_promote_allowed,
        )
        self._records[(tenant_id, learning_id)] = updated
        return updated

    def set_confidence(
        self, *, tenant_id: str, learning_id: str, confidence: str
    ) -> LearningRecord:
        if confidence not in {"unknown", "low", "medium", "high"}:
            raise ExecutionKernelRepositoryError(f"invalid confidence={confidence!r}")
        rec = self._require(tenant_id, learning_id)
        updated = replace(rec, confidence=confidence)
        self._records[(tenant_id, learning_id)] = updated
        return updated

    def set_influence_level(
        self, *, tenant_id: str, learning_id: str, influence_level: str
    ) -> LearningRecord:
        if influence_level not in INFLUENCE_LEVELS:
            raise ExecutionKernelRepositoryError(f"invalid influence_level={influence_level!r}")
        rec = self._require(tenant_id, learning_id)
        if influence_level in REQUIRES_EXPLICIT_POLICY and not rec.auto_promote_allowed:
            raise ExecutionKernelRepositoryError(
                "influence_level requires explicit auto_promote policy (not enabled by default)"
            )
        if rec.scope == "organisation" and influence_level in REQUIRES_EXPLICIT_POLICY:
            raise ExecutionKernelRepositoryError(
                "organisation scope cannot use automatic influence levels"
            )
        updated = replace(rec, influence_level=influence_level)
        self._records[(tenant_id, learning_id)] = updated
        return updated

    def advance(self, *, tenant_id: str, learning_id: str, to_stage: str) -> LearningRecord:
        rec = self._require(tenant_id, learning_id)
        if to_stage not in STAGES:
            raise ExecutionKernelRepositoryError(f"invalid stage={to_stage!r}")
        allowed = _FORWARD.get(rec.stage, ())
        if to_stage not in allowed:
            raise ExecutionKernelRepositoryError(
                f"illegal transition {rec.stage} -> {to_stage}"
            )
        updated = replace(rec, stage=to_stage)
        self._records[(tenant_id, learning_id)] = updated
        return updated

    def approve_activation(
        self,
        *,
        tenant_id: str,
        learning_id: str,
        actor_id: str,
        proposal_level: int,
        proposal_id: str | None = None,
        active_knowledge_entry_id: str | None = None,
    ) -> LearningRecord:
        """Move AWAITING_APPROVAL → ACTIVE_KNOWLEDGE when proposal level is sufficient."""
        rec = self._require(tenant_id, learning_id)
        if rec.stage != "AWAITING_APPROVAL":
            raise ExecutionKernelRepositoryError(
                f"approve requires AWAITING_APPROVAL, got {rec.stage}"
            )
        if proposal_level < rec.required_proposal_level:
            raise ExecutionKernelRepositoryError(
                f"proposal_level {proposal_level} < required {rec.required_proposal_level} for scope={rec.scope}"
            )
        if rec.scope == "organisation" and proposal_level < 4:
            raise ExecutionKernelRepositoryError(
                "organisation-scoped learning requires Proposal level 4"
            )
        actor_id = str(actor_id or "").strip()
        if not actor_id:
            raise ExecutionKernelRepositoryError("actor_id is required for approval")
        updated = replace(
            rec,
            stage="ACTIVE_KNOWLEDGE",
            approved_by_actor=actor_id,
            proposal_id=proposal_id,
            active_knowledge_entry_id=active_knowledge_entry_id,
            influence_level="recommendation_to_planner"
            if rec.influence_level == "retrieved_for_consideration"
            else rec.influence_level,
        )
        self._records[(tenant_id, learning_id)] = updated
        return updated

    def reject(self, *, tenant_id: str, learning_id: str) -> LearningRecord:
        rec = self._require(tenant_id, learning_id)
        if rec.stage in {"SUPERSEDED", "REJECTED"}:
            return rec
        updated = replace(rec, stage="REJECTED")
        self._records[(tenant_id, learning_id)] = updated
        return updated

    def _require(self, tenant_id: str, learning_id: str) -> LearningRecord:
        tenant_id = _require_tenant(tenant_id)
        rec = self._records.get((tenant_id, learning_id))
        if rec is None:
            raise ExecutionKernelRepositoryError(f"learning record not found: {learning_id}")
        return rec


def learning_record_to_dict(rec: LearningRecord) -> dict[str, Any]:
    return {
        "learning_id": rec.learning_id,
        "tenant_id": rec.tenant_id,
        "stage": rec.stage,
        "summary": rec.summary,
        "scope": rec.scope,
        "confidence": rec.confidence,
        "influence_level": rec.influence_level,
        "source_work_item_id": rec.source_work_item_id,
        "source_knowledge_ids": list(rec.source_knowledge_ids),
        "capability_id": rec.capability_id,
        "problem_id": rec.problem_id,
        "proposal_id": rec.proposal_id,
        "required_proposal_level": rec.required_proposal_level,
        "approved_by_actor": rec.approved_by_actor,
        "active_knowledge_entry_id": rec.active_knowledge_entry_id,
        "auto_promote_allowed": rec.auto_promote_allowed,
    }


__all__ = [
    "STAGES",
    "SCOPES",
    "INFLUENCE_LEVELS",
    "PROPOSAL_LEVEL_FOR_SCOPE",
    "REQUIRES_EXPLICIT_POLICY",
    "LearningRecord",
    "InMemoryLearningLifecycleStore",
    "learning_record_to_dict",
]
