from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from backend.models.planner_slice_packet_models import (
    PlannerAllowedSurfaceCurrent,
    PlannerOutputSchemaCurrent,
    PlannerSlicePacketCurrent,
)


class PlannerRequestPacketProvenanceCurrent(BaseModel):
    admission_status: str = Field(..., min_length=1)
    admission_packet_fingerprint: str | None = None
    ticket_contract_ref: str | None = None


class PlannerRequestPacketLineageCurrent(BaseModel):
    slice_id: str = Field(..., min_length=1)
    parent_task_number: int = Field(..., ge=1)


class PlannerRequestTaskIdentityCurrent(BaseModel):
    task_number: int = Field(..., ge=1)
    slice_id: str = Field(..., min_length=1)
    parent_task_number: int | None = Field(default=None, ge=1)


class PlannerRequestCodeintelAnchorCurrent(BaseModel):
    file_path: str = Field(..., min_length=1)
    symbol: str | None = None
    start_line: int | None = Field(default=None, ge=1)
    end_line: int | None = Field(default=None, ge=1)
    output_content: str | None = None
    snapshot_id: str = Field(..., min_length=1)
    symbol_source_hash: str = Field(..., min_length=1)
    content_ref: str | None = None


class PlannerRequestReferencesCurrent(BaseModel):
    ticket_contract_ref: str | None = None
    anchor_snapshot_ref: str | None = None
    runtime_capsule_ref: str | None = None


class PlannerRequestPacketCurrent(BaseModel):
    schema_name: Literal["planner_slice.current"] = "planner_slice.current"
    schema_version: Literal["current"] = "current"
    task_number: int = Field(..., ge=1)
    slice_id: str = Field(..., min_length=1)
    objective: str = Field(..., min_length=1)
    target_file: str = Field(..., min_length=1)
    target_symbol: str | None = None
    bounded_mutation_goal: str = Field(..., min_length=1)
    allowed_surface: PlannerAllowedSurfaceCurrent
    validation_command: str = Field(..., min_length=1)
    output_schema: PlannerOutputSchemaCurrent
    hard_failure_rules: list[str] = Field(..., min_length=1, max_length=4)


class PlannerRequestExecutionContractCurrent(BaseModel):
    packet_max_bytes: int = Field(..., ge=1)
    packet_mode: Literal["strict_current_only"] = "strict_current_only"


class PlannerRequestEnvelopeCurrent(BaseModel):
    schema_name: Literal["aix.utf_task.current"] = "aix.utf_task.current"
    schema_version: Literal["current"] = "current"
    task_identity: PlannerRequestTaskIdentityCurrent
    planner_packet: PlannerSlicePacketCurrent
    execution_contract: PlannerRequestExecutionContractCurrent
    references: PlannerRequestReferencesCurrent | None = None


PlannerRequestEnvelopeCurrent.model_rebuild()
