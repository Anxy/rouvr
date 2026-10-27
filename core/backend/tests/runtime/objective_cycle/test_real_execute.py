"""001P-W11: real objective-cycle execute tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.aix_core.runtime.objective.durable_api_backend import DurableObjectiveCycleApiBackend
from backend.aix_core.runtime.executor.openrouter_codegen import HtmlCssBundle, local_fallback_html_css_bundle
from backend.aix_core.runtime.executor.real_execute import (
    RealExecuteError,
    _actions_fulfill_the_step,
    _extract_symbol_name_from_objective,
    run_real_execute,
)
from backend.aix_core.runtime.objective.contracts import (
    ActionRequestV1,
    BudgetV1,
    RequiredEffectV1,
    StepContractV1,
)

_WS_ROOT = Path(r"D:\v2.1\AIX\.tmp\aix-cycle-test-workspaces")


def test_symbol_extraction_ignores_generic_contract_prose() -> None:
    assert (
        _extract_symbol_name_from_objective(
            "Add an audit helper without changing existing service function interfaces."
        )
        is None
    )
    assert (
        _extract_symbol_name_from_objective("Modify function normalize_user_email safely.")
        == "normalize_user_email"
    )
    assert (
        _extract_symbol_name_from_objective(
            "Read README_A.txt. Do not modify the README or any other repository file."
        )
        is None
    )
    assert (
        _extract_symbol_name_from_objective(
            "Resolve normalize_user_email as one symbol in service.py exactly once."
        )
        == "normalize_user_email"
    )
    assert (
        _extract_symbol_name_from_objective(
            "Perform the symbol-scoped edit for normalize_user_email in service.py."
        )
        == "normalize_user_email"
    )
    assert _extract_symbol_name_from_objective("Do not modify files outside config.json.") is None
    assert (
        _extract_symbol_name_from_objective(
            "Correct the behavior so the existing add function satisfies the test."
        )
        is None
    )
    # Live refactor sprint: path after "refactor of" must not become EDIT_SYMBOL.
    assert (
        _extract_symbol_name_from_objective(
            "Controlled refactor of backend/aix_core/runtime/objective/durable_api_backend.py "
            "into a thin orchestration coordinator."
        )
        is None
    )
    assert (
        _extract_symbol_name_from_objective(
            "refactor of backend\\aix_core\\runtime\\objective\\durable_api_backend.py"
        )
        is None
    )


def _gaef_step(*, actions, effects) -> StepContractV1:
    return StepContractV1(
        cycle_id="cyc-1",
        tenant_id="tenant-1",
        step_index=0,
        purpose="Diagnose and fix the calculator bug",
        target_scope=("gaef001-scratch/calc_f.py",),
        expected_output_schema_ref="aix.step_contract.v1.generic_edit",
        acceptance_criteria=("test passes",),
        validation_commands=("python -m pytest -q gaef001-scratch/test_calc_f.py",),
        assigned_role="executor",
        step_budget=BudgetV1(
            time_seconds=3600, tokens=100_000, cost_usd=5.0, retries=2, steps=10, concurrency=1
        ),
        actions=actions,
        required_effects=effects,
        status="PENDING",
    )


def test_read_only_actions_do_not_fulfill_a_literal_replace_step() -> None:
    contract = _gaef_step(
        actions=(
            ActionRequestV1(
                capability="filesystem",
                operation="read",
                arguments={"path": "gaef001-scratch/calc_f.py"},
            ),
        ),
        effects=(RequiredEffectV1(kind="LITERAL_REPLACE"),),
    )
    assert _actions_fulfill_the_step(contract) is False


def test_mutating_actions_fulfill_the_step() -> None:
    contract = _gaef_step(
        actions=(
            ActionRequestV1(
                capability="filesystem",
                operation="rename",
                arguments={"source": "a.png", "destination": "b.png"},
            ),
        ),
        effects=(RequiredEffectV1(kind="LITERAL_REPLACE"),),
    )
    assert _actions_fulfill_the_step(contract) is True
    assert (
        _extract_symbol_name_from_objective(
            "Implement normalize_user_email method while preserving its interface."
        )
        == "normalize_user_email"
    )


@pytest.fixture(autouse=True)
def offline_codegen(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the HTML/CSS branch off the network; these tests cover orchestration only."""

    async def _fake_generate(*, objective: str, **_kwargs: object) -> HtmlCssBundle:
        return local_fallback_html_css_bundle(objective)

    monkeypatch.setattr(
        "backend.aix_core.runtime.executor.real_execute.generate_html_css_page",
        _fake_generate,
    )


