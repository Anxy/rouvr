"""AIX-E2E-CYCLE-001A-W07: contract/schema evolution policy.

Only `v1` exists today. This module exists so the *first* breaking change to any
objective-cycle contract has somewhere fail-closed to land, instead of becoming an
ad-hoc in-place field edit on a contract other packets already depend on.

Policy:

1. Within a version, contracts are additive-only: a new optional field is fine: a
   removed field, a renamed field, or a changed required-ness is a new version.
2. A breaking change adds a new version literal to the contract's `version` field and
   registers an upcaster in `UPCASTERS` that maps an old-version payload dict to a
   payload the new version's model can validate. The old version's Python class is not
   deleted; deprecated contracts are kept importable for at least one full version cycle
   after their replacement ships, so already-persisted rows stay loadable.
3. `load_versioned` never guesses: an unknown or newer-than-current version is a fail
   closed `SchemaEvolutionError`, not a best-effort parse.
4. Every registered contract must have at least one fixture in
   `tests/fixtures/objective_cycle/` proving today's canonical payload shape round-trips.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ValidationError

from backend.aix_core.runtime.objective.contracts import (
    CycleOutcomeV1,
    ExecutionCycleV1,
    ObjectiveEnvelopeV1,
    ReplanDecisionV1,
    StepAttemptV1,
    StepContractV1,
)

CURRENT_VERSION = "v1"

CONTRACT_REGISTRY: dict[str, type[BaseModel]] = {
    "ObjectiveEnvelope": ObjectiveEnvelopeV1,
    "ExecutionCycle": ExecutionCycleV1,
    "StepContract": StepContractV1,
    "StepAttempt": StepAttemptV1,
    "ReplanDecision": ReplanDecisionV1,
    "CycleOutcome": CycleOutcomeV1,
}

Upcaster = Callable[[dict[str, Any]], dict[str, Any]]

# Keyed by (contract_name, from_version). Empty until a v2 exists; the shape is fixed now
# so the first breaking change has a registration point instead of an ad-hoc patch.
UPCASTERS: dict[tuple[str, str], Upcaster] = {}


class SchemaEvolutionError(ValueError):
    """Raised when a payload's declared version cannot be safely loaded."""


class UnknownContractError(SchemaEvolutionError):
    pass


class UnsupportedVersionError(SchemaEvolutionError):
    pass


def registered_contract_names() -> tuple[str, ...]:
    return tuple(sorted(CONTRACT_REGISTRY))


def load_versioned(contract_name: str, payload: dict[str, Any]) -> BaseModel:
    """Validate `payload` against `contract_name`'s current version, upcasting first if
    `payload["version"]` names an older, still-supported version.

    Fails closed (raises `SchemaEvolutionError`, never returns a best-effort object) when:
    - `contract_name` is not registered;
    - `payload` has no `version` field;
    - the declared version is newer than `CURRENT_VERSION` (this code is older than the
      data — do not guess how to downcast);
    - the declared version is older than `CURRENT_VERSION` and no upcaster chain reaches
      `CURRENT_VERSION`.
    """
    model_cls = CONTRACT_REGISTRY.get(contract_name)
    if model_cls is None:
        raise UnknownContractError(
            f"unknown contract: {contract_name!r}; registered: {registered_contract_names()}"
        )

    declared_version = payload.get("version")
    if not declared_version:
        raise SchemaEvolutionError(f"{contract_name} payload is missing required 'version' field")

    working = dict(payload)
    version = declared_version
    seen_versions = {version}
    while version != CURRENT_VERSION:
        upcaster = UPCASTERS.get((contract_name, version))
        if upcaster is None:
            raise UnsupportedVersionError(
                f"{contract_name} version {version!r} has no upcast path to {CURRENT_VERSION!r} "
                f"(declared version was {declared_version!r})"
            )
        working = upcaster(working)
        version = working.get("version")
        if not version:
            raise SchemaEvolutionError(f"upcaster for {contract_name} did not set a resulting 'version'")
        if version in seen_versions:
            raise SchemaEvolutionError(f"upcast chain for {contract_name} cycled back to version {version!r}")
        seen_versions.add(version)

    try:
        return model_cls.model_validate(working)
    except ValidationError as exc:
        raise SchemaEvolutionError(f"{contract_name} payload failed validation at {CURRENT_VERSION!r}: {exc}") from exc


__all__ = [
    "CURRENT_VERSION",
    "CONTRACT_REGISTRY",
    "UPCASTERS",
    "Upcaster",
    "SchemaEvolutionError",
    "UnknownContractError",
    "UnsupportedVersionError",
    "registered_contract_names",
    "load_versioned",
]
