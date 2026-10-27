"""Pure task-contract data models.

This module intentionally contains only canonical contract shapes and intrinsic
field defaults. It must not grow orchestration policy.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

TicketHandoffStatus = Literal[
    "ready_for_planning",
    "ready_for_execution",
    "ready_for_verification",
    "needs_clarification",
    "needs_replan",
    "needs_approval",
    "blocked",
]

TicketFailureCategory = Literal[
    "missing_input",
    "bad_decomposition",
    "execution_failed",
    "validation_failed",
    "approval_required",
    "risk_exceeded",
    "blocked_dependency",
]

TicketInterfaceChangePolicy = Literal[
    "preserve",
    "additive_optional_params",
    "signature_change_allowed",
]


class TaskTicketScope(BaseModel):
    in_scope: list[str] = Field(default_factory=list)
    out_of_scope: list[str] = Field(default_factory=list)


class TaskTicketTargetSurface(BaseModel):
    target_files: list[str] = Field(default_factory=list)
    prompt_symbol_targets: list[dict[str, str]] = Field(default_factory=list)
    owning_symbols: list[str] = Field(default_factory=list)
    bounded_search_surface: list[str] = Field(default_factory=list)


class TaskTicketExecutionSlice(BaseModel):
    summary: str | None = None
    execution_phase: str | None = None
    target_files: list[str] = Field(default_factory=list)
    validation_commands: list[str] = Field(default_factory=list)
    interface_change_policy: TicketInterfaceChangePolicy = "preserve"


class TaskTicketContractCurrent(BaseModel):
    objective: str
    scope: TaskTicketScope = Field(default_factory=TaskTicketScope)
    target_surface: TaskTicketTargetSurface = Field(default_factory=TaskTicketTargetSurface)
    acceptance_criteria: list[str] = Field(default_factory=list)
    validation_plan: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    stop_conditions: list[str] = Field(default_factory=list)
    non_goals: list[str] = Field(default_factory=list)
    execution_slice: TaskTicketExecutionSlice | None = None
    handoff_status: TicketHandoffStatus = "ready_for_planning"
    failure_category: TicketFailureCategory | None = None


__all__ = [
    "TaskTicketContractCurrent",
    "TaskTicketExecutionSlice",
    "TaskTicketScope",
    "TaskTicketTargetSurface",
    "TicketFailureCategory",
    "TicketHandoffStatus",
    "TicketInterfaceChangePolicy",
]
