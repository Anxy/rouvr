"""Transitional ``ayrpack_current`` packet schema.

This module is schema-only and intentionally carries no runtime behavior.

Target protocol authority now lives in
``projects/AIX/src/aix/contracts/ayrpack_protocol_current.contract.json``.
``ayrpack_current`` remains the migration-source internal packet shape during the
CloudEvents transition.

Current authority model:
- ``active_frame.status`` is the lifecycle source of truth.
- top-level ``contract_ref`` points at the authoritative task contract snapshot.
- ``active_frame.contract_ref`` can narrow that pointer to the currently active
  review/execution slice.
- ``spine`` is append-only audit history; it is not a second lifecycle source.
"""
from __future__ import annotations

from enum import Enum
from typing import Literal, NotRequired, TypedDict


class VisibilityTier(str, Enum):
    """Field visibility tier label."""

    PUBLIC = "public"
    ASSIGNED = "assigned"
    AUDIT = "audit"
    OWNER = "owner"


class ActiveFrameKind(str, Enum):
    """Current execution frame type."""

    PLANNER = "planner"
    EXECUTOR = "executor"
    VERIFIER = "verifier"
    REVIEW = "review"
    OPERATOR = "operator"
    SYSTEM = "system"


class SpineEntryKind(str, Enum):
    """Append-only timeline entry classification."""

    EVENT = "event"
    DECISION = "decision"
    ARTIFACT = "artifact"
    DIFF = "diff"
    HANDOFF = "handoff"
    ATTESTATION = "attestation"


class DiffFormat(str, Enum):
    """Patch record format for spine diff entries."""

    UNIFIED = "unified"
    GIT = "git"
    SEARCH_REPLACE = "search_replace"
    STRUCTURED = "structured"


class ContractPointerKind(str, Enum):
    """Reference type for the external contract object."""

    TASK_TICKET = "task_ticket"
    EXECUTION_SLICE = "execution_slice"
    ACCEPTANCE_CONTRACT = "acceptance_contract"
    VALIDATION_PLAN = "validation_plan"
    EXTERNAL = "external"


class ActorRefCurrent(TypedDict):
    """Typed actor reference for human, agent, or system routing."""

    kind: Literal["human", "agent", "system"]  # VISIBILITY: public
    id: str  # VISIBILITY: internal
    display_name: NotRequired[str]  # VISIBILITY: public


class AyrpackMetaCurrent(TypedDict):
    """Packet meta block."""

    schema_version: Literal["current"]  # VISIBILITY: public
    trace_id: str  # VISIBILITY: internal
    packet_id: NotRequired[str]  # VISIBILITY: internal
    created_at_utc: NotRequired[str]  # VISIBILITY: public
    updated_at_utc: NotRequired[str]  # VISIBILITY: public
    producer: str  # VISIBILITY: internal
    visibility_default: NotRequired[VisibilityTier]  # VISIBILITY: public
    digest_cursor: NotRequired[int]  # VISIBILITY: audit


class AyrpackHeaderCurrent(TypedDict):
    """Stable packet identity and routing header."""

    packet_type: Literal["ayrpack"]  # VISIBILITY: public
    packet_version: Literal["current"]  # VISIBILITY: public
    tenant_id: NotRequired[str]  # VISIBILITY: restricted
    project_id: NotRequired[str]  # VISIBILITY: internal
    task_id: NotRequired[str]  # VISIBILITY: internal
    task_number: NotRequired[int]  # VISIBILITY: internal
    run_id: NotRequired[str]  # VISIBILITY: internal
    correlation_id: NotRequired[str]  # VISIBILITY: internal
    lane: NotRequired[str]  # VISIBILITY: internal
    # AIX lane identifies the execution/routing channel that owns this packet.
    parent_packet_id: NotRequired[str]  # VISIBILITY: internal
    child_packet_ids: NotRequired[list[str]]  # VISIBILITY: internal
    source_system: NotRequired[str]  # VISIBILITY: public


class ContractRefCurrent(TypedDict):
    """Pointer to authoritative contract content stored elsewhere.

    The packet never embeds full contract payloads here. Surfaces such as
    WorkHub task metadata, review-contract views, and execution slices own the
    concrete payload; ayrpack only carries the stable pointer.
    """

    kind: ContractPointerKind  # VISIBILITY: public
    ref: str  # VISIBILITY: internal
    version: NotRequired[str]  # VISIBILITY: public
    digest: NotRequired[str]  # VISIBILITY: restricted
    summary: NotRequired[str]  # VISIBILITY: public


