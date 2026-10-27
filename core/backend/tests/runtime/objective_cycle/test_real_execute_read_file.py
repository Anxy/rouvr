"""Integration test for the real read-only branch of `run_real_execute` — the exact
scenario named in the "AIX Real Execution Sprint" problem statement: an objective
naming a literal file path plus "read this file" must produce a real
`filesystem.read_file` call against the approved repository, real evidence, and a
truthful `REAL_EXECUTE_OK` only after verification — never the metadata-only
`bounded_propose_artifact` / `human_review` plan."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from backend.aix_core.runtime.executor.real_execute import RealExecuteError, run_real_execute


def _init_git_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=root, check=True)


@pytest.fixture
def fake_integration_service_with_env(tmp_path: Path) -> Path:
    (tmp_path / ".env").write_text(
        "SMTP_HOST=smtp.example.com\n"
        "SMTP_PORT=587\n"
        "SMTP_USER=notifications@example.com\n"
        "SMTP_PASSWORD=hunter2\n"
        "SMTP_TLS=true\n"
        "SMTP_FROM=noreply@example.com\n",
        encoding="utf-8",
        newline="\n",
    )
    (tmp_path / "README.md").write_text("placeholder\n", encoding="utf-8", newline="\n")
    _init_git_repo(tmp_path)
    return tmp_path


class _FakeSettings:
    def __init__(self, roots: dict[str, str]) -> None:
        self.OBJECTIVE_CYCLE_REPOSITORY_ROOTS = json.dumps(roots)
        self.OPENROUTER_API_KEY = ""


@pytest.mark.asyncio
async def test_sprint_acceptance_scenario_reads_the_real_env_file(
    fake_integration_service_with_env: Path, tmp_path_factory
) -> None:
    settings = _FakeSettings({"hyeve-integration-service": str(fake_integration_service_with_env)})
    workspace_base = tmp_path_factory.mktemp("ws_base")
    objective = f"{fake_integration_service_with_env / '.env'} read this file"

    result = await run_real_execute(
        tenant_id="tenant-1",
        cycle_id="cyc_read1",
        objective_id="obj_read1",
        objective=objective,
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
    assert result.repository_root == str(fake_integration_service_with_env.resolve())

    # Real tool call happened — not a proposal.
    event_types = [e["event_type"] for e in result.events]
    assert "tool.file_read" in event_types

    assert result.read_verification == {
        "resource_resolved": True,
        "file_read_non_empty": True,
        "target_resource_accessed": True,
        "secret_values_redacted": True,
    }

    # The raw secret value must never appear anywhere the caller can see.
    assert "hunter2" not in str(result.artifacts)
    assert "hunter2" not in (result.detail or "")
    evidence_text = Path(result.artifacts["file_read"]).read_text(encoding="utf-8")
    assert "hunter2" not in evidence_text
    execution_report = json.loads(Path(result.artifacts["execution_report"]).read_text(encoding="utf-8"))
    assert "hunter2" not in json.dumps(execution_report)

    # The evidence must actually name the 6 env vars found, 1 flagged sensitive —
    # matching the sprint's own worked example exactly.
    file_read_evidence = json.loads(evidence_text)
    env_summary = file_read_evidence["env_summary"]
    assert len(env_summary["keys"]) == 6
    sensitive_keys = [k["name"] for k in env_summary["keys"] if k["sensitive"]]
    assert sensitive_keys == ["SMTP_PASSWORD"]

    # This must be the real read-file executor, never the fake-success
    # metadata-only path (the pre-existing `plan.json`/`bounded_propose_artifact`
    # bookkeeping is still written as an administrative artifact alongside it, same
    # as the already-existing source-edit branch — only the executor identity and
    # real tool call above prove this was not a proposal-only outcome).
    assert execution_report["executor"] == "objective_cycle.real_execute.read_file.v1"


@pytest.mark.asyncio
async def test_missing_file_blocks_with_path_not_found(
    fake_integration_service_with_env: Path, tmp_path_factory
) -> None:
    settings = _FakeSettings({"hyeve-integration-service": str(fake_integration_service_with_env)})
    workspace_base = tmp_path_factory.mktemp("ws_base")
    objective = str(fake_integration_service_with_env / "missing.env") + " read this file"

    with pytest.raises(RealExecuteError) as exc_info:
        await run_real_execute(
            tenant_id="tenant-1",
            cycle_id="cyc_read2",
            objective_id="obj_read2",
            objective=objective,
            workhub_task_id="",
            project_id=None,
            task_number=None,
            repository_id="hyeve-integration-service",
            settings=settings,
            require_workhub_load=False,
            workspace_base=workspace_base,
        )
    assert exc_info.value.code == "PATH_NOT_FOUND"


@pytest.mark.asyncio
async def test_unregistered_repository_blocks_read_objective_without_workspace_binding(
    tmp_path_factory,
) -> None:
    """A path request must never fall back to administrative proposal artifacts."""
    settings = _FakeSettings({})
    workspace_base = tmp_path_factory.mktemp("ws_base")

    with pytest.raises(RealExecuteError) as exc_info:
        await run_real_execute(
            tenant_id="tenant-1",
            cycle_id="cyc_read3",
            objective_id="obj_read3",
            objective=r"D:\some\unapproved\path\.env read this file",
            workhub_task_id="wh-task-demo",
            project_id=None,
            task_number=None,
            repository_id="not-registered",
            settings=settings,
            require_workhub_load=False,
            workspace_base=workspace_base,
        )
    assert exc_info.value.code == "WORKSPACE_NOT_BOUND"


@pytest.mark.asyncio
async def test_bare_path_auto_binds_to_the_matching_registered_workspace(
    fake_integration_service_with_env: Path, tmp_path_factory
) -> None:
    """AIX-REAL-EXECUTION-002 bare-path -> workspace: the caller's `repository_id`
    doesn't match anything registered, but the literal path in the objective text
    unambiguously resolves under a *different* registered root -- must auto-bind to
    that root and execute for real, never fail closed just because the caller guessed
    the wrong repository_id string."""
    settings = _FakeSettings({"hyeve-integration-service": str(fake_integration_service_with_env)})
    workspace_base = tmp_path_factory.mktemp("ws_base")
    objective = f"{fake_integration_service_with_env / '.env'} read this file"

    result = await run_real_execute(
        tenant_id="tenant-1",
        cycle_id="cyc_read5",
        objective_id="obj_read5",
        objective=objective,
        workhub_task_id="",
        project_id=None,
        task_number=None,
        repository_id="not-the-registered-key",
        settings=settings,
        require_workhub_load=False,
        workspace_base=workspace_base,
    )
    assert result.ok is True
    assert result.code == "REAL_EXECUTE_OK"
    assert result.objective_status == "COMPLETED"
    assert result.repository_root == str(fake_integration_service_with_env.resolve())


@pytest.mark.asyncio
async def test_bare_path_with_no_registered_root_match_still_blocks(tmp_path_factory) -> None:
    """Zero matches must stay fail-closed -- bare-path binding only ever adds a new
    success path, never weakens the existing WORKSPACE_NOT_BOUND containment."""
    settings = _FakeSettings({"some-other-service": r"D:\some\other\root"})
    workspace_base = tmp_path_factory.mktemp("ws_base")

    with pytest.raises(RealExecuteError) as exc_info:
        await run_real_execute(
            tenant_id="tenant-1",
            cycle_id="cyc_read6",
            objective_id="obj_read6",
            objective=r"D:\some\unapproved\path\.env read this file",
            workhub_task_id="",
            project_id=None,
            task_number=None,
            repository_id="not-registered",
            settings=settings,
            require_workhub_load=False,
            workspace_base=workspace_base,
        )
    assert exc_info.value.code == "WORKSPACE_NOT_BOUND"


@pytest.mark.asyncio
async def test_edit_objective_on_registered_repository_still_uses_source_edit_path(
    fake_integration_service_with_env: Path, tmp_path_factory
) -> None:
    """An objective that both names a path and asks for an edit must never be
    misclassified as a read — the edit verb disqualifies read-intent classification."""
    (fake_integration_service_with_env / "backend").mkdir()
    (fake_integration_service_with_env / "backend" / "config.py").write_text(
        "class Settings:\n    SMTP_HOST: str = ''\n", encoding="utf-8", newline="\n"
    )
    (fake_integration_service_with_env / "pyproject.toml").write_text(
        "[project]\nname='x'\n", encoding="utf-8", newline="\n"
    )
    (fake_integration_service_with_env / "tests").mkdir()
    (fake_integration_service_with_env / "tests" / "test_smoke.py").write_text(
        "def test_smoke():\n    assert True\n", encoding="utf-8", newline="\n"
    )
    subprocess.run(["git", "add", "-A"], cwd=fake_integration_service_with_env, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "add files"], cwd=fake_integration_service_with_env, check=True)

    settings = _FakeSettings({"hyeve-integration-service": str(fake_integration_service_with_env)})
    workspace_base = tmp_path_factory.mktemp("ws_base")
    objective = f"{fake_integration_service_with_env / '.env'} read this file and update SMTP_PASSWORD=rotated-test-value"

    result = await run_real_execute(
        tenant_id="tenant-1",
        cycle_id="cyc_read4",
        objective_id="obj_read4",
        objective=objective,
        workhub_task_id="",
        project_id=None,
        task_number=None,
        repository_id="hyeve-integration-service",
        settings=settings,
        require_workhub_load=False,
        workspace_base=workspace_base,
        allowed_files=(".env",),
    )
    # Reached the source-edit (real apply+verify) path, not the read path.
    assert result.code == "REAL_EXECUTE_OK"
    assert result.read_verification is None
    assert result.objective_status == "COMPLETED"
