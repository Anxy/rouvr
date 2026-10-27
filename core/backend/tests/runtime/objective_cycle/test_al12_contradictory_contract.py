"""Autonomous Closed-Loop Lane, Packet AL-12 -- Transport Failure Tests, "Contradictory
contract" scenario (sprint doc: 'Objective forbids API changes. Planner proposes API
change. Expected: INVALID_STEP_CONTRACT. Executor never called.').

Reuses `test_w6_governance_scope.py`'s own admit/start/approve fixtures rather than
building a second harness -- this is the same dispatch path W6's scope check already
exercises, just for the planner-refusal branch instead of the scope-violation branch.

Found and fixed a real bug while wiring this: `self._step_contract_errors` (populated on
any planner refusal -- INVALID_STEP_CONTRACT, NEEDS_MORE_EVIDENCE, ...) was never actually
read before `_maybe_real_execute` fell through to `run_real_execute` -- a refused proposal
silently executed anyway with no contract at all. `durable_api_backend.py` now checks it
and blocks, mirroring the scope check immediately below it.
"""

from __future__ import annotations

from typing import Any

import pytest

from backend.aix_core.runtime.objective.durable_api_backend import DurableObjectiveCycleApiBackend
from backend.aix_core.runtime.executor.real_execute import RealExecuteResult
from backend.aix_core.runtime.planner.step_contract_planner import StepContractProposalV1
from backend.tests.runtime.objective_cycle.test_w6_governance_scope import _admit_start_approve


@pytest.mark.asyncio
async def test_contradictory_contract_blocks_with_invalid_step_contract_and_never_executes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executed = {"called": False}

    async def _run_real_execute_should_never_run(**kwargs: Any) -> RealExecuteResult:
        executed["called"] = True
        return RealExecuteResult(
            ok=True, code="REAL_EXECUTE_OK", workspace="ws", evidence_refs=[], workhub_loaded=True,
            artifacts={}, execution_status="COMPLETED", verification_status="PASSED",
            objective_status="COMPLETED", real_operations=1, target_files_modified=1,
        )

    monkeypatch.setattr(
        "backend.aix_core.runtime.objective.durable_api_backend.run_real_execute",
        _run_real_execute_should_never_run,
    )
    monkeypatch.setattr(
        "backend.hyeve_aix_service.config.settings.INTELLIGENCE_LAYER_STEP_CONTRACT_PLANNER_ENABLED", True
    )

    # Simulates the planner model proposing a contradictory step (e.g. an API change
    # against an objective that forbids one) -- StepContractV1's own validation rejects
    # it (empty acceptance_criteria has min_length=1), the same real mechanism
    # step_contract_planner.py's own try/except around StepContractV1(...) hits for any
    # genuinely contradictory/malformed model reply, not a fabricated shortcut.
    async def _fake_propose_contradictory(**kwargs: Any) -> StepContractProposalV1:
        return StepContractProposalV1(
            contract=None,
            reply="I'll add a new public API endpoint to expose this data.",
            reason="INVALID_STEP_CONTRACT:1 validation error for StepContractV1\nacceptance_criteria\n  Tuple should have at least 1 item after validation, not 0",
        )

    monkeypatch.setattr(
        "backend.aix_core.runtime.objective.durable_api_backend.propose_step_contract",
        _fake_propose_contradictory,
    )

    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess", real_execute_enabled=True, require_workhub_load=False, create_workhub_tasks=False,
    )
    result = await _admit_start_approve(backend, seed_understanding=True, allowed_files=("FeeDue.php",))
    approved = result["approved"]

    assert approved["ok"] is False, approved
    assert approved["error"]["code"] == "INVALID_STEP_CONTRACT"
    assert "acceptance_criteria" in approved["error"]["detail"]
    assert executed["called"] is False, "run_real_execute must never be called for a contradictory contract"

    objective_id = result["admit"]["objective_id"]
    assert objective_id not in backend._step_contracts  # noqa: SLF001
    assert objective_id in backend._step_contract_errors  # noqa: SLF001
