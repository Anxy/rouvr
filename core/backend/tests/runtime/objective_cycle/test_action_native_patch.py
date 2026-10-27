"""AIX-UGEF P6: planner-emitted filesystem.patch actions bypass classify_intent
and route through the governed patch pipeline."""

from __future__ import annotations

from backend.aix_core.runtime.executor.real_execute import (
    _actions_fulfill_the_step,
)
from backend.aix_core.runtime.objective.contracts import (
    ActionRequestV1,
    BudgetV1,
    RequiredEffectV1,
    StepContractV1,
)


def _budget() -> BudgetV1:
    return BudgetV1(time_seconds=3600, tokens=100_000, cost_usd=5.0, retries=2, steps=10, concurrency=1)


def test_patch_action_does_not_fulfill_step() -> None:
    """filesystem.patch actions need the multi-step pipeline and should NOT
    be dispatched through the simple execute_action_loop."""
    contract = StepContractV1(
        cycle_id="cyc-1",
        tenant_id="t1",
        step_index=0,
        purpose="Replace deprecated API calls with new version",
        target_scope=("src/api_client.py",),
        expected_output_schema_ref="aix.step_contract.v1.generic_edit",
        acceptance_criteria=("all deprecated calls replaced",),
        validation_commands=("python -m pytest tests/test_api.py",),
        assigned_role="executor",
        step_budget=_budget(),
        actions=(
            ActionRequestV1(
                capability="filesystem",
                operation="patch",
                arguments={"paths": ["src/api_client.py"]},
            ),
        ),
        required_effects=(
            RequiredEffectV1(kind="LITERAL_REPLACE"),
        ),
        status="PENDING",
    )
    assert not _actions_fulfill_the_step(contract)


def test_mkdir_action_still_fulfills_step() -> None:
    """mkdir actions should still fulfill steps through the simple executor."""
    contract = StepContractV1(
        cycle_id="cyc-1",
        tenant_id="t1",
        step_index=0,
        purpose="Create src/new_module directory",
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


def test_mixed_read_and_patch_actions_do_not_fulfill() -> None:
    """Steps with both read and patch actions need the multi-step pipeline."""
    contract = StepContractV1(
        cycle_id="cyc-1",
        tenant_id="t1",
        step_index=0,
        purpose="Read config then update settings",
        target_scope=("config.json", "src/settings.py"),
        expected_output_schema_ref="aix.step_contract.v1.generic_edit",
        acceptance_criteria=("settings updated",),
        validation_commands=("python -m pytest tests/",),
        assigned_role="executor",
        step_budget=_budget(),
        actions=(
            ActionRequestV1(
                capability="filesystem",
                operation="read",
                arguments={"path": "config.json"},
            ),
            ActionRequestV1(
                capability="filesystem",
                operation="patch",
                arguments={"paths": ["src/settings.py"]},
            ),
        ),
        required_effects=(
            RequiredEffectV1(kind="LITERAL_REPLACE"),
        ),
        status="PENDING",
    )
    assert not _actions_fulfill_the_step(contract)


def test_rename_action_still_fulfills_step() -> None:
    """Rename actions should still fulfill steps."""
    contract = StepContractV1(
        cycle_id="cyc-1",
        tenant_id="t1",
        step_index=0,
        purpose="Rename old_module to new_module",
        target_scope=("old_module.py", "new_module.py"),
        expected_output_schema_ref="aix.step_contract.v1.generic_edit",
        acceptance_criteria=("file renamed",),
        validation_commands=("test -f new_module.py",),
        assigned_role="executor",
        step_budget=_budget(),
        actions=(
            ActionRequestV1(
                capability="filesystem",
                operation="rename",
                arguments={"source": "old_module.py", "destination": "new_module.py"},
            ),
        ),
        status="PENDING",
    )
    assert _actions_fulfill_the_step(contract)


def test_planner_parse_actions_accepts_filesystem_patch() -> None:
    """The planner action parser should accept filesystem.patch."""
    from backend.aix_core.runtime.planner.step_contract_planner import _parse_actions

    actions = _parse_actions([
        {
            "capability": "filesystem",
            "operation": "patch",
            "arguments": {"paths": ["src/main.py"]},
            "expected_observation": {"changed": True},
        }
    ])
    assert len(actions) == 1
    assert actions[0].capability == "filesystem"
    assert actions[0].operation == "patch"
    assert actions[0].arguments == {"paths": ["src/main.py"]}
