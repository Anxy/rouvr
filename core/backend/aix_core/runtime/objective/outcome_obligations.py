"""AIX-DoD-002A/B — Compile sealed OutcomeContractV1 into mandatory obligations.

Pure control plane: no LLM, no I/O, no planner.

ID algorithm (deterministic):
  obl_ui_{sanitized_operation}
  obl_api_{sanitized_operation_id}     # '.' → '_'
  obl_db_{sanitized_entity}
  obl_auth_actor_{sanitized_actor}     # only if AUTH.requirement == MANDATORY
  obl_auth_perm_{sanitized_permission} # only if AUTH.requirement == MANDATORY
  obl_map_{sanitized_mapping_id}

  sanitize = lowercase; non [a-z0-9] → '_'; collapse repeats; strip '_'

Emission order (canonical):
  UI ops (sorted) → API ops (sorted by id) → DB entities (sorted)
  → AUTH actors (sorted) → AUTH permissions (sorted)
  → INTEGRATION mappings (sorted by id)

Only MANDATORY facets emit members. OPTIONAL/ABSENT emit nothing.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.aix_core.runtime.objective.outcome_contract import (
    FacetName,
    OutcomeContractV1,
    compute_outcome_contract_hash,
    validate_outcome_contract_structure,
)

ObligationSource = Literal["OUTCOME_CONTRACT_V1"]
ObligationKind = Literal[
    "UI_OPERATION",
    "API_OPERATION",
    "DB_ENTITY",
    "AUTH_ACTOR",
    "AUTH_PERMISSION",
    "INTEGRATION_MAPPING",
]

_SOURCE: ObligationSource = "OUTCOME_CONTRACT_V1"
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


class OutcomeContractObligationV1(BaseModel):
    """One mandatory obligation derived from a sealed OutcomeContractV1 (DoD-002A/B)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    obligation_id: str = Field(min_length=1)
    contract_id: str = Field(min_length=1)
    contract_hash: str = Field(min_length=1)
    facet: FacetName
    kind: ObligationKind
    ref: str = Field(min_length=1)
    mandatory: Literal[True] = True
    acceptance: tuple[str, ...] = ()
    source: ObligationSource = _SOURCE
    # Optional mapping endpoints for INTEGRATION obligations
    from_facet: FacetName | None = None
    from_id: str | None = None
    to_facet: FacetName | None = None
    to_id: str | None = None


@dataclass(frozen=True)
class ObligationCompileResult:
    ok: bool
    code: str
    detail: str
    obligations: tuple[OutcomeContractObligationV1, ...] = ()