@pytest.fixture
def workspace_base(request: pytest.FixtureRequest) -> Path:
    base = _WS_ROOT / request.node.name
    if base.exists():
        for child in sorted(base.rglob("*"), reverse=True):
            if child.is_file() or child.is_symlink():
                child.unlink(missing_ok=True)
            elif child.is_dir():
                child.rmdir()
        base.rmdir()
    base.mkdir(parents=True, exist_ok=True)
    return base


@pytest.mark.asyncio
async def test_run_real_execute_writes_registration_artifacts(workspace_base: Path) -> None:
    result = await run_real_execute(
        tenant_id="tenant-1",
        cycle_id="cyc_test1",
        objective_id="obj_test1",
        objective="Build a React registration form with email validation",
        workhub_task_id="wh-task-demo",
        project_id=None,
        task_number=None,
        repository_id="aix-frontend",
        require_workhub_load=False,
        workspace_base=workspace_base,
        demo_capability=True,
    )
    assert result.ok is True
    assert result.workhub_lifecycle_authority is False
    assert result.workhub_loaded is False
    workspace = Path(result.workspace)
    assert (workspace / "evidence" / "objective.json").is_file()
    assert (workspace / "evidence" / "execution_report.json").is_file()
    assert (workspace / "proposed" / "RegisterFormProposed.tsx").is_file()
    assert "proposed_form" in result.artifacts


@pytest.mark.asyncio
async def test_run_real_execute_requires_project_when_workhub_required(workspace_base: Path) -> None:
    with pytest.raises(RealExecuteError) as exc:
        await run_real_execute(
            tenant_id="tenant-1",
            cycle_id="cyc_test2",
            objective_id="obj_test2",
            objective="Anything",
            workhub_task_id="wh-task-demo",
            project_id=None,
            task_number=None,
            repository_id="aix",
            require_workhub_load=True,
            workspace_base=workspace_base,
        )
    assert exc.value.code == "WORKHUB_PROJECT_ID_REQUIRED"


@pytest.mark.asyncio
async def test_backend_stub_path_unchanged_when_real_execute_off() -> None:
    backend = DurableObjectiveCycleApiBackend(mode="inprocess", real_execute_enabled=False)
    admit = await backend.command(
        operation="admit_objective",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=None,
        body={
            "workhub_task_id": "wh-task-1",
            "objective": "Prove stub still works",
            "task_class": "bounded_repair",
            "definition_of_done": ("tests pass",),
            "repository_id": "aix",
            "allowed_files": (),
        },
        idempotency_key="admit-stub",
        expected_version=None,
    )
    started = await backend.command(
        operation="start_cycle",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=admit["objective_id"],
        body={"candidate_sha": "abc1234", "budgets": {"max_steps": 3}},
        idempotency_key="start-stub",
        expected_version=None,
    )
    approved = await backend.command(
        operation="validate_approval_reference",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=started["cycle_id"],
        body={"approval_reference": "workhub://approvals/a1"},
        idempotency_key="approve-stub",
        expected_version=started["version"],
    )
    assert approved["ok"] is False
    assert approved["phase"] == "BLOCKED"
    assert (approved.get("error") or {}).get("code") == "NEED_EVIDENCE"
    assert approved.get("real_execute") is None


