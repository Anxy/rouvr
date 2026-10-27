"""Real source-edit path of `run_real_execute`: the executor must actually read and
patch files in the approved target repository, never just write metadata about it."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from backend.aix_core.runtime.objective.contracts import (
    BudgetV1,
    RequiredEffectV1,
    RiskTier,
    StepContractV1,
)
from backend.aix_core.runtime.executor.real_execute import (
    RealExecuteError,
    looks_like_page_generation_objective,
    run_real_execute,
)


def _init_git_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=root, check=True)


@pytest.fixture
def fake_integration_service(tmp_path: Path) -> Path:
    (tmp_path / "backend" / "hyeve_integration_service").mkdir(parents=True)
    (tmp_path / "backend" / "hyeve_integration_service" / "config.py").write_text(
        "class Settings:\n    VALUE_HOST: str = ''\n    VALUE_PORT: int = 587\n",
        encoding="utf-8",
        newline="\n",
    )
    (tmp_path / ".env.example").write_text("VALUE_HOST=\nVALUE_PORT=587\n", encoding="utf-8", newline="\n")
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname = 'hyeve-integration-service'\n", encoding="utf-8", newline="\n"
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_smoke.py").write_text("def test_smoke():\n    assert True\n", encoding="utf-8", newline="\n")
    _init_git_repo(tmp_path)
    return tmp_path


class _FakeSettings:
    def __init__(self, roots: dict[str, str]) -> None:
        self.OBJECTIVE_CYCLE_REPOSITORY_ROOTS = json.dumps(roots)
        self.OPENROUTER_API_KEY = ""


@pytest.mark.asyncio
async def test_source_edit_applies_a_real_verified_change(
    fake_integration_service: Path, tmp_path_factory
) -> None:
    """Real source-edit resolves the target file from real discovery, validates a
    hash-gated patch, applies it atomically to the approved repository, and only
    reports completion once independent verification (a real test run) passes -- the
    surgical apply+verify path, not a proposal awaiting a separate reviewer step."""
    settings = _FakeSettings({"hyeve-integration-service": str(fake_integration_service)})
    workspace_base = tmp_path_factory.mktemp("ws_base")

    result = await run_real_execute(
        tenant_id="tenant-1",
        cycle_id="cyc_edit1",
        objective_id="obj_edit1",
        objective="replace 'VALUE_HOST' with 'VALUE_HOST_READY'",
        workhub_task_id="",
        project_id=None,
        task_number=None,
        repository_id="hyeve-integration-service",
        settings=settings,
        require_workhub_load=False,
        workspace_base=workspace_base,
    )

    assert result.ok is True
    assert result.code == "REAL_EXECUTE_OK"
    assert result.repository_root == str(fake_integration_service.resolve())

    on_disk = (fake_integration_service / "backend" / "hyeve_integration_service" / "config.py").read_text(
        encoding="utf-8"
    )
    assert "VALUE_HOST_READY" in on_disk

    assert result.diff_text and "VALUE_HOST_READY" in result.diff_text
    changed_paths = [c["path"] for c in result.changed_source_files]
    assert "backend/hyeve_integration_service/config.py" in changed_paths
    assert result.completion_gates is not None
    assert result.completion_gates["passed"] is True

    assert result.execution_status == "COMPLETED"
    assert result.verification_status == "PASSED"
    assert result.objective_status == "COMPLETED"
    assert result.real_operations >= 1
    assert result.reason_code is None
    assert result.target_files_modified >= 1

    event_types = [e["event_type"] for e in result.events]
    assert "discovery.started" in event_types
    assert "tool.file_read" in event_types
    assert "patch.application.completed" in event_types


@pytest.mark.asyncio
async def test_source_edit_apply_edits_false_proposes_without_applying(
    fake_integration_service: Path, tmp_path_factory
) -> None:
    settings = _FakeSettings({"hyeve-integration-service": str(fake_integration_service)})
    workspace_base = tmp_path_factory.mktemp("ws_base")

    result = await run_real_execute(
        tenant_id="tenant-1",
        cycle_id="cyc_edit2",
        objective_id="obj_edit2",
        objective="replace 'VALUE_HOST' with 'VALUE_HOST_READY'",
        workhub_task_id="",
        project_id=None,
        task_number=None,
        repository_id="hyeve-integration-service",
        settings=settings,
        require_workhub_load=False,
        workspace_base=workspace_base,
        apply_edits=False,
    )

    assert result.ok is True
    assert result.code == "PATCH_PROPOSED"
    assert result.repository_root == str(fake_integration_service.resolve())

    on_disk = (fake_integration_service / "backend" / "hyeve_integration_service" / "config.py").read_text(
        encoding="utf-8"
    )
    assert "VALUE_HOST_READY" not in on_disk

    assert result.proposed_edits is not None
    proposed_paths = [e["path"] for e in result.proposed_edits]
    assert "backend/hyeve_integration_service/config.py" in proposed_paths
    config_edit = next(e for e in result.proposed_edits if e["path"] == "backend/hyeve_integration_service/config.py")
    assert "VALUE_HOST_READY" in config_edit["new_content"]
    assert result.diff_text and "VALUE_HOST_READY" in result.diff_text

    assert result.changed_source_files == []
    assert result.completion_gates is None
    assert result.execution_status == "AWAITING_APPLICATION"
    assert result.verification_status == "NOT_RUN"
    assert result.objective_status == "AWAITING_REVIEW"
    assert result.reason_code == "NO_FILES_CHANGED"

    event_types = [e["event_type"] for e in result.events]
    assert "tool.file_read" in event_types
    assert "tool.patch_applied" not in event_types
    assert "patch.application.completed" not in event_types


@pytest.mark.asyncio
async def test_unregistered_repository_id_keeps_metadata_only_demo_path(tmp_path_factory) -> None:
    settings = _FakeSettings({})  # nothing registered
    workspace_base = tmp_path_factory.mktemp("ws_base")

    result = await run_real_execute(
        tenant_id="tenant-1",
        cycle_id="cyc_demo1",
        objective_id="obj_demo1",
        objective="build a login page",
        workhub_task_id="wh-task-demo",
        project_id=None,
        task_number=None,
        repository_id="aix-frontend",
        settings=settings,
        require_workhub_load=False,
        workspace_base=workspace_base,
        demo_capability=True,
    )
    assert result.ok is True
    assert result.code == "PROPOSAL_READY"
    assert result.objective_status == "AWAITING_REVIEW"
    assert result.real_operations == 0
    assert result.repository_root is None
    assert result.changed_source_files == []
    assert "proposed_html" in result.artifacts


@pytest.mark.asyncio
async def test_no_applicable_source_file_blocks_with_clear_reason(tmp_path_factory, tmp_path: Path) -> None:
    empty_repo = tmp_path / "empty-repo"
    empty_repo.mkdir()
    (empty_repo / "README.md").write_text("nothing relevant\n", encoding="utf-8", newline="\n")
    _init_git_repo(empty_repo)

    settings = _FakeSettings({"empty-repo": str(empty_repo)})
    workspace_base = tmp_path_factory.mktemp("ws_base")

    with pytest.raises(RealExecuteError) as exc_info:
        await run_real_execute(
            tenant_id="tenant-1",
            cycle_id="cyc_blocked1",
            objective_id="obj_blocked1",
            objective="replace 'MISSING_TOKEN_ZZZ' with 'OTHER'",
            workhub_task_id="",
            project_id=None,
            task_number=None,
            repository_id="empty-repo",
            settings=settings,
            require_workhub_load=False,
            workspace_base=workspace_base,
        )
    assert exc_info.value.code == "NO_APPLICABLE_SOURCE_FILE"
    assert exc_info.value.detail


@pytest.mark.asyncio
async def test_unregistered_repository_id_via_registry_lookup_is_fail_closed(
    fake_integration_service: Path, tmp_path_factory
) -> None:
    settings = _FakeSettings({"some-other-service": str(fake_integration_service)})
    workspace_base = tmp_path_factory.mktemp("ws_base")

    result = await run_real_execute(
        tenant_id="tenant-1",
        cycle_id="cyc_notreg1",
        objective_id="obj_notreg1",
        objective="replace 'VALUE_HOST' with 'VALUE_HOST_READY'",
        workhub_task_id="wh-task-demo",
        project_id=None,
        task_number=None,
        repository_id="hyeve-integration-service",  # not the registered key
        settings=settings,
        require_workhub_load=False,
        workspace_base=workspace_base,
    )
    assert result.repository_root is None
    on_disk = (fake_integration_service / "backend" / "hyeve_integration_service" / "config.py").read_text(
        encoding="utf-8"
    )
    assert "VALUE_HOST_READY" not in on_disk


@pytest.fixture
def fake_repo_with_readme(tmp_path: Path) -> Path:
    (tmp_path / "README.md").write_text(
        "# Demo Repository\n\nThis application powers the demo page for the team.\n",
        encoding="utf-8",
        newline="\n",
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_smoke.py").write_text("def test_smoke():\n    assert True\n", encoding="utf-8", newline="\n")
    _init_git_repo(tmp_path)
    return tmp_path


@pytest.mark.asyncio
async def test_readme_text_replace_is_not_hijacked_by_page_generation_demo(
    fake_repo_with_readme: Path, tmp_path_factory
) -> None:
    """Regression for the live `real_execute.demo.v1` misrouting (cyc_8d6f434a5d76):
    a plain text-replace objective against an existing, registered README.md must
    apply the real edit, even though the README's own content contains
    page-generation-shaped words ("application", "page") and even though the caller
    (`durable_api_backend.py`) always derives `demo_capability` from the very same
    `looks_like_page_generation_objective` heuristic that flags this objective. The
    demo/proposal branch must never preempt a real, already-classified EDIT_FILE
    intent on a registered repository -- it must never produce unrelated
    `proposed/index.html` / `proposed/styles.css` in place of the requested edit."""
    settings = _FakeSettings({"demo-readme-repo": str(fake_repo_with_readme)})
    workspace_base = tmp_path_factory.mktemp("ws_base")

    objective = "replace 'Demo Repository' with 'Renamed Application' in README.md"
    # Heuristic must NOT flag a file-bound replace as page generation. Even if a
    # caller still forces demo_capability=True (legacy wiring), EDIT_FILE + allowed
    # scope must take the real mutation path — never HTML/CSS proposal artifacts.
    assert looks_like_page_generation_objective(objective) is False

    result = await run_real_execute(
        tenant_id="tenant-1",
        cycle_id="cyc_readme1",
        objective_id="obj_readme1",
        objective=objective,
        workhub_task_id="",
        project_id=None,
        task_number=None,
        repository_id="demo-readme-repo",
        settings=settings,
        require_workhub_load=False,
        workspace_base=workspace_base,
        demo_capability=True,  # forced: prove file-bound real execution still wins
        allowed_files=("README.md",),
    )

    assert result.code == "REAL_EXECUTE_OK"
    assert result.objective_status == "COMPLETED"
    assert result.repository_root == str(fake_repo_with_readme.resolve())

    on_disk = (fake_repo_with_readme / "README.md").read_text(encoding="utf-8")
    assert "Renamed Application" in on_disk
    assert "Demo Repository" not in on_disk

    changed_paths = [c["path"] for c in result.changed_source_files]
    assert "README.md" in changed_paths
    assert not (fake_repo_with_readme / "proposed").exists()
    assert "proposed_html" not in result.artifacts
    assert "proposed_css" not in result.artifacts


@pytest.mark.asyncio
async def test_planner_guided_readme_replace_is_not_routed_to_demo_executor(
    fake_repo_with_readme: Path, tmp_path_factory
) -> None:
    """Reproduce cyc_8d6f434a5d76: StepContract guidance replaces the raw
    objective before executor routing, and an old README title containing a broad
    page keyword ("Dashboard") must not preempt the registered EDIT_FILE lane."""
    settings = _FakeSettings({"demo-readme-repo": str(fake_repo_with_readme)})
    workspace_base = tmp_path_factory.mktemp("ws_base")
    contract = StepContractV1(
        cycle_id="cyc_readme_planned",
        tenant_id="tenant-1",
        step_index=0,
        purpose=(
            "Replace the exact title string Demo Repository Dashboard in README.md "
            "and confirm the change is limited to that file."
        ),
        target_scope=("README.md",),
        expected_output_schema_ref="aix.step_contract.v1.generic_edit",
        acceptance_criteria=(
            'README.md contains "Renamed Application".',
            'README.md no longer contains "Demo Repository".',
        ),
        validation_commands=("git diff -- README.md",),
        required_effects=(RequiredEffectV1(kind="LITERAL_REPLACE"),),
        assigned_role="executor",
        risk_tier=RiskTier.LOW,
        step_budget=BudgetV1(
            time_seconds=300,
            tokens=10_000,
            cost_usd=1.0,
            retries=1,
            steps=2,
            concurrency=1,
        ),
        status="PENDING",
    )

    result = await run_real_execute(
        tenant_id="tenant-1",
        cycle_id="cyc_readme_planned",
        objective_id="obj_readme_planned",
        objective=(
            "replace 'Demo Repository' with 'Renamed Application' in README.md. "
            "Do not create HTML. Do not create CSS. Do not create proposal artifacts."
        ),
        workhub_task_id="",
        project_id=None,
        task_number=None,
        repository_id="demo-readme-repo",
        settings=settings,
        require_workhub_load=False,
        workspace_base=workspace_base,
        demo_capability=True,
        step_contract=contract,
    )

    assert result.code == "REAL_EXECUTE_OK"
    assert result.objective_status == "COMPLETED"
    assert "objective_cycle.real_execute.demo.v1" not in str(result.events)
    assert not (fake_repo_with_readme / "proposed").exists()
    on_disk = (fake_repo_with_readme / "README.md").read_text(encoding="utf-8")
    assert "Renamed Application" in on_disk
    assert "Demo Repository" not in on_disk
