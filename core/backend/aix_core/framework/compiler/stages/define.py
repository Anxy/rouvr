"""AIX Framework Compiler - Stage 2: Outcome & DoD Extraction.

Sprint: F10 Work Compiler (F10-P1)
Grounded in: F10-COMPILER-STAGE-CONTRACTS.md, OutcomeContractV1.
"""

from __future__ import annotations

import hashlib
from typing import Any
from pydantic import BaseModel, ConfigDict, Field

from backend.aix_core.framework.compiler.stages.intake import CompilerIntakeSnapshot
from backend.aix_core.runtime.objective.outcome_contract import (
    ApiOperationSpecV1,
    ApiFacetV1,
    DbFacetV1,
    IntegrationFacetV1,
    IntegrationMappingV1,
    OutcomeContractV1,
    OutcomeFacetsV1,
    UiFacetV1,
    seal_outcome_contract,
)


class DefinedOutcome(BaseModel):
    """Strongly-typed extraction of outcome contract and obligations."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    outcome_contract: OutcomeContractV1
    definition_hash: str


class DefineStage:
    """Stage 2: Derives and seals formal OutcomeContract from intake snapshot."""

    @classmethod
    def run(
        cls,
        intake: CompilerIntakeSnapshot,
        *,
        definition_of_done: tuple[str, ...] | None = None,
        repository_id: str = "workspace",
        allowed_files: tuple[str, ...] | None = None,
        facets: OutcomeFacetsV1 | None = None,
    ) -> DefinedOutcome:
        dods = definition_of_done if definition_of_done is not None else intake.definition_of_done
        files = allowed_files if allowed_files is not None else intake.allowed_files

        if facets is None:
            facets = OutcomeFacetsV1(
                UI=UiFacetV1(
                    required_operations=("view_main", "submit_action"),
                    required_views=("main_view", "action_view"),
                    acceptance_criteria=dods,
                ),
                API=ApiFacetV1(
                    required_operations=(
                        ApiOperationSpecV1(id="resource.get", path_or_action="GET /api/v1/resource"),
                        ApiOperationSpecV1(id="resource.post", path_or_action="POST /api/v1/resource"),
                    ),
                    acceptance_criteria=dods,
                ),
                DB=DbFacetV1(
                    entities=("resources",),
                    acceptance_criteria=dods,
                ),
                INTEGRATION=IntegrationFacetV1(
                    mappings=(
                        IntegrationMappingV1(
                            id="map-1",
                            from_facet="UI",
                            from_id="view_main",
                            to_facet="API",
                            to_id="resource.get",
                        ),
                        IntegrationMappingV1(
                            id="map-2",
                            from_facet="UI",
                            from_id="submit_action",
                            to_facet="API",
                            to_id="resource.post",
                        ),
                        IntegrationMappingV1(
                            id="map-3",
                            from_facet="API",
                            from_id="resource.get",
                            to_facet="DB",
                            to_id="resources",
                        ),
                        IntegrationMappingV1(
                            id="map-4",
                            from_facet="API",
                            from_id="resource.post",
                            to_facet="DB",
                            to_id="resources",
                        ),
                    ),
                    acceptance_criteria=dods,
                ),
            )

        contract = OutcomeContractV1(
            contract_id=f"contract_{intake.objective_id}",
            objective_id=intake.objective_id,
            definition_of_done=dods,
            facets=facets,
            forbidden_effects=intake.forbidden_effects,
            scope=files,
        )
        sealed_contract = seal_outcome_contract(contract)

        hasher = hashlib.sha256()
        hasher.update(sealed_contract.contract_hash.encode("utf-8"))
        definition_hash = hasher.hexdigest()

        return DefinedOutcome(
            outcome_contract=sealed_contract,
            definition_hash=definition_hash,
        )