def sanitize_obligation_token(raw: str) -> str:
    """Deterministic token for obligation_id segments."""
    text = str(raw or "").casefold().strip()
    text = _NON_ALNUM.sub("_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text or "unnamed"


def obligation_id_for(*, facet: FacetName, kind: ObligationKind, ref: str) -> str:
    token = sanitize_obligation_token(ref)
    if kind == "UI_OPERATION":
        return f"obl_ui_{token}"
    if kind == "API_OPERATION":
        return f"obl_api_{token}"
    if kind == "DB_ENTITY":
        return f"obl_db_{token}"
    if kind == "AUTH_ACTOR":
        return f"obl_auth_actor_{token}"
    if kind == "AUTH_PERMISSION":
        return f"obl_auth_perm_{token}"
    if kind == "INTEGRATION_MAPPING":
        return f"obl_map_{token}"
    raise ValueError(f"unknown obligation kind: {kind}")


def compile_outcome_obligations(contract: OutcomeContractV1) -> ObligationCompileResult:
    """Derive mandatory obligations from a sealed OutcomeContractV1. Fail closed."""
    if contract is None:  # type: ignore[comparison-overlap]
        return ObligationCompileResult(
            ok=False,
            code="OUTCOME_CONTRACT_REQUIRED",
            detail="OutcomeContractV1 is required",
        )

    hash_value = str(contract.contract_hash or "").strip()
    if not hash_value:
        return ObligationCompileResult(
            ok=False,
            code="CONTRACT_UNSEALED",
            detail="OutcomeContractV1 must be sealed (contract_hash) before obligations compile",
        )

    expected = compute_outcome_contract_hash(contract)
    if hash_value != expected:
        return ObligationCompileResult(
            ok=False,
            code="CONTRACT_HASH_MISMATCH",
            detail=f"contract_hash mismatch: expected {expected}, got {hash_value}",
        )

    struct_ok, struct_code, struct_details = validate_outcome_contract_structure(contract)
    if not struct_ok:
        return ObligationCompileResult(
            ok=False,
            code=struct_code,
            detail="; ".join(struct_details) or "outcome contract invalid",
        )

    facets = contract.facets
    out: list[OutcomeContractObligationV1] = []
    cid = contract.contract_id
    chash = hash_value

    if facets.UI.requirement == "MANDATORY":
        ui_ac = tuple(facets.UI.acceptance_criteria)
        for op in sorted(facets.UI.required_operations):
            out.append(
                OutcomeContractObligationV1(
                    obligation_id=obligation_id_for(facet="UI", kind="UI_OPERATION", ref=op),
                    contract_id=cid,
                    contract_hash=chash,
                    facet="UI",
                    kind="UI_OPERATION",
                    ref=op,
                    acceptance=ui_ac,
                )
            )

    if facets.API.requirement == "MANDATORY":
        api_ac = tuple(facets.API.acceptance_criteria)
        for op in sorted(facets.API.required_operations, key=lambda o: o.id):
            out.append(
                OutcomeContractObligationV1(
                    obligation_id=obligation_id_for(facet="API", kind="API_OPERATION", ref=op.id),
                    contract_id=cid,
                    contract_hash=chash,
                    facet="API",
                    kind="API_OPERATION",
                    ref=op.id,
                    acceptance=api_ac,
                )
            )

    if facets.DB.requirement == "MANDATORY":
        db_ac = tuple(facets.DB.acceptance_criteria)
        for entity in sorted(facets.DB.entities):
            out.append(
                OutcomeContractObligationV1(
                    obligation_id=obligation_id_for(facet="DB", kind="DB_ENTITY", ref=entity),
                    contract_id=cid,
                    contract_hash=chash,
                    facet="DB",
                    kind="DB_ENTITY",
                    ref=entity,
                    acceptance=db_ac,
                )
            )

    if facets.AUTH.requirement == "MANDATORY":
        auth_ac = tuple(facets.AUTH.acceptance_criteria)
        for actor in sorted(facets.AUTH.actors):
            out.append(
                OutcomeContractObligationV1(
                    obligation_id=obligation_id_for(facet="AUTH", kind="AUTH_ACTOR", ref=actor),
                    contract_id=cid,
                    contract_hash=chash,
                    facet="AUTH",
                    kind="AUTH_ACTOR",
                    ref=actor,
                    acceptance=auth_ac,
                )
            )
        for perm in sorted(facets.AUTH.permissions):
            out.append(
                OutcomeContractObligationV1(
                    obligation_id=obligation_id_for(facet="AUTH", kind="AUTH_PERMISSION", ref=perm),
                    contract_id=cid,
                    contract_hash=chash,
                    facet="AUTH",
                    kind="AUTH_PERMISSION",
                    ref=perm,
                    acceptance=auth_ac,
                )
            )

    if facets.INTEGRATION.requirement == "MANDATORY":
        int_ac = tuple(facets.INTEGRATION.acceptance_criteria)
        for mapping in sorted(facets.INTEGRATION.mappings, key=lambda m: m.id):
            out.append(
                OutcomeContractObligationV1(
                    obligation_id=obligation_id_for(
                        facet="INTEGRATION", kind="INTEGRATION_MAPPING", ref=mapping.id
                    ),
                    contract_id=cid,
                    contract_hash=chash,
                    facet="INTEGRATION",
                    kind="INTEGRATION_MAPPING",
                    ref=mapping.id,
                    acceptance=int_ac,
                    from_facet=mapping.from_facet,
                    from_id=mapping.from_id,
                    to_facet=mapping.to_facet,
                    to_id=mapping.to_id,
                )
            )

    # Deduplicate by obligation_id while preserving order (fail closed on collision mismatch)
    seen: dict[str, OutcomeContractObligationV1] = {}
    for obl in out:
        prior = seen.get(obl.obligation_id)
        if prior is not None and prior.ref != obl.ref:
            return ObligationCompileResult(
                ok=False,
                code="CONTRACT_INVALID",
                detail=f"duplicate obligation_id {obl.obligation_id!r} for distinct refs",
            )
        if prior is None:
            seen[obl.obligation_id] = obl

    obligations = tuple(seen.values())
    return ObligationCompileResult(
        ok=True,
        code="OBLIGATIONS_COMPILED",
        detail=f"compiled {len(obligations)} mandatory obligations",
        obligations=obligations,
    )


def obligations_for_planner(
    obligations: tuple[OutcomeContractObligationV1, ...] | list[OutcomeContractObligationV1],
) -> list[dict[str, object]]:
    """Compact authoritative obligation list for planner Evidence bags (DoD-002C)."""
    out: list[dict[str, object]] = []
    for obl in obligations:
        out.append(
            {
                "id": obl.obligation_id,
                "facet": obl.facet,
                "kind": obl.kind,
                "ref": obl.ref,
                "mandatory": True,
                "contract_id": obl.contract_id,
                "contract_hash": obl.contract_hash,
            }
        )
    return out


__all__ = [
    "ObligationSource",
    "ObligationKind",
    "OutcomeContractObligationV1",
    "ObligationCompileResult",
    "sanitize_obligation_token",
    "obligation_id_for",
    "compile_outcome_obligations",
    "obligations_for_planner",
]
