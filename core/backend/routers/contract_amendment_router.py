"""Contract amendment gate (Wave 2 build, see evidence/aix-contract-drift/AMENDMENT-DESIGN.md).

Scope grows EXCLUSIVELY through a Lane-approved amendment recorded on the
append-only JSONL chain. The router cannot self-approve: approval is checked
before anything is appended, and a missing approval appends nothing.

New file by design: zero collision with the in-flight recon/router hunks.
Auth follows the CI10 lane pattern (401 on missing header); approval failure
is 403 AMENDMENT_APPROVAL_REQUIRED.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query, status
from pydantic import BaseModel, Field

from backend.aix_core.contract_adapter import (
    ContractNotAdmissible,
    from_execution_dict,
)
from backend.aix_core.contracts.task_contract import (
    ContractAmendment,
    ScopeDelta,
    append_amendment_record,
    apply_amendment,
    read_amendment_chain,
    require_lane_approval,
)

router = APIRouter(
    prefix="/contracts/amendments",
    tags=["contracts"],
)

#: Canonical chain location (workspace <root>/evidence/amendments.jsonl).
DEFAULT_LOG_PATH = Path(__file__).resolve().parents[3] / "evidence" / "amendments.jsonl"

_LOG_PATH = DEFAULT_LOG_PATH

#: Pure-narrowing amendments still require approval by default. Tests flip
#: this to prove the narrowing exception; production stays fail-closed.
REQUIRE_APPROVAL_FOR_NARROWING = True


def configure_log_path(path: str | Path) -> None:
    """Point the chain at another file (tests / ephemeral use)."""
    global _LOG_PATH
    _LOG_PATH = Path(str(path))


def reset_amendment_state() -> None:
    """Restore the canonical log path (tests / ephemeral use)."""
    global _LOG_PATH
    _LOG_PATH = DEFAULT_LOG_PATH


def _require_authorization(authorization: str | None) -> str:
    """Inbound auth (CI10 lane presence-check pattern)."""
    if not authorization or not authorization.strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "CONTRACT_AUTH_REQUIRED",
                "message": "Authorization header required",
            },
        )
    return authorization


class ProposeAmendmentRequest(BaseModel):
    """Body = base execution dict + delta + reason + approver + approval id."""

    contract: dict[str, Any] = Field(description="Prod-shaped execution dict (base scope)")
    delta: ScopeDelta = Field(default_factory=ScopeDelta)
    who: str = ""
    why: str = ""
    lane_approval_id: str = ""
    evidence_path: str = ""
    parent_commit_sha: str = ""


class ProposeAmendmentResponse(BaseModel):
    amendment_id: str
    objective_id: str
    files_in_scope: list[str]
    symbols_in_scope: list[str]
    chain_length: int


def _apply_delta_unchecked(view: Any, delta: ScopeDelta) -> tuple[list[str], list[str]]:
    """Scope math mirroring apply_amendment, for the narrowing exception only.

    Reachable solely for pure-narrowing amendments when
    REQUIRE_APPROVAL_FOR_NARROWING is off. The approved path always uses the
    canonical apply_amendment (which re-gates on approval).
    """
    files = [f for f in view.files_in_scope if f not in set(delta.removed_files)]
    files.extend(f for f in delta.added_files if f not in set(files))
    symbols = [s for s in view.symbols_in_scope if s not in set(delta.removed_symbols)]
    symbols.extend(s for s in delta.added_symbols if s not in set(symbols))
    return files, symbols


def _append_narrowing_record(*, amendment: ContractAmendment) -> None:
    """Append a pure-narrowing record without an approval id.

    Same one-record-per-line format as append_amendment_record, which stays
    the only writer for approval-gated amendments. Callers must prove the
    delta is pure narrowing and the flag allows it before calling.
    """
    _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_LOG_PATH, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(amendment.model_dump(mode="json")) + "\n")


@router.post(
    "",
    response_model=ProposeAmendmentResponse,
    status_code=status.HTTP_200_OK,
    summary="Propose a scope amendment (Lane approval required for growth)",
)
def propose_amendment(
    data: ProposeAmendmentRequest,
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> ProposeAmendmentResponse:
    """Validate the base dict with prod rules, gate on approval, append."""
    _require_authorization(authorization)
    try:
        view = from_execution_dict(data.contract)
    except ContractNotAdmissible as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    except TypeError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "CONTRACT_NOT_A_DICT", "message": str(exc)},
        ) from exc

    delta = data.delta
    grows = bool(delta.added_files or delta.added_symbols)
    amendment = ContractAmendment(
        objective_id=view.objective_id,
        who=data.who,
        why=data.why,
        new_scope_delta=delta,
        evidence_path=data.evidence_path,
        lane_approval_id=data.lane_approval_id,
        parent_commit_sha=data.parent_commit_sha,
    )
    if grows or REQUIRE_APPROVAL_FOR_NARROWING:
        try:
            require_lane_approval(amendment)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "AMENDMENT_APPROVAL_REQUIRED",
                    "message": str(exc),
                },
            ) from exc
        amended = apply_amendment(view, amendment)
        append_amendment_record(log_path=_LOG_PATH, amendment=amendment)
        files, symbols = amended.files_in_scope, amended.symbols_in_scope
    else:
        files, symbols = _apply_delta_unchecked(view, delta)
        _append_narrowing_record(amendment=amendment)
    chain = read_amendment_chain(log_path=_LOG_PATH, objective_id=view.objective_id)
    return ProposeAmendmentResponse(
        amendment_id=amendment.amendment_id,
        objective_id=view.objective_id,
        files_in_scope=files,
        symbols_in_scope=symbols,
        chain_length=len(chain),
    )


@router.get(
    "/export",
    status_code=status.HTTP_200_OK,
    summary="Export the amendment chain for one objective (platform audit)",
    include_in_schema=False,
)
@router.get(
    "",
    status_code=status.HTTP_200_OK,
    summary="Export the amendment chain for one objective (platform audit)",
)
def export_amendments(
    objective_id: str = Query(..., min_length=1, description="Objective to export"),
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> dict[str, Any]:
    """Read API over the same JSONL file the POST path appends to."""
    _require_authorization(authorization)
    chain = read_amendment_chain(log_path=_LOG_PATH, objective_id=objective_id)
    return {"objective_id": objective_id, "amendments": chain, "chain_length": len(chain)}
