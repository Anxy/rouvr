"""OutcomeContractV1 — sealed, outcome-centric definition of done.

Must exist and validate before decomposition is treated as authoritative.
Pure data + deterministic hashing/validation. No I/O, no LLM.
"""
from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.aix_core.runtime.objective.ulid_compat import new_ulid

CONTRACT_VERSION = "v1"

FacetName = Literal["UI", "API", "DB", "AUTH", "INTEGRATION"]
FacetRequirement = Literal["MANDATORY", "OPTIONAL", "ABSENT"]
BindingRole = Literal["IMPLEMENT", "VERIFY", "IMPLEMENT_AND_VERIFY"]
IntegrationProofResult = Literal[
    "INTEGRATION_PROVEN_STATIC",
    "INTEGRATION_FAILED",
    "INTEGRATION_SKIPPED_NO_CONTRACT",
]
ObjectiveProvenResult = Literal[
    "COMPLETED_FOR_REVIEW",
    "BLOCKED",
    "FAILED",
]


def _uuid() -> str:
    return new_ulid()


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ApiOperationSpecV1(_Frozen):
    id: str = Field(min_length=1)
    method: str | None = None
    path_or_action: str = Field(min_length=1)
    inputs: tuple[str, ...] = ()
    outputs: tuple[str, ...] = ()


class UiFacetV1(_Frozen):
    required_operations: tuple[str, ...] = ()
    required_views: tuple[str, ...] = ()
    acceptance_criteria: tuple[str, ...] = ()
    requirement: FacetRequirement = "MANDATORY"


class ApiFacetV1(_Frozen):
    required_operations: tuple[ApiOperationSpecV1, ...] = ()
    acceptance_criteria: tuple[str, ...] = ()
    requirement: FacetRequirement = "MANDATORY"


class DbFacetV1(_Frozen):
    entities: tuple[str, ...] = ()
    fields: tuple[str, ...] = ()
    relationships: tuple[str, ...] = ()
    acceptance_criteria: tuple[str, ...] = ()
    requirement: FacetRequirement = "MANDATORY"


class AuthFacetV1(_Frozen):
    actors: tuple[str, ...] = ()
    permissions: tuple[str, ...] = ()
    acceptance_criteria: tuple[str, ...] = ()
    requirement: FacetRequirement = "OPTIONAL"


class IntegrationMappingV1(_Frozen):
    id: str = Field(default_factory=_uuid)
    from_facet: FacetName
    from_id: str = Field(min_length=1)
    to_facet: FacetName
    to_id: str = Field(min_length=1)


class IntegrationFacetV1(_Frozen):
    dependencies: tuple[str, ...] = ()
    mappings: tuple[IntegrationMappingV1, ...] = ()
    acceptance_criteria: tuple[str, ...] = ()
    requirement: FacetRequirement = "MANDATORY"


class OutcomeFacetsV1(_Frozen):
    UI: UiFacetV1 = Field(default_factory=UiFacetV1)
    API: ApiFacetV1 = Field(default_factory=ApiFacetV1)
    DB: DbFacetV1 = Field(default_factory=DbFacetV1)
    AUTH: AuthFacetV1 = Field(default_factory=AuthFacetV1)
    INTEGRATION: IntegrationFacetV1 = Field(default_factory=IntegrationFacetV1)


class OutcomeContractV1(_Frozen):
    """Sealed outcome contract. Create and validate before authoritative decomposition.

    Use `seal_outcome_contract(...)` so `contract_hash` is populated. A non-empty
    hash is verified against the canonical body on construction.
    """

    version: Literal["v1"] = CONTRACT_VERSION
    contract_id: str = Field(default_factory=_uuid)
    objective_id: str = Field(min_length=1)
    definition_of_done: tuple[str, ...] = Field(min_length=1)
    facets: OutcomeFacetsV1
    constraints: tuple[str, ...] = ()
    forbidden_effects: tuple[str, ...] = ()
    scope: tuple[str, ...] = ()
    contract_hash: str = ""

    @model_validator(mode="after")
    def _check_hash_when_present(self) -> "OutcomeContractV1":
        if not self.contract_hash:
            return self
        expected = compute_outcome_contract_hash(self)
        if self.contract_hash != expected:
            raise ValueError(
                f"CONTRACT_HASH_MISMATCH: expected {expected}, got {self.contract_hash}"
            )
        return self


def compute_outcome_contract_hash(contract: OutcomeContractV1) -> str:
    """Deterministic hash over contract body excluding contract_hash itself."""
    payload = contract.model_dump(mode="json")
    payload.pop("contract_hash", None)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def seal_outcome_contract(contract: OutcomeContractV1) -> OutcomeContractV1:
    """Return a copy with contract_hash set (authoritative seal before decomposition)."""
    digest = compute_outcome_contract_hash(contract)
    return contract.model_copy(update={"contract_hash": digest})


def validate_outcome_contract_structure(
    contract: OutcomeContractV1,
) -> tuple[bool, str, tuple[str, ...]]:
    """Structural fail-closed checks before decomposition may be authoritative.

    Returns (ok, code, details).
    """
    details: list[str] = []
    facets = contract.facets

    if facets.UI.requirement == "MANDATORY" and not facets.UI.required_operations:
        details.append("UI facet MANDATORY but required_operations empty")
    if facets.API.requirement == "MANDATORY" and not facets.API.required_operations:
        details.append("API facet MANDATORY but required_operations empty")
    if facets.DB.requirement == "MANDATORY" and not facets.DB.entities:
        details.append("DB facet MANDATORY but entities empty")
    if facets.INTEGRATION.requirement == "MANDATORY" and not facets.INTEGRATION.mappings:
        details.append("INTEGRATION facet MANDATORY but mappings empty")

    api_ids = {op.id for op in facets.API.required_operations}
    ui_ops = set(facets.UI.required_operations)
    db_entities = set(facets.DB.entities)

    for mapping in facets.INTEGRATION.mappings:
        if mapping.from_facet == "UI" and mapping.from_id not in ui_ops:
            details.append(f"mapping {mapping.id}: unknown UI op {mapping.from_id!r}")
        if mapping.to_facet == "API" and mapping.to_id not in api_ids:
            details.append(f"mapping {mapping.id}: unknown API op {mapping.to_id!r}")
        if mapping.from_facet == "API" and mapping.from_id not in api_ids:
            details.append(f"mapping {mapping.id}: unknown API op {mapping.from_id!r}")
        if mapping.to_facet == "DB" and mapping.to_id not in db_entities:
            details.append(f"mapping {mapping.id}: unknown DB entity {mapping.to_id!r}")

    if details:
        return False, "CONTRACT_INVALID", tuple(details)
    return True, "CONTRACT_VALID", ()


__all__ = [
    "CONTRACT_VERSION",
    "FacetName",
    "FacetRequirement",
    "BindingRole",
    "IntegrationProofResult",
    "ObjectiveProvenResult",
    "ApiOperationSpecV1",
    "UiFacetV1",
    "ApiFacetV1",
    "DbFacetV1",
    "AuthFacetV1",
    "IntegrationMappingV1",
    "IntegrationFacetV1",
    "OutcomeFacetsV1",
    "OutcomeContractV1",
    "compute_outcome_contract_hash",
    "seal_outcome_contract",
    "validate_outcome_contract_structure",
]
