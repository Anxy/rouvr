from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


RunStatus = Literal[
    "queued",
    "loaded",
    "recon_complete",
    "planned",
    "executed",
    "verified",
    "writeback_complete",
    "success",
    "blocked",
    "failed",
    "needs_human_input",
]


class RunFailure(BaseModel):
    model_config = ConfigDict(extra="forbid")

    classification: str
    phase: str
    code: str
    message: str
    retryable: bool = False
    details: dict[str, Any] = Field(default_factory=dict)


class TimestampLedger(BaseModel):
    model_config = ConfigDict(extra="forbid")

    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)
    loaded_at: str | None = None
    recon_at: str | None = None
    planned_at: str | None = None
    executed_at: str | None = None
    verified_at: str | None = None
    writeback_at: str | None = None
    completed_at: str | None = None


class ReconResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str = "ready"
    reason: str | None = None
    entity_query: str
    execution_contract: dict[str, Any] = Field(default_factory=dict)
    context_error: str | None = None
    codeintel: dict[str, Any] = Field(default_factory=dict)
    target_file: str | None = None
    target_symbol: str | None = None
    target_files: list[str] = Field(default_factory=list)
    anchor: dict[str, Any] = Field(default_factory=dict)
    symbol_ranges: list[dict[str, Any]] = Field(default_factory=list)
    anchor_ranges: list[dict[str, Any]] = Field(default_factory=list)
    validation_target: str
    related_tests: list[str] = Field(default_factory=list)
    dependency_context: dict[str, Any] = Field(default_factory=dict)
    slim_packet_inputs: dict[str, Any] = Field(default_factory=dict)


class PlannerResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    steps: list[dict[str, Any]] = Field(default_factory=list)
    planner_packet: dict[str, Any] = Field(default_factory=dict)
    planner_trace: dict[str, Any] = Field(default_factory=dict)
    declared_validation: str | None = None
    file_targets: list[str] = Field(default_factory=list)
    symbol_targets: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    blocked_reason: str | None = None
    input_summary: dict[str, Any] = Field(default_factory=dict)
    raw_output_text: str | None = None
    normalized_plan: list[dict[str, Any]] = Field(default_factory=list)
    validation_failures: list[str] = Field(default_factory=list)
    trace_artifact_ref: str | None = None


class ExecutionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    run_result: dict[str, Any] = Field(default_factory=dict)
    changed_files: list[str] = Field(default_factory=list)
    commands: list[dict[str, Any]] = Field(default_factory=list)
    tests: dict[str, Any] = Field(default_factory=dict)
    diff_refs: list[str] = Field(default_factory=list)
    failure: dict[str, Any] | None = None
    trace: dict[str, Any] = Field(default_factory=dict)
    envelope: dict[str, Any] = Field(default_factory=dict)


class VerificationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    classification: str
    phase: str | None = None
    reason_code: str | None = None
    summary: str
    retry_hint: str | None = None
    checks: list[dict[str, Any]] = Field(default_factory=list)
    reasons: list[dict[str, Any]] = Field(default_factory=list)
    verifier_decision: dict[str, Any] = Field(default_factory=dict)


class WritebackResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    writeback: dict[str, Any] = Field(default_factory=dict)
    verification_evaluation: dict[str, Any] | None = None
    verification_error: str | None = None
    closeout_operation: dict[str, Any] | None = None
    closeout_error: str | None = None
    transition_ok: bool = False
    transition_error: str | None = None
    evidence_ok: bool = False
    evidence_error: str | None = None


class PhaseArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    ref: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class AixOrchestratorV2RunState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str | None = None
    run_id: str = Field(default_factory=lambda: f"aixv2-{uuid4().hex[:16]}")
    current_phase: str = "queued"
    task_payload: dict[str, Any] = Field(default_factory=dict)
    packet: dict[str, Any] = Field(default_factory=dict)
    recon: ReconResult | None = None
    plan: PlannerResult | None = None
    execution: ExecutionResult | None = None
    verification: VerificationResult | None = None
    writeback: WritebackResult | None = None
    status: RunStatus = "queued"
    failure: RunFailure | None = None
    artifacts: list[PhaseArtifact] = Field(default_factory=list)
    timestamps: TimestampLedger = Field(default_factory=TimestampLedger)
    validator_ledger: dict[str, Any] = Field(default_factory=dict)

    def touch(self) -> None:
        self.timestamps.updated_at = utc_now()

    def add_artifact(self, *, name: str, payload: dict[str, Any], ref: str | None = None) -> None:
        self.artifacts.append(PhaseArtifact(name=name, ref=ref, payload=dict(payload)))
        self.touch()

    def fail(
        self,
        *,
        classification: str,
        phase: str,
        code: str,
        message: str,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
        status: RunStatus = "failed",
    ) -> None:
        self.failure = RunFailure(
            classification=classification,
            phase=phase,
            code=code,
            message=message,
            retryable=retryable,
            details=dict(details or {}),
        )
        self.status = status
        self.timestamps.completed_at = utc_now()
        self.touch()


class AixOrchestratorV2TriggerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(..., min_length=1)
    task_number: int = Field(..., ge=1)
    workspace_root: str = Field(..., min_length=1)
    repo_key: str = Field(default="hyeve-aix-service", min_length=1)
    explicit_profile_id: str | None = Field(default=None, min_length=1)
    tenant_id: str = Field(default="default", min_length=1)
    mode: Literal["auto", "inline", "temporal"] = "auto"
    writeback: bool = True
    actor_id: str = Field(default="aix-orchestrator-v2", min_length=1)


class AixOrchestratorV2TriggerResponse(BaseModel):
    success: bool = True
    data: dict[str, Any]