@pytest.mark.asyncio
async def test_backend_real_execute_on_approval(workspace_base: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HYEVE_AIX_SERVICE_OBJECTIVE_CYCLE_WORKSPACE_BASE", str(workspace_base))
    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess",
        real_execute_enabled=True,
        require_workhub_load=False,
        settings=None,
    )
    admit = await backend.command(
        operation="admit_objective",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=None,
        body={
            "workhub_task_id": "wh-task-1",
            "objective": "Build a React registration form",
            "task_class": "bounded_repair",
            "definition_of_done": ("form validates",),
            "repository_id": "aix-frontend",
            "allowed_files": (),
            "project_id": "proj-demo",
            "task_number": 7,
        },
        idempotency_key="admit-real",
        expected_version=None,
    )
    assert admit["ok"] is True
    assert admit["real_execute_enabled"] is True
    assert admit["aix_creates_workhub_tasks"] is False

    started = await backend.command(
        operation="start_cycle",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=admit["objective_id"],
        body={"candidate_sha": "abc1234", "budgets": {"max_steps": 3}},
        idempotency_key="start-real",
        expected_version=None,
    )
    approved = await backend.command(
        operation="validate_approval_reference",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=started["cycle_id"],
        body={"approval_reference": "workhub://approvals/a1"},
        idempotency_key="approve-real",
        expected_version=started["version"],
    )
    assert approved["ok"] is False
    assert approved["phase"] == "BLOCKED"
    assert approved["workhub_lifecycle_authority"] is False
    err = approved.get("error") or {}
    assert err.get("code") == "NEED_EVIDENCE"
    # Empty allowed_files + page-generation wording is not execute authority.
    assert approved.get("real_execute") is None


@pytest.mark.asyncio
async def test_backend_real_execute_fail_closed_without_project() -> None:
    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess",
        real_execute_enabled=True,
        require_workhub_load=True,
    )
    admit = await backend.command(
        operation="admit_objective",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=None,
        body={
            "workhub_task_id": "wh-task-1",
            "objective": "Anything",
            "task_class": "bounded_repair",
            "definition_of_done": ("done",),
            "repository_id": "aix",
            "allowed_files": (),
        },
        idempotency_key="admit-fail",
        expected_version=None,
    )
    assert admit["ok"] is False
    assert admit["error"]["code"] == "WORKHUB_PROJECT_ID_REQUIRED"


