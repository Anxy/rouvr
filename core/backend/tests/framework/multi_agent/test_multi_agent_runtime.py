"""Tests for F10-P4 Multi-Agent Runtime & Governance."""

from __future__ import annotations

import pytest

from backend.aix_core.framework.agent.models import Role
from backend.aix_core.framework.multi_agent.conflict import ConflictDetector, ScopeCollision
from backend.aix_core.framework.multi_agent.delegation import DelegationManager
from backend.aix_core.framework.multi_agent.handoff import (
    HandoffAuthorizationError,
    MultiAgentHandoffProtocol,
)
from backend.aix_core.framework.multi_agent.models import (
    ActionObservationV1,
    MultiAgentHandoffRequest,
    MultiAgentHandoffResult,
    StepContextCapsuleV1,
)
from backend.aix_core.framework.multi_agent.sync import ScopeLockConflictError, ScopeLockManager
from backend.aix_core.framework.work.models import (
    UnitStatus,
    WorkContract,
    WorkGraph,
    WorkUnit,
)
from backend.aix_core.runtime.agents.handoff import (
    HandoffError,
    HandoffRequestV1,
    accept_handoff,
    complete_handoff,
    fail_handoff,
    validate_handoff,
)


def test_capsule_and_observation_hashing():
    capsule = StepContextCapsuleV1(
        unit_id="unit_1",
        work_id="work_1",
        assigned_role=Role.EXECUTOR_CODER,
        purpose="Implement login route",
        target_scope=("src/login.py",),
    )
    chash = capsule.compute_hash()
    assert len(chash) == 64

    obs = ActionObservationV1(
        capsule_id=capsule.capsule_id,
        unit_id="unit_1",
        status="SUCCESS",
        touched_files=("src/login.py",),
    )
    ohash = obs.compute_hash()
    assert len(ohash) == 64


def test_scope_lock_manager_lifecycle():
    lock_mgr = ScopeLockManager()

    # Acquire lock on scope
    granted = lock_mgr.acquire_scope_locks("unit_1", ("src/auth.py", "src/login.py"))
    assert granted == ("src/auth.py", "src/login.py")
    assert lock_mgr.is_scope_locked("src/auth.py") is True
    assert lock_mgr.get_holder("src/auth.py") == "unit_1"

    # Same unit acquiring again should succeed
    lock_mgr.acquire_scope_locks("unit_1", ("src/auth.py",))

    # Conflicting unit acquiring should raise ScopeLockConflictError
    with pytest.raises(ScopeLockConflictError):
        lock_mgr.acquire_scope_locks("unit_2", ("src/auth.py",))

    # Release locks
    released = lock_mgr.release_scope_locks("unit_1")
    assert set(released) == {"src/auth.py", "src/login.py"}
    assert lock_mgr.is_scope_locked("src/auth.py") is False

    # Now unit_2 can acquire
    lock_mgr.acquire_scope_locks("unit_2", ("src/auth.py",))
    assert lock_mgr.get_holder("src/auth.py") == "unit_2"


def test_conflict_detector():
    c1 = WorkContract(unit_id="u1", assigned_role="executor")
    c2 = WorkContract(unit_id="u2", assigned_role="executor")
    c3 = WorkContract(unit_id="u3", assigned_role="executor")

    u1 = WorkUnit(
        unit_id="u1",
        work_id="w1",
        unit_index=1,
        purpose="Step 1",
        target_scope=("src/shared.py",),
        contract=c1,
    )
    u2 = WorkUnit(
        unit_id="u2",
        work_id="w1",
        unit_index=2,
        purpose="Step 2 (parallel conflict with u1)",
        target_scope=("src/shared.py",),
        contract=c2,
    )
    u3 = WorkUnit(
        unit_id="u3",
        work_id="w1",
        unit_index=3,
        purpose="Step 3 (depends on u1)",
        depends_on=("u1",),
        target_scope=("src/shared.py",),
        contract=c3,
    )

    # Graph with parallel conflict between u1 and u2
    conflict_graph = WorkGraph(
        graph_id="g1",
        work_id="w1",
        units=(u1, u2),
    )
    collisions = ConflictDetector.detect_graph_collisions(conflict_graph)
    assert len(collisions) == 1
    assert collisions[0].unit_a == "u1"
    assert collisions[0].unit_b == "u2"
    assert collisions[0].overlapping_scopes == ("src/shared.py",)

    # Graph with dependency between u1 and u3 (no conflict since ordered)
    ordered_graph = WorkGraph(
        graph_id="g2",
        work_id="w1",
        units=(u1, u3),
    )
    ordered_collisions = ConflictDetector.detect_graph_collisions(ordered_graph)
    assert len(ordered_collisions) == 0


def test_delegation_manager():
    lock_mgr = ScopeLockManager()
    delegator = DelegationManager(lock_mgr)

    contract = WorkContract(unit_id="unit_10", assigned_role="executor")
    unit = WorkUnit(
        unit_id="unit_10",
        work_id="work_10",
        unit_index=1,
        purpose="Implement auth helper",
        target_scope=("src/auth_helper.py",),
        contract=contract,
    )

    capsule = delegator.prepare_capsule(unit, Role.EXECUTOR_CODER)
    assert capsule.unit_id == "unit_10"
    assert capsule.capsule_hash != ""

    with pytest.raises(NotImplementedError, match="F4/OC"):
        delegator.execute_delegated_unit(capsule)
    # Capsule path remains; no second executor
    assert lock_mgr.is_scope_locked("src/auth_helper.py") is False


def test_multi_agent_handoff_protocol():
    capsule = StepContextCapsuleV1(
        unit_id="u_h1",
        work_id="w_h1",
        assigned_role=Role.EXECUTOR_CODER,
        purpose="Execute task",
        target_scope=("src/main.py",),
    )

    # Authorized: Planner -> Executor
    req_valid = MultiAgentHandoffRequest(
        from_role=Role.PLANNER,
        to_role=Role.EXECUTOR_CODER,
        unit_id="u_h1",
        work_id="w_h1",
        context_capsule=capsule,
    )
    res_valid = MultiAgentHandoffProtocol.process_handoff(req_valid)
    assert res_valid.status == "ACCEPTED"

    # Unauthorized: Reviewer -> Executor
    req_invalid = MultiAgentHandoffRequest(
        from_role=Role.REVIEW_PACKET_CURATOR,
        to_role=Role.EXECUTOR_CODER,
        unit_id="u_h1",
        work_id="w_h1",
        context_capsule=capsule,
    )
    with pytest.raises(HandoffAuthorizationError):
        MultiAgentHandoffProtocol.process_handoff(req_invalid)


def test_legacy_runtime_handoff_compatibility():
    req = HandoffRequestV1(
        handoff_id="h1",
        from_agent_id="agent_planner",
        from_role="planner",
        to_role="coder",
        cycle_id="cycle_1",
        step_id="step_1",
        objective="Write auth code",
    )
    validate_handoff(req)

    res_accept = accept_handoff(req, "agent_coder")
    assert res_accept.status == "ACCEPTED"

    res_complete = complete_handoff(req, "agent_coder", outputs={"code": "done"})
    assert res_complete.status == "COMPLETED"

    res_fail = fail_handoff(req, "agent_coder", error_code="SYNTAX_ERROR")
    assert res_fail.status == "FAILED"
