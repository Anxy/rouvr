"""C002: canonical runtime identity model (ADR-004).

Maps legacy identity fields onto one tenant-scoped hierarchy without creating a
second objective authority. Migration base remains objective_cycle.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal
from backend.aix_core.runtime.objective.ulid_compat import new_ulid

IDENTITY_CONTRACT_VERSION = "aix.runtime_identity.v1"

CanonicalKind = Literal[
    "tenant",
    "objective",
    "objective_revision",
    "run",
    "repository_snapshot",
    "plan_revision",
    "step_contract",
    "attempt",
    "effect",
    "artifact",
    "evidence",
    "approval",
    "budget",
    "completion_decision",
]

LegacySystem = Literal[
    "objective_cycle",
    "workhub",
    "aix_orchestrator_v2",
    "execution_kernel",
    "langgraph",
    "operator_runs",
    "mvp_product",
]


def new_id(prefix: str) -> str:
    return f"{prefix}_{new_ulid()}"


@dataclass(frozen=True)
class CanonicalIdentity:
    """One node in the ADR-004 hierarchy."""

    kind: CanonicalKind
    id: str
    tenant_id: str
    parent_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LegacyIdentityRef:
    """External/competing identity that must map into the canonical hierarchy."""

    system: LegacySystem
    field: str
    value: str
    tenant_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class IdentityBinding:
    """Deterministic binding from legacy ref → canonical identity."""

    legacy: LegacyIdentityRef
    canonical: CanonicalIdentity
    mapping_rule: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "legacy": self.legacy.to_dict(),
            "canonical": self.canonical.to_dict(),
            "mapping_rule": self.mapping_rule,
        }


@dataclass
class IdentityRegistry:
    """In-process registry enforcing one canonical owner per legacy ref within a tenant."""

    contract_version: str = IDENTITY_CONTRACT_VERSION
    bindings: list[IdentityBinding] = field(default_factory=list)
    _by_legacy_key: dict[str, IdentityBinding] = field(default_factory=dict, repr=False)
    _by_canonical_id: dict[str, IdentityBinding] = field(default_factory=dict, repr=False)

    @staticmethod
    def _legacy_key(legacy: LegacyIdentityRef) -> str:
        tenant = legacy.tenant_id or ""
        return f"{tenant}|{legacy.system}|{legacy.field}|{legacy.value}"

    def get_by_legacy(self, legacy: LegacyIdentityRef) -> IdentityBinding | None:
        return self._by_legacy_key.get(self._legacy_key(legacy))

    def register(self, binding: IdentityBinding) -> IdentityBinding:
        if not binding.canonical.tenant_id.strip():
            raise ValueError("TENANT_REQUIRED")
        if binding.legacy.tenant_id and binding.legacy.tenant_id != binding.canonical.tenant_id:
            raise ValueError("CROSS_TENANT_BINDING_FORBIDDEN")

        key = self._legacy_key(binding.legacy)
        existing = self._by_legacy_key.get(key)
        if existing is not None:
            if existing.canonical.id != binding.canonical.id:
                raise ValueError(
                    f"IDENTITY_COLLISION:{key}:{existing.canonical.id}:{binding.canonical.id}"
                )
            return existing

        # Same canonical id cannot be claimed by a different legacy key of the same system+field
        # under another tenant (cross-tenant collision).
        prior = self._by_canonical_id.get(binding.canonical.id)
        if prior is not None and prior.canonical.tenant_id != binding.canonical.tenant_id:
            raise ValueError(
                f"CROSS_TENANT_CANONICAL_REUSE:{binding.canonical.id}:"
                f"{prior.canonical.tenant_id}:{binding.canonical.tenant_id}"
            )

        self._by_legacy_key[key] = binding
        self._by_canonical_id[binding.canonical.id] = binding
        self.bindings.append(binding)
        return binding

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_version": self.contract_version,
            "binding_count": len(self.bindings),
            "bindings": [b.to_dict() for b in self.bindings],
        }


def map_objective_cycle_objective(*, tenant_id: str, objective_id: str) -> IdentityBinding:
    return IdentityBinding(
        legacy=LegacyIdentityRef(
            system="objective_cycle",
            field="objective_id",
            value=objective_id,
            tenant_id=tenant_id,
        ),
        canonical=CanonicalIdentity(
            kind="objective",
            id=objective_id,
            tenant_id=tenant_id,
        ),
        mapping_rule="objective_cycle.objective_id_is_canonical_objective",
    )


def map_objective_cycle_cycle(*, tenant_id: str, cycle_id: str, objective_id: str) -> IdentityBinding:
    """cycle_id is the current Run identity during migration (ADR-004 Run)."""
    return IdentityBinding(
        legacy=LegacyIdentityRef(
            system="objective_cycle",
            field="cycle_id",
            value=cycle_id,
            tenant_id=tenant_id,
        ),
        canonical=CanonicalIdentity(
            kind="run",
            id=cycle_id,
            tenant_id=tenant_id,
            parent_id=objective_id,
        ),
        mapping_rule="objective_cycle.cycle_id_is_canonical_run",
    )


def map_workhub_task(
    *,
    tenant_id: str,
    project_id: str,
    task_number: int,
    objective_id: str | None = None,
) -> IdentityBinding:
    """WorkHub project_id/task_number is an external correlation, not a second Objective."""
    composite = f"{project_id}:{int(task_number)}"
    canonical_id = objective_id or new_id("obj")
    return IdentityBinding(
        legacy=LegacyIdentityRef(
            system="workhub",
            field="project_id:task_number",
            value=composite,
            tenant_id=tenant_id,
        ),
        canonical=CanonicalIdentity(
            kind="objective",
            id=canonical_id,
            tenant_id=tenant_id,
        ),
        mapping_rule="workhub.project_task_correlates_to_objective",
    )


def map_orchestrator_v2_run(
    *,
    tenant_id: str,
    run_id: str,
    task_id: str | None = None,
    objective_id: str | None = None,
) -> IdentityBinding:
    """orchestrator_v2 run_id maps to a Run under an Objective (adapter; not a second authority)."""
    parent = objective_id or (f"legacy_obj_from_task:{task_id}" if task_id else new_id("obj"))
    return IdentityBinding(
        legacy=LegacyIdentityRef(
            system="aix_orchestrator_v2",
            field="run_id",
            value=run_id,
            tenant_id=tenant_id,
        ),
        canonical=CanonicalIdentity(
            kind="run",
            id=run_id if run_id.startswith("run_") else f"run_v2_{run_id}",
            tenant_id=tenant_id,
            parent_id=parent,
        ),
        mapping_rule="orchestrator_v2.run_id_adapts_to_canonical_run",
    )


def map_execution_kernel_problem(
    *,
    tenant_id: str,
    problem_id: str,
    objective_id: str | None = None,
) -> IdentityBinding:
    parent = objective_id or new_id("obj")
    return IdentityBinding(
        legacy=LegacyIdentityRef(
            system="execution_kernel",
            field="problem_id",
            value=problem_id,
            tenant_id=tenant_id,
        ),
        canonical=CanonicalIdentity(
            kind="objective",
            id=parent,
            tenant_id=tenant_id,
        ),
        mapping_rule="execution_kernel.problem_correlates_to_objective",
    )


def map_execution_kernel_work_item(
    *,
    tenant_id: str,
    work_item_id: str,
    problem_id: str,
) -> IdentityBinding:
    return IdentityBinding(
        legacy=LegacyIdentityRef(
            system="execution_kernel",
            field="work_item_id",
            value=work_item_id,
            tenant_id=tenant_id,
        ),
        canonical=CanonicalIdentity(
            kind="step_contract",
            id=work_item_id if work_item_id.startswith("step_") else f"step_wi_{work_item_id}",
            tenant_id=tenant_id,
            parent_id=problem_id,
        ),
        mapping_rule="execution_kernel.work_item_adapts_to_step_contract",
    )


LIFECYCLE_SEMANTICS: dict[str, str] = {
    "retry": "Creates a new Attempt under the same StepContract/Run.",
    "repair": "Creates a changed directive and a new Attempt; PlanRevision unchanged unless invalid.",
    "replan": "Creates a new PlanRevision in the same Run; prior steps classified PRESERVE/REVERIFY/INVALIDATE/COMPENSATE/ROLL_BACK/HUMAN_DECISION.",
    "reunderstand": "Creates a new ObjectiveRevision and normally a new Run.",
    "completion_decision": "Only Completion Authority may emit terminal success over durable evidence (C003).",
}
