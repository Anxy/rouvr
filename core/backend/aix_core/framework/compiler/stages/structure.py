"""AIX Framework Compiler - Stage 3: Product Structure Decomposition (PD-003A).

Sprint: F10 Work Compiler (F10-P1)
Grounded in: F10-COMPILER-STAGE-CONTRACTS.md, product_structure.py.
"""

from __future__ import annotations

import hashlib
from typing import Any
from pydantic import BaseModel, ConfigDict, Field

from backend.aix_core.framework.compiler.stages.define import DefinedOutcome
from backend.aix_core.runtime.objective.product_structure import (
    ProductStructureV1,
    compile_product_structure,
)


class ProductStructureDecomposition(BaseModel):
    """Decomposed structural representation of the work scope."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    structure_id: str
    objective_id: str
    product_structure: ProductStructureV1
    structure_hash: str


class StructureStage:
    """Stage 3: Decomposes outcome into structural facets, entities, and operations."""

    @classmethod
    def run(cls, outcome: DefinedOutcome) -> ProductStructureDecomposition:
        contract = outcome.outcome_contract
        result = compile_product_structure(contract)
        if not result.ok or result.structure is None:
            raise ValueError(f"ProductStructure compilation failed: {result.code} - {result.detail}")

        structure = result.structure
        hasher = hashlib.sha256()
        hasher.update(structure.structure_id.encode("utf-8"))
        structure_hash = hasher.hexdigest()

        return ProductStructureDecomposition(
            structure_id=structure.structure_id,
            objective_id=contract.objective_id,
            product_structure=structure,
            structure_hash=structure_hash,
        )
