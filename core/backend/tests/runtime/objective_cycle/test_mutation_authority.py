"""AIX-UGEF-001 Task 2: mutation with no governed ActionRequest must fail closed,
never fall through classify_intent into a legacy mutation path."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.aix_core.runtime.executor.real_execute import (
    RealExecuteError,
    _actions_fulfill_the_step,
    looks_like_create_empty_directory_objective,
)
from backend.aix_core.runtime.foundation.capability_registry import (
    is_information_operation,
    resolve_capability,
)
from backend.aix_core.runtime.objective.contracts import ActionRequestV1, StepContractV1, BudgetV1


def _budget() -> BudgetV1:
    return BudgetV1(time_seconds=3600, tokens=100_000, cost_usd=5.0, retries=2, steps=10, concurrency=1)


def test_mkdir_objective_detected() -> None:
    assert looks_like_create_empty_directory_objective("Create an empty directory named src/new_module")


def test_mkdir_without_governed_action_is_not_fulfilled() -> None:
    """A step contract with mkdir-shaped purpose but no actions must not be
    treated as fulfilled — it should fail closed, not fall through to legacy."""
    contract = StepContractV1(
        cycle_id="cyc-1",
        tenant_id="t1",
        step_index=0,
        purpose="Create empty directory src/new_module",
        target_scope=("src/new_module/.gitkeep",),
        expected_output_schema_ref="aix.step_contract.v1.generic_edit",
        acceptance_criteria=("directory exists",),
        validation_commands=("test -d src/new_module",),
        assigned_role="executor",
        step_budget=_budget(),
        actions=(),
        status="PENDING",
    )
    assert not _actions_fulfill_the_step(contract)


def test_mkdir_with_governed_action_is_fulfilled() -> None:
    """A properly seeded mkdir contract with ActionRequestV1 IS fulfilled."""
    contract = StepContractV1(
        cycle_id="cyc-1",
        tenant_id="t1",
        step_index=0,
        purpose="Create empty directory src/new_module",
        target_scope=("src/new_module/.gitkeep",),
        expected_output_schema_ref="aix.step_contract.v1.generic_edit",
        acceptance_criteria=("directory exists",),
        validation_commands=("test -d src/new_module",),
        assigned_role="executor",
        step_budget=_budget(),
        actions=(
            ActionRequestV1(
                capability="filesystem",
                operation="mkdir",
                arguments={"path": "src/new_module/.gitkeep", "marker": True},
            ),
        ),
        status="PENDING",
    )
    assert _actions_fulfill_the_step(contract)


def test_unknown_capability_fails_closed() -> None:
    """An unregistered capability must return None from resolve_capability."""
    assert resolve_capability("database", "query") is None
    assert resolve_capability("shell", "run") is None
    assert resolve_capability("git", "push") is None


def test_filesystem_patch_is_mutation_operation() -> None:
    """filesystem.patch is a mutation — it must not bypass authorization."""
    assert not is_information_operation("filesystem", "patch")


def test_filesystem_read_is_information_operation() -> None:
    assert is_information_operation("filesystem", "read")
    assert is_information_operation("filesystem", "inspect")
    assert is_information_operation("filesystem", "search")
