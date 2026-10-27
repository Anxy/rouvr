"""Wave 1: TaskContract + ContractAmendment.

The engine may only execute what contract + filesystem authorized.
Scope grows EXCLUSIVELY through a ContractAmendment carrying a Lane
approval id. The router (or any AIX component) cannot self-amend: there is
no code path that mints lane approvals; `apply_amendment` rejects anything
without one. Amendment history is append-only: `apply_amendment` returns a
new contract with the record appended; records are never mutated or removed.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator


class ContractAmendmentRejected(ValueError):
    """Raised when an amendment lacks Lane approval or is otherwise illegal."""


class ScopeDelta(BaseModel):
    added_files: list[str] = Field(default_factory=list)
    removed_files: list[str] = Field(default_factory=list)
    added_symbols: list[str] = Field(default_factory=list)
    removed_symbols: list[str] = Field(default_factory=list)


class TaskContract(BaseModel):
    objective_id: str
    tenant_id: str
    files_in_scope: list[str] = Field(default_factory=list)
    symbols_in_scope: list[str] = Field(default_factory=list)
    verify_commands: list[str] = Field(default_factory=list)
    exit_criteria: list[str] = Field(default_factory=list)
    rollback_plan: str = ""
    commit_sha: str = ""
    created_by: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    amendments: tuple[ContractAmendment, ...] = ()

    @field_validator("objective_id", "tenant_id")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("objective_id and tenant_id are required")
        return text


class ContractAmendment(BaseModel):
    amendment_id: str = Field(default_factory=lambda: f"amd-{uuid4().hex[:12]}")
    objective_id: str
    who: str = ""
    why: str = ""
    new_scope_delta: ScopeDelta = Field(default_factory=ScopeDelta)
    evidence_path: str = ""
    lane_approval_id: str = ""
    parent_commit_sha: str = ""
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


def require_lane_approval(amendment: ContractAmendment) -> str:
    """Return the Lane approval id or raise. Routers cannot self-approve:
    no function in this package mints lane_approval_id values."""
    approval = str(amendment.lane_approval_id or "").strip()
    if not approval:
        raise ContractAmendmentRejected(
            f"amendment {amendment.amendment_id} has no lane_approval_id: "
            "scope growth without recorded Lane approval is rejected"
        )
    return approval


def apply_amendment(contract: TaskContract, amendment: ContractAmendment) -> TaskContract:
    """Grow scope exclusively via an approved amendment. Returns a NEW
    contract with the record appended; the input is untouched (append-only)."""
    require_lane_approval(amendment)
    if amendment.objective_id != contract.objective_id:
        raise ContractAmendmentRejected(
            f"amendment targets {amendment.objective_id}, contract is {contract.objective_id}"
        )
    delta = amendment.new_scope_delta
    files = [f for f in contract.files_in_scope if f not in set(delta.removed_files)]
    files.extend(f for f in delta.added_files if f not in set(files))
    symbols = [s for s in contract.symbols_in_scope if s not in set(delta.removed_symbols)]
    symbols.extend(s for s in delta.added_symbols if s not in set(symbols))
    return contract.model_copy(
        update={
            "files_in_scope": files,
            "symbols_in_scope": symbols,
            "amendments": (*contract.amendments, amendment),
        }
    )


def check_filesystem_scope(
    contract: TaskContract,
    *,
    workspace_root: str | Path,
) -> tuple[bool, list[str]]:
    """Every file in scope must exist on disk and be readable.

    Returns (ok, missing). Missing is non-empty => caller must BLOCK with
    FS_MISMATCH before any model call. `commit_sha` mismatch does not fail
    here; it drives the freshness rule (Wave 0), not scope admission.
    """
    root = Path(str(workspace_root or "").strip() or ".")
    missing: list[str] = []
    for name in contract.files_in_scope:
        text = str(name or "").strip().lstrip("/")
        if not text:
            missing.append(str(name))
            continue
        candidate = root / text
        try:
            ok = candidate.is_file()
            if ok:
                with open(candidate, "rb") as handle:
                    handle.read(1)
        except OSError:
            ok = False
        if not ok:
            missing.append(str(name))
    return (len(missing) == 0, missing)


def append_amendment_record(*, log_path: str | Path, amendment: ContractAmendment) -> None:
    """Append one JSONL record. The log is append-only by construction:
    this function only ever appends; no reader or writer removes entries."""
    require_lane_approval(amendment)
    path = Path(str(log_path))
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(amendment.model_dump(mode="json")) + "\n")


def read_amendment_chain(*, log_path: str | Path, objective_id: str) -> list[dict[str, Any]]:
    """Return the ordered amendment records for one objective (evidence chain)."""
    path = Path(str(log_path))
    if not path.is_file():
        return []
    chain: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict) and record.get("objective_id") == objective_id:
                chain.append(record)
    return chain
