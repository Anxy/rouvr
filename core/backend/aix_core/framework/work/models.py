"""F1-P1: Framework Work domain schemas (additive leaf layer).

Grounded in runtime `ObjectiveEnvelopeV1` / `StepContractV1` / `PlanRevisionV1`.
Runtime contracts are never imported here — adapters own that boundary.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _uuid_str() -> str:
    return str(uuid.uuid4())


class WorkStatus(str, Enum):
    DRAFT = "DRAFT"
    ADMITTED = "ADMITTED"
    PLANNING = "PLANNING"
    EXECUTING = "EXECUTING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class UnitStatus(str, Enum):
    PENDING = "PENDING"
    READY = "READY"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class ExternalWorkRef(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    system: Literal["workhub", "linear", "jira", "local"] = "workhub"
    external_task_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    correlation_id: str = Field(default_factory=_uuid_str)
    read_contract_endpoint: str | None = None


class WorkType(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    task_class: str = Field(min_length=1)
    task_family: str = Field(default="general")
    product_class: str = Field(default="software")
    allowed_effects: tuple[str, ...] = ()
    forbidden_effects: tuple[str, ...] = ()
    environment_scope: Literal["development", "staging", "production"] = "development"
    approval_policy: str = "INITIAL_PLAN_ONLY"

    @model_validator(mode="after")
    def _validate_effects_disjoint(self) -> "WorkType":
        overlap = set(self.allowed_effects) & set(self.forbidden_effects)
        if overlap:
            raise ValueError(
                f"allowed_effects may not overlap forbidden_effects: {sorted(overlap)}"
            )
        return self


class WorkDefinition(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    definition_id: str = Field(default_factory=_uuid_str)
    work_id: str = Field(min_length=1)
    definition_of_done: str = Field(min_length=1)
    acceptance_criteria: tuple[str, ...] = Field(min_length=1)
    obligations: tuple[str, ...] = ()
    target_scope: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    # Semantic / PD-003B2 refs preserved for adapter parity (not in mermaid sketch).
    facet_refs: tuple[str, ...] = ()
    operation_refs: tuple[str, ...] = ()
    entity_refs: tuple[str, ...] = ()
    mapping_refs: tuple[str, ...] = ()
    definition_hash: str = ""
    source_contract_hash: str | None = None

    def compute_hash(self) -> str:
        data = self.model_dump(mode="json", exclude={"definition_hash"})
        canonical = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class WorkContract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    contract_id: str = Field(default_factory=_uuid_str)
    unit_id: str = Field(min_length=1)
    required_effects: tuple[dict[str, Any], ...] = ()
    obligation_refs: tuple[str, ...] = ()
    validation_commands: tuple[str, ...] = ()
    expected_checks: tuple[str, ...] = ()
    assigned_role: str = "executor"
    risk_tier: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL", "PROHIBITED"] = "LOW"
    approval_required: bool = False
    timeout_seconds: int = 300
    token_budget: int = 30000
    # PD-003B2 / OutcomeContract binding refs (preserved through adapters).
    outcome_contract_id: str | None = None
    outcome_contract_hash: str | None = None
    facet_refs: tuple[str, ...] = ()
    operation_refs: tuple[str, ...] = ()
    entity_refs: tuple[str, ...] = ()
    mapping_refs: tuple[str, ...] = ()
    binding_role: str | None = None


class WorkUnit(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    unit_id: str = Field(default_factory=_uuid_str)
    work_id: str = Field(min_length=1)
    unit_index: int = Field(ge=0)
    purpose: str = Field(min_length=1)
    depends_on: tuple[str, ...] = ()
    target_scope: tuple[str, ...] = Field(min_length=1)
    contract: WorkContract
    actions: tuple[dict[str, Any], ...] = ()
    status: UnitStatus = UnitStatus.PENDING
    created_at: str = Field(default_factory=_utc_now_iso)
    # F10-P2 canonical metadata (filled by GraphStage / AssignStage enrichment).
    required_capabilities: tuple[str, ...] = ()
    required_agent_role: str | None = None
    verification_requirements: tuple[str, ...] = ()
    recovery_metadata: dict[str, Any] = Field(default_factory=dict)
    context_refs: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _validate_no_self_dep(self) -> "WorkUnit":
        if self.unit_id in self.depends_on:
            raise ValueError(f"WorkUnit {self.unit_id} cannot depend on itself")
        return self


class WorkGraph(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    graph_id: str = Field(default_factory=_uuid_str)
    work_id: str = Field(min_length=1)
    revision: int = Field(ge=1, default=1)
    units: tuple[WorkUnit, ...] = Field(min_length=1)
    created_from_revision: str | None = None
    created_at: str = Field(default_factory=_utc_now_iso)
    # Deterministic execution order (unit_ids) when computed.
    execution_order: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _validate_acyclic(self) -> "WorkGraph":
        known = {u.unit_id for u in self.units}
        for u in self.units:
            missing = set(u.depends_on) - known
            if missing:
                raise ValueError(f"WorkUnit {u.unit_id} depends on unknown units: {missing}")

        adj = {u.unit_id: u.depends_on for u in self.units}
        visiting: set[str] = set()
        visited: set[str] = set()

        def dfs(node: str) -> None:
            if node in visited:
                return
            if node in visiting:
                raise ValueError(f"Cycle detected in WorkGraph at unit {node}")
            visiting.add(node)
            for neighbor in adj.get(node, ()):
                dfs(neighbor)
            visiting.remove(node)
            visited.add(node)

        for node in adj:
            dfs(node)
        return self

    def dependency_edges(self) -> tuple[tuple[str, str], ...]:
        """Stable (unit_id, dependency_id) edges matching the step DAG."""
        edges: list[tuple[str, str]] = []
        for unit in self.units:
            for dep in unit.depends_on:
                edges.append((unit.unit_id, dep))
        return tuple(edges)

    def compute_execution_order(self) -> tuple[str, ...]:
        """Deterministic Kahn topological order (unit_index tie-break)."""
        by_id = {u.unit_id: u for u in self.units}
        indeg = {u.unit_id: 0 for u in self.units}
        children: dict[str, list[str]] = {u.unit_id: [] for u in self.units}
        for u in self.units:
            for dep in u.depends_on:
                children[dep].append(u.unit_id)
                indeg[u.unit_id] += 1
        ready = sorted(
            [uid for uid, d in indeg.items() if d == 0],
            key=lambda i: (by_id[i].unit_index, i),
        )
        order: list[str] = []
        while ready:
            n = ready.pop(0)
            order.append(n)
            for ch in sorted(children[n], key=lambda i: (by_id[i].unit_index, i)):
                indeg[ch] -= 1
                if indeg[ch] == 0:
                    ready.append(ch)
                    ready.sort(key=lambda i: (by_id[i].unit_index, i))
        if len(order) != len(self.units):
            raise ValueError("WorkGraph topological sort incomplete (cycle?)")
        return tuple(order)


class Work(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    work_id: str = Field(default_factory=_uuid_str)
    tenant_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    actor_ref: str = Field(min_length=1)
    work_type: WorkType
    definition: WorkDefinition
    graph: WorkGraph | None = None
    external_ref: ExternalWorkRef | None = None
    status: WorkStatus = WorkStatus.DRAFT
    created_at: str = Field(default_factory=_utc_now_iso)


__all__ = [
    "WorkStatus",
    "UnitStatus",
    "ExternalWorkRef",
    "WorkType",
    "WorkDefinition",
    "WorkContract",
    "WorkUnit",
    "WorkGraph",
    "Work",
]
