"""rouvr/contracts.py — dataclasses passed through Temporal (must be JSON-serialisable)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Verdict = Literal["approve", "request_changes", "reject"]


@dataclass
class TaskWorkflowInput:
    tenant_id: str
    project_id: str
    task_id: str
    contract_rev: str
    requested_by: str
    max_rework: int = 2
    assign_start_to_close_s: int = 1800
    spec_timeout_s: int = 1500  # ExecSpec.timeout_s: one container run; test activity runs two


@dataclass
class Binding:
    task_id: str
    coder_persona: str
    reviewer_persona: str
    contract_rev: str
    record_version: int


@dataclass
class Lease:
    task_id: str
    holder: str
    lease_epoch: int
    attempt_no: int
    attempt_id: str
    expires_at: str  # ISO-8601 UTC
    assignment_id: str = ""  # stage attempts key off the assignment


@dataclass
class AssignInput:
    wf: TaskWorkflowInput
    binding: Binding
    attempt_no: int
    rework_notes: str = ""
    resume_session_id: str | None = None


@dataclass
class AssignResult:
    attempt_id: str
    lease_epoch: int
    muse_session_id: str
    branch: str
    pr_url: str
    head_sha: str
    evidence_ids: list[str] = field(default_factory=list)


@dataclass
class TestResult:
    run_id: str
    passed: bool
    summary: str
    evidence_id: str


@dataclass
class VerifyResult:
    verdict: Verdict
    notes: str
    reviewer_principal: str
    evidence_id: str


@dataclass
class CloseResult:
    final_state: str
    record_version: int
    receipt_digest: str | None = None


@dataclass
class OpsInput:
    run_key: str  # e.g. morning-report:2026-10-30, the idempotency scope
    instruction: str


@dataclass
class OpsResult:
    state: str  # done | blocked | failed
    digest: str  # exception-only: "Done. No action needed." when clean
    task_id: str
    message_id: str = ""
    sink: str = ""
