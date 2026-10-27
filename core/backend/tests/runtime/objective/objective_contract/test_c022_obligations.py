"""C022 obligation derivation tests."""

from __future__ import annotations

from backend.aix_core.runtime.objective.objective_contract import understand_objective
from backend.aix_core.runtime.objective.objective_contract.obligation_derivation import (
    assert_coverage,
    derive_from_contract,
)


def test_stable_ids_and_full_coverage() -> None:
    c = understand_objective(
        tenant_id="t",
        raw_objective="Add health endpoint",
        definition_of_done=["GET /health returns 200", "OpenAPI documents /health"],
        allowed_files=["api/health.py"],
    )
    a = derive_from_contract(c)
    b = derive_from_contract(c)
    assert [x.obligation_id for x in a] == [x.obligation_id for x in b]
    assert all(x.proof_required for x in a)
    assert_coverage(c, a)
    assert len(a) >= 3  # 2 outcomes + 1 constraint


def test_each_outcome_has_obligation() -> None:
    c = understand_objective(
        tenant_id="t",
        raw_objective="Ship feature X",
        definition_of_done=["feature X works", "tests pass"],
    )
    obs = derive_from_contract(c)
    statements = {o.statement for o in obs if o.source == "outcome"}
    assert statements == {"feature X works", "tests pass"}
