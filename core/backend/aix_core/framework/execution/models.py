"""Immutable F4-P1 execution models.

The models are read-only framework views around the existing objective-cycle
contracts.  Runtime contracts remain the serialization and behavior authority;
these types add no execution, scheduling, verification, or recovery behavior.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Mapping, TypeAlias

from backend.aix_core.runtime.objective.contracts import (
    RequiredEffectV1,
    StepContractV1,
)

PlanStatus: TypeAlias = Literal["DRAFT", "ACCEPTED", "REJECTED", "SUPERSEDED"]
StepStatus: TypeAlias = Literal[
    "PENDING", "DISPATCHED", "IN_PROGRESS", "VERIFIED", "FAILED", "BLOCKED", "SKIPPED"
]
AttemptJournalStatus: TypeAlias = Literal[
    "OPEN", "COMMITTED", "ABORTED", "ORPHAN_COMPENSATED"
]
AttemptOutcome: TypeAlias = Literal["SUCCEEDED", "FAILED", "CANCELLED", "ABORTED"]

_PLAN_STATUSES = frozenset({"DRAFT", "ACCEPTED", "REJECTED", "SUPERSEDED"})
_STEP_STATUSES = frozenset(
    {"PENDING", "DISPATCHED", "IN_PROGRESS", "VERIFIED", "FAILED", "BLOCKED", "SKIPPED"}
)
_ATTEMPT_STATUSES = frozenset({"OPEN", "COMMITTED", "ABORTED", "ORPHAN_COMPENSATED"})
_ATTEMPT_OUTCOMES = frozenset({"SUCCEEDED", "FAILED", "CANCELLED", "ABORTED"})


def _required(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")


def _string_tuple(raw: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(raw, (list, tuple)):
        raise ValueError(f"{field_name} must be a list or tuple")
    values = tuple(str(value) for value in raw)
    if any(not value.strip() for value in values):
        raise ValueError(f"{field_name} cannot contain empty identifiers")
    return values


@dataclass(frozen=True, slots=True)
class ExecutionObjectiveRef:
    """Stable identity and authority references copied from ObjectiveEnvelopeV1."""

    objective_id: str
    tenant_id: str
    actor_ref: str
    project_id: str
    workhub_task_ref: str
    correlation_id: str
    causation_id: str | None
    idempotency_key: str
    repository_scope: tuple[str, ...]

    def __post_init__(self) -> None:
        for name in (
            "objective_id", "tenant_id", "actor_ref", "project_id",
            "workhub_task_ref", "correlation_id", "idempotency_key",
        ):
            _required(getattr(self, name), name)
        if not self.repository_scope:
            raise ValueError("repository_scope must contain at least one repository")

    def to_dict(self) -> dict[str, Any]:
        return {
            "objective_id": self.objective_id,
            "tenant_id": self.tenant_id,
            "actor_ref": self.actor_ref,
            "project_id": self.project_id,
            "workhub_task_ref": self.workhub_task_ref,
            "correlation_id": self.correlation_id,
            "causation_id": self.causation_id,
            "idempotency_key": self.idempotency_key,
            "repository_scope": list(self.repository_scope),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ExecutionObjectiveRef":
        return cls(
            objective_id=str(raw["objective_id"]),
            tenant_id=str(raw["tenant_id"]),
            actor_ref=str(raw["actor_ref"]),
            project_id=str(raw["project_id"]),
            workhub_task_ref=str(raw["workhub_task_ref"]),
            correlation_id=str(raw["correlation_id"]),
            causation_id=str(raw["causation_id"]) if raw.get("causation_id") is not None else None,
            idempotency_key=str(raw["idempotency_key"]),
            repository_scope=_string_tuple(raw["repository_scope"], "repository_scope"),
        )


@dataclass(frozen=True, slots=True)
class ExecutionLane:
    lane_id: str
    ordinal: int
    step_ids: tuple[str, ...]
    concurrency_limit: Literal[1] = 1

    def __post_init__(self) -> None:
        _required(self.lane_id, "lane_id")
        if self.ordinal < 0:
            raise ValueError("ordinal must be non-negative")
        if not self.step_ids:
            raise ValueError("step_ids must contain at least one step")
        if len(self.step_ids) != len(set(self.step_ids)):
            raise ValueError("step_ids must be unique within a lane")
        if self.concurrency_limit != 1:
            raise ValueError("F4 execution lanes must remain serial (concurrency_limit=1)")

    def to_dict(self) -> dict[str, Any]:
        return {
            "lane_id": self.lane_id,
            "ordinal": self.ordinal,
            "step_ids": list(self.step_ids),
            "concurrency_limit": self.concurrency_limit,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ExecutionLane":
        return cls(
            lane_id=str(raw["lane_id"]),
            ordinal=int(raw["ordinal"]),
            step_ids=_string_tuple(raw["step_ids"], "step_ids"),
            concurrency_limit=int(raw.get("concurrency_limit", 1)),  # type: ignore[arg-type]
        )


@dataclass(frozen=True, slots=True)
class Step:
    step_id: str
    index: int
    purpose: str
    depends_on: tuple[str, ...]
    target_scope: tuple[str, ...]
    acceptance_criteria: tuple[str, ...]
    validation_commands: tuple[str, ...]
    required_effects: tuple[RequiredEffectV1, ...]
    required_evidence: tuple[str, ...]
    risk_tier: str
    approval_required: bool
    status: StepStatus
    source_contract: StepContractV1

    def __post_init__(self) -> None:
        _required(self.step_id, "step_id")
        _required(self.purpose, "purpose")
        _required(self.risk_tier, "risk_tier")
        if self.index < 0:
            raise ValueError("index must be non-negative")
        if self.step_id in self.depends_on:
            raise ValueError("a step cannot depend on itself")
        if not self.target_scope:
            raise ValueError("target_scope must contain at least one target")
        if not self.acceptance_criteria:
            raise ValueError("acceptance_criteria must not be empty")
        if not self.validation_commands:
            raise ValueError("validation_commands must not be empty")
        if self.status not in _STEP_STATUSES:
            raise ValueError(f"invalid step status: {self.status!r}")
        if self.source_contract.id != self.step_id:
            raise ValueError("source_contract.id must equal step_id")
        if self.source_contract.step_index != self.index:
            raise ValueError("source_contract.step_index must equal index")

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "index": self.index,
            "purpose": self.purpose,
            "depends_on": list(self.depends_on),
            "target_scope": list(self.target_scope),
            "acceptance_criteria": list(self.acceptance_criteria),
            "validation_commands": list(self.validation_commands),
            "required_effects": [effect.model_dump(mode="json") for effect in self.required_effects],
            "required_evidence": list(self.required_evidence),
            "risk_tier": self.risk_tier,
            "approval_required": self.approval_required,
            "status": self.status,
            "source_contract": self.source_contract.model_dump(mode="json"),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "Step":
        contract = StepContractV1.model_validate(raw["source_contract"])
        return cls(
            step_id=str(raw["step_id"]),
            index=int(raw["index"]),
            purpose=str(raw["purpose"]),
            depends_on=_string_tuple(raw.get("depends_on", ()), "depends_on"),
            target_scope=_string_tuple(raw["target_scope"], "target_scope"),
            acceptance_criteria=_string_tuple(raw["acceptance_criteria"], "acceptance_criteria"),
            validation_commands=_string_tuple(raw["validation_commands"], "validation_commands"),
            required_effects=tuple(
                RequiredEffectV1.model_validate(effect) for effect in raw.get("required_effects", ())
            ),
            required_evidence=_string_tuple(raw.get("required_evidence", ()), "required_evidence"),
            risk_tier=str(raw["risk_tier"]),
            approval_required=bool(raw["approval_required"]),
            status=str(raw["status"]),  # type: ignore[arg-type]
            source_contract=contract,
        )


@dataclass(frozen=True, slots=True)
class ExecutionPlan:
    plan_id: str
    objective_id: str
    cycle_id: str
    revision: int
    status: PlanStatus
    lanes: tuple[ExecutionLane, ...]
    step_ids: tuple[str, ...]
    source_contract_ref: str
    approval_ref: str | None

    def __post_init__(self) -> None:
        for name in ("plan_id", "objective_id", "cycle_id", "source_contract_ref"):
            _required(getattr(self, name), name)
        if self.revision < 1:
            raise ValueError("revision must be at least 1")
        if self.status not in _PLAN_STATUSES:
            raise ValueError(f"invalid plan status: {self.status!r}")
        if not self.step_ids or len(self.step_ids) != len(set(self.step_ids)):
            raise ValueError("step_ids must be non-empty and unique")
        if not self.lanes:
            raise ValueError("lanes must not be empty")
        if tuple(lane.ordinal for lane in self.lanes) != tuple(range(len(self.lanes))):
            raise ValueError("lane ordinals must be contiguous and ordered from zero")
        lane_ids = tuple(step_id for lane in self.lanes for step_id in lane.step_ids)
        if len(lane_ids) != len(set(lane_ids)) or set(lane_ids) != set(self.step_ids):
            raise ValueError("lanes must contain every plan step exactly once")

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "objective_id": self.objective_id,
            "cycle_id": self.cycle_id,
            "revision": self.revision,
            "status": self.status,
            "lanes": [lane.to_dict() for lane in self.lanes],
            "step_ids": list(self.step_ids),
            "source_contract_ref": self.source_contract_ref,
            "approval_ref": self.approval_ref,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ExecutionPlan":
        lanes_raw = raw.get("lanes")
        if not isinstance(lanes_raw, (list, tuple)):
            raise ValueError("lanes must be a list or tuple")
        return cls(
            plan_id=str(raw["plan_id"]),
            objective_id=str(raw["objective_id"]),
            cycle_id=str(raw["cycle_id"]),
            revision=int(raw["revision"]),
            status=str(raw["status"]),  # type: ignore[arg-type]
            lanes=tuple(ExecutionLane.from_dict(lane) for lane in lanes_raw),
            step_ids=_string_tuple(raw["step_ids"], "step_ids"),
            source_contract_ref=str(raw["source_contract_ref"]),
            approval_ref=str(raw["approval_ref"]) if raw.get("approval_ref") is not None else None,
        )


@dataclass(frozen=True, slots=True)
class Attempt:
    attempt_id: str
    step_id: str
    number: int
    command_id: str
    status: AttemptJournalStatus
    outcome: AttemptOutcome | None
    effects_applied: bool
    started_at: str
    completed_at: str | None
    evidence_refs: tuple[str, ...]
    result_ref: str | None

    def __post_init__(self) -> None:
        for name in ("attempt_id", "step_id", "command_id", "started_at"):
            _required(getattr(self, name), name)
        if self.number < 1:
            raise ValueError("number must be at least 1")
        if self.status not in _ATTEMPT_STATUSES:
            raise ValueError(f"invalid attempt status: {self.status!r}")
        if self.outcome is not None and self.outcome not in _ATTEMPT_OUTCOMES:
            raise ValueError(f"invalid attempt outcome: {self.outcome!r}")
        if self.status == "OPEN":
            if self.outcome is not None or self.completed_at is not None:
                raise ValueError("OPEN attempts cannot have an outcome or completed_at")
        else:
            if self.outcome is None or self.completed_at is None:
                raise ValueError("terminal journal states require outcome and completed_at")
        if self.status in {"ABORTED", "ORPHAN_COMPENSATED"} and self.outcome != "ABORTED":
            raise ValueError("aborted/compensated attempts must have ABORTED outcome")
        if self.outcome == "SUCCEEDED" and (
            self.status != "COMMITTED" or not self.effects_applied
        ):
            raise ValueError("SUCCEEDED requires COMMITTED with effects_applied")

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempt_id": self.attempt_id,
            "step_id": self.step_id,
            "number": self.number,
            "command_id": self.command_id,
            "status": self.status,
            "outcome": self.outcome,
            "effects_applied": self.effects_applied,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "evidence_refs": list(self.evidence_refs),
            "result_ref": self.result_ref,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "Attempt":
        return cls(
            attempt_id=str(raw["attempt_id"]),
            step_id=str(raw["step_id"]),
            number=int(raw["number"]),
            command_id=str(raw["command_id"]),
            status=str(raw["status"]),  # type: ignore[arg-type]
            outcome=str(raw["outcome"]) if raw.get("outcome") is not None else None,  # type: ignore[arg-type]
            effects_applied=bool(raw["effects_applied"]),
            started_at=str(raw["started_at"]),
            completed_at=str(raw["completed_at"]) if raw.get("completed_at") is not None else None,
            evidence_refs=_string_tuple(raw.get("evidence_refs", ()), "evidence_refs"),
            result_ref=str(raw["result_ref"]) if raw.get("result_ref") is not None else None,
        )


__all__ = [
    "Attempt",
    "AttemptJournalStatus",
    "AttemptOutcome",
    "ExecutionLane",
    "ExecutionObjectiveRef",
    "ExecutionPlan",
    "PlanStatus",
    "Step",
    "StepStatus",
]