@pytest.fixture
def test_repo(tmp_path_factory) -> Path:
    repo_dir = Path(__file__).parent / ".tmp_test_repo"
    if repo_dir.exists():
        import shutil
        shutil.rmtree(repo_dir, ignore_errors=True)
    repo_dir.mkdir(parents=True, exist_ok=True)
    yield repo_dir
    if repo_dir.exists():
        import shutil
        shutil.rmtree(repo_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_add_logging_to_function_x_resolves_and_patches_symbol(test_repo: Path, monkeypatch: pytest.MonkeyPatch, workspace_base: Path) -> None:
    """Scenario 1: Add logging to function X resolves symbol and applies surgical patch."""
    from backend.aix_core.runtime.executor.source_patch_codegen import PatchCodegenResult, PatchEdit

    monkeypatch.setenv("HYEVE_AIX_SERVICE_OBJECTIVE_CYCLE_REPOSITORY_ROOTS", f'{{"test-repo": "{test_repo.as_posix()}"}}')
    file_path = test_repo / "math_utils.py"
    initial_content = "def calculate_total(items):\n    return sum(items)\n"
    file_path.write_text(initial_content, encoding="utf-8")
    (test_repo / "pyproject.toml").write_text("[project]\nname = 'test-repo'\n", encoding="utf-8")
    (test_repo / "tests").mkdir(exist_ok=True)
    (test_repo / "tests" / "test_smoke.py").write_text("def test_smoke():\n    assert True\n", encoding="utf-8")

    diff = (
        "--- a/math_utils.py\n"
        "+++ b/math_utils.py\n"
        "@@ -1,2 +1,4 @@\n"
        "+import logging\n"
        " def calculate_total(items):\n"
        "+    logging.info('calculating total')\n"
        "     return sum(items)\n"
    )
    new_content = "import logging\ndef calculate_total(items):\n    logging.info('calculating total')\n    return sum(items)\n"

    async def _mock_patch(*_args, **_kwargs):
        return PatchCodegenResult(edits=[PatchEdit(path="math_utils.py", unified_diff=diff, new_content=new_content)])

    monkeypatch.setattr(
        "backend.aix_core.runtime.executor.multi_step_execute.generate_source_patch",
        _mock_patch,
    )

    result = await run_real_execute(
        tenant_id="tenant-1",
        cycle_id="cyc_edit_1",
        objective_id="obj_edit_1",
        objective="Add logging to function calculate_total in math_utils.py",
        workhub_task_id="wh-task-edit",
        project_id=None,
        task_number=None,
        repository_id="test-repo",
        require_workhub_load=False,
        workspace_base=workspace_base,
    )
    assert result.ok is True
    assert result.resolved_symbol is not None
    assert result.resolved_symbol["symbol_name"] == "calculate_total"
    assert result.changed_source_files is not None


@pytest.mark.asyncio
async def test_missing_symbol_returns_symbol_not_found(test_repo: Path, monkeypatch: pytest.MonkeyPatch, workspace_base: Path) -> None:
    """Scenario 2: Missing symbol returns SYMBOL_NOT_FOUND."""
    monkeypatch.setenv("HYEVE_AIX_SERVICE_OBJECTIVE_CYCLE_REPOSITORY_ROOTS", f'{{"test-repo": "{test_repo.as_posix()}"}}')
    file_path = test_repo / "math_utils.py"
    file_path.write_text("def calculate_total(items):\n    return sum(items)\n", encoding="utf-8")

    with pytest.raises(RealExecuteError) as exc:
        await run_real_execute(
            tenant_id="tenant-1",
            cycle_id="cyc_edit_2",
            objective_id="obj_edit_2",
            objective="Add logging to function non_existent_function in math_utils.py",
            workhub_task_id="wh-task-edit",
            project_id=None,
            task_number=None,
            repository_id="test-repo",
            require_workhub_load=False,
            workspace_base=workspace_base,
        )
    assert exc.value.code == "SYMBOL_NOT_FOUND"


@pytest.mark.asyncio
async def test_duplicate_symbol_returns_symbol_ambiguous(test_repo: Path, monkeypatch: pytest.MonkeyPatch, workspace_base: Path) -> None:
    """Scenario 3: Ambiguous symbol returns SYMBOL_AMBIGUOUS."""
    monkeypatch.setenv("HYEVE_AIX_SERVICE_OBJECTIVE_CYCLE_REPOSITORY_ROOTS", f'{{"test-repo": "{test_repo.as_posix()}"}}')
    (test_repo / "mod1.py").write_text("def duplicate_func(): pass\n", encoding="utf-8")
    (test_repo / "mod2.py").write_text("def duplicate_func(): pass\n", encoding="utf-8")

    with pytest.raises(RealExecuteError) as exc:
        await run_real_execute(
            tenant_id="tenant-1",
            cycle_id="cyc_edit_3",
            objective_id="obj_edit_3",
            objective="Update function duplicate_func to add logging",
            workhub_task_id="wh-task-edit",
            project_id=None,
            task_number=None,
            repository_id="test-repo",
            require_workhub_load=False,
            workspace_base=workspace_base,
        )
    assert exc.value.code == "SYMBOL_AMBIGUOUS"


@pytest.mark.asyncio
async def test_wrong_patch_context_leaves_file_unchanged(test_repo: Path, monkeypatch: pytest.MonkeyPatch, workspace_base: Path) -> None:
    """Scenario 4: Wrong patch context rejects patch and leaves file unchanged."""
    from backend.aix_core.runtime.executor.source_patch_codegen import PatchCodegenResult, PatchEdit

    monkeypatch.setenv("HYEVE_AIX_SERVICE_OBJECTIVE_CYCLE_REPOSITORY_ROOTS", f'{{"test-repo": "{test_repo.as_posix()}"}}')
    file_path = test_repo / "math_utils.py"
    initial_content = "def calculate_total(items):\n    return sum(items)\n"
    file_path.write_text(initial_content, encoding="utf-8")

    # Mock codegen to return wrong hunk context diff
    bad_diff = (
        "--- a/math_utils.py\n"
        "+++ b/math_utils.py\n"
        "@@ -1,2 +1,3 @@\n"
        " def subtract_total(items):\n"
        "+    logging.info('subtracting')\n"
        "     return sum(items)\n"
    )

    async def _mock_bad_patch(*_args, **_kwargs):
        return PatchCodegenResult(
            edits=[PatchEdit(path="math_utils.py", unified_diff=bad_diff, new_content="")],
        )

    monkeypatch.setattr(
        "backend.aix_core.runtime.executor.multi_step_execute.generate_source_patch",
        _mock_bad_patch,
    )

    with pytest.raises(RealExecuteError) as exc:
        await run_real_execute(
            tenant_id="tenant-1",
            cycle_id="cyc_edit_4",
            objective_id="obj_edit_4",
            objective="Add logging to function calculate_total in math_utils.py",
            workhub_task_id="wh-task-edit",
            project_id=None,
            task_number=None,
            repository_id="test-repo",
            require_workhub_load=False,
            workspace_base=workspace_base,
        )

    assert exc.value.code == "PATCH_CONTEXT_MISMATCH"
    assert file_path.read_text(encoding="utf-8") == initial_content  # File completely untouched!


@pytest.mark.asyncio
async def test_stale_hash_prevents_mutation(test_repo: Path, monkeypatch: pytest.MonkeyPatch, workspace_base: Path) -> None:
    """Scenario 5: Stale precondition hash prevents patch application."""
    from backend.aix_core.runtime.executor.patch_validate import validate_patch, compute_content_hash

    initial_content = "def foo(): pass\n"
    diff = "--- a/foo.py\n+++ b/foo.py\n@@ -1,1 +1,2 @@\n def foo(): pass\n+# log\n"
    res = validate_patch(
        file_path="foo.py",
        existing_content=initial_content,
        unified_diff=diff,
        expected_file_hash="0000000000000000000000000000000000000000000000000000000000000000",
    )
    assert res.valid is False
    assert res.reason_code == "STALE_FILE_VERSION"


@pytest.mark.asyncio
async def test_no_op_patch_does_not_complete(test_repo: Path, monkeypatch: pytest.MonkeyPatch, workspace_base: Path) -> None:
    """Scenario 6: +0/-0 or identical diff is rejected as NO_OP_PATCH."""
    from backend.aix_core.runtime.executor.source_patch_codegen import PatchCodegenResult, PatchEdit

    monkeypatch.setenv("HYEVE_AIX_SERVICE_OBJECTIVE_CYCLE_REPOSITORY_ROOTS", f'{{"test-repo": "{test_repo.as_posix()}"}}')
    file_path = test_repo / "math_utils.py"
    initial_content = "def calculate_total(items):\n    return sum(items)\n"
    file_path.write_text(initial_content, encoding="utf-8")

    no_op_diff = "--- a/math_utils.py\n+++ b/math_utils.py\n@@ -1,2 +1,2 @@\n def calculate_total(items):\n     return sum(items)\n"

    async def _mock_noop_patch(*_args, **_kwargs):
        return PatchCodegenResult(
            edits=[PatchEdit(path="math_utils.py", unified_diff=no_op_diff, new_content=initial_content)],
        )

    monkeypatch.setattr(
        "backend.aix_core.runtime.executor.multi_step_execute.generate_source_patch",
        _mock_noop_patch,
    )

    with pytest.raises(RealExecuteError) as exc:
        await run_real_execute(
            tenant_id="tenant-1",
            cycle_id="cyc_edit_6",
            objective_id="obj_edit_6",
            objective="Add logging to function calculate_total in math_utils.py",
            workhub_task_id="wh-task-edit",
            project_id=None,
            task_number=None,
            repository_id="test-repo",
            require_workhub_load=False,
            workspace_base=workspace_base,
        )

    assert exc.value.code == "NO_OP_PATCH"


@pytest.mark.asyncio
async def test_whole_file_overwrite_is_rejected(test_repo: Path) -> None:
    """Scenario 7: Whole-file overwrite of an existing source file is rejected by policy."""
    from backend.aix_core.runtime.executor.patch_validate import validate_write_file_policy

    existing = test_repo / "main.py"
    existing.write_text("print('hello')", encoding="utf-8")

    policy_res = validate_write_file_policy(existing, "print('overwrite')", create_only=True)
    assert policy_res.allowed is False
    assert policy_res.reason_code == "CREATE_ONLY_VIOLATION"


@pytest.mark.asyncio
async def test_repeated_identical_searches_block(test_repo: Path, monkeypatch: pytest.MonkeyPatch, workspace_base: Path) -> None:
    """Scenario 8: Repeated identical searches trigger REPEATED_IDENTICAL_SEARCH loop detector."""
    from backend.aix_core.runtime.governance.execution_budget import ExecutionBudgetConfig, ExecutionBudgetManager

    config = ExecutionBudgetConfig(max_identical_searches=1)
    mgr = ExecutionBudgetManager(cycle_id="c1", objective_id="o1", config=config)

    assert mgr.record_search("pattern X", scope="all").allowed is True
    res = mgr.record_search("pattern X", scope="all")
    assert res.allowed is False
    assert res.reason_code == "REPEATED_IDENTICAL_SEARCH"
    assert res.objective_status == "BLOCKED"


@pytest.mark.asyncio
async def test_repeated_identical_reads_block(test_repo: Path, monkeypatch: pytest.MonkeyPatch, workspace_base: Path) -> None:
    """Scenario 9: Repeated identical reads trigger REPEATED_IDENTICAL_READ loop detector."""
    from backend.aix_core.runtime.governance.execution_budget import ExecutionBudgetConfig, ExecutionBudgetManager

    config = ExecutionBudgetConfig(max_identical_reads=1)
    mgr = ExecutionBudgetManager(cycle_id="c1", objective_id="o1", config=config)

    assert mgr.record_read("file.py", file_hash="hash1").allowed is True
    assert mgr.record_read("other.py", file_hash="hash2").allowed is True
    res = mgr.record_read("file.py", file_hash="hash1")
    assert res.allowed is False
    assert res.reason_code == "REPEATED_IDENTICAL_READ"
    assert res.objective_status == "BLOCKED"


@pytest.mark.asyncio
async def test_budget_exhaustion_blocks_objective(test_repo: Path, monkeypatch: pytest.MonkeyPatch, workspace_base: Path) -> None:
    """Scenario 10: Search budget exhaustion triggers EXPLORATION_BUDGET_EXCEEDED and blocks objective."""
    from backend.aix_core.runtime.governance.execution_budget import ExecutionBudgetConfig

    monkeypatch.setenv("HYEVE_AIX_SERVICE_OBJECTIVE_CYCLE_REPOSITORY_ROOTS", f'{{"test-repo": "{test_repo.as_posix()}"}}')
    (test_repo / "config.py").write_text("SMTP_HOST=localhost\n", encoding="utf-8")

    # Tight search budget cap
    tight_budget = ExecutionBudgetConfig(max_searches=1)

    with pytest.raises(RealExecuteError) as exc:
        await run_real_execute(
            tenant_id="tenant-1",
            cycle_id="cyc_budget_10",
            objective_id="obj_budget_10",
            objective="Add SMTP_PASSWORD configuration to config.py",
            workhub_task_id="wh-task-budget",
            project_id=None,
            task_number=None,
            repository_id="test-repo",
            require_workhub_load=False,
            workspace_base=workspace_base,
            budget_config=tight_budget,
        )

    assert exc.value.code == "EXPLORATION_BUDGET_EXCEEDED"


@pytest.mark.asyncio
async def test_successful_edit_records_before_after_hashes_and_passes_verification(test_repo: Path, monkeypatch: pytest.MonkeyPatch, workspace_base: Path) -> None:
    """Scenario 11: Successful edit records before/after hashes and passes verification."""
    from backend.aix_core.runtime.executor.patch_validate import compute_content_hash, validate_patch, apply_patch_atomic

    file_path = test_repo / "math_utils.py"
    initial_content = "def calculate_total(items):\n    return sum(items)\n"
    file_path.write_text(initial_content, encoding="utf-8")
    before_hash = compute_content_hash(initial_content)

    diff = (
        "--- a/math_utils.py\n"
        "+++ b/math_utils.py\n"
        "@@ -1,2 +1,4 @@\n"
        "+import logging\n"
        " def calculate_total(items):\n"
        "+    logging.info('total')\n"
        "     return sum(items)\n"
    )

    patch_val = validate_patch(file_path=file_path, existing_content=initial_content, unified_diff=diff)
    assert patch_val.valid is True

    after_hash = apply_patch_atomic(file_path, patch_val)
    assert after_hash != before_hash
    assert compute_content_hash(file_path.read_text(encoding="utf-8")) == after_hash