class ActiveFrameCurrent(TypedDict):
    """Current authoritative frame.

    ``contract_ref`` points to the run-specific slice or execution intent for
    this frame, not the full task-level contract.
    """

    frame_id: str  # VISIBILITY: internal
    kind: ActiveFrameKind  # VISIBILITY: public
    phase: str  # VISIBILITY: public
    status: str  # VISIBILITY: public
    actor: NotRequired[ActorRefCurrent]  # VISIBILITY: internal
    assigned_to: NotRequired[ActorRefCurrent]  # VISIBILITY: assigned
    started_at_utc: NotRequired[str]  # VISIBILITY: public
    updated_at_utc: NotRequired[str]  # VISIBILITY: public
    summary: NotRequired[str]  # VISIBILITY: public
    contract_ref: NotRequired[ContractRefCurrent]  # VISIBILITY: public


class SpineDiffFileRecordCurrent(TypedDict):
    """Per-file diff record embedded in a diff spine entry."""

    path: str  # VISIBILITY: internal
    change_type: Literal["add", "modify", "delete", "rename"]  # VISIBILITY: public
    old_path: NotRequired[str]  # VISIBILITY: internal
    hunk_count: NotRequired[int]  # VISIBILITY: public
    added_line_count: NotRequired[int]  # VISIBILITY: public
    removed_line_count: NotRequired[int]  # VISIBILITY: public


class SpineDiffRecordCurrent(TypedDict):
    """Diff payload shape for a spine entry of kind ``diff``."""

    diff_id: str  # VISIBILITY: internal
    format: DiffFormat  # VISIBILITY: public
    patch_ref: NotRequired[str]  # VISIBILITY: internal
    patch_digest: NotRequired[str]  # VISIBILITY: restricted
    file_count: NotRequired[int]  # VISIBILITY: public
    files: NotRequired[list[SpineDiffFileRecordCurrent]]  # VISIBILITY: internal
    summary: NotRequired[str]  # VISIBILITY: public


class SpineEntryCurrent(TypedDict):
    """Append-only audit/event entry."""

    entry_id: str  # VISIBILITY: internal
    index: int  # VISIBILITY: public
    kind: SpineEntryKind  # VISIBILITY: public
    timestamp_utc: str  # VISIBILITY: public
    phase: str  # VISIBILITY: public
    status: str  # VISIBILITY: public
    actor: NotRequired[ActorRefCurrent]  # VISIBILITY: internal
    title: NotRequired[str]  # VISIBILITY: public
    summary: NotRequired[str]  # VISIBILITY: public
    contract_ref: NotRequired[ContractRefCurrent]  # VISIBILITY: public
    diff: NotRequired[SpineDiffRecordCurrent]  # VISIBILITY: internal
    artifact_ref: NotRequired[str]  # VISIBILITY: internal
    evidence_ref: NotRequired[str]  # VISIBILITY: internal
    visibility: NotRequired[VisibilityTier]  # VISIBILITY: public


class AyrpackCurrent(TypedDict):
    """Canonical ayrpack_current packet envelope.

    Top-level ``contract_ref`` points to the task-level contract snapshot for
    the packet. Frame-level ``active_frame.contract_ref`` can narrow to the
    run-specific slice that is currently active. Consumers must treat
    ``active_frame.status`` as the lifecycle authority and ``spine`` as audit.
    """

    meta: AyrpackMetaCurrent  # VISIBILITY: public
    header: AyrpackHeaderCurrent  # VISIBILITY: public
    active_frame: ActiveFrameCurrent  # VISIBILITY: public
    spine: list[SpineEntryCurrent]  # VISIBILITY: public
    contract_ref: ContractRefCurrent  # VISIBILITY: public


__all__ = [
    "ActiveFrameKind",
    "ActiveFrameCurrent",
    "ActorRefCurrent",
    "AyrpackHeaderCurrent",
    "AyrpackMetaCurrent",
    "AyrpackCurrent",
    "ContractPointerKind",
    "ContractRefCurrent",
    "DiffFormat",
    "SpineDiffFileRecordCurrent",
    "SpineDiffRecordCurrent",
    "SpineEntryKind",
    "SpineEntryCurrent",
    "VisibilityTier",
]
