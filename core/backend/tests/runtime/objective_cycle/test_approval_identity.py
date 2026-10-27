"""AIX gap-closure sprint 3, Priority 3: approval identity.

Approval must bind to the cycle's current plan/gate, not just "some non-empty
string while the cycle happens to be WAITING_APPROVAL". Driven through the real
`DurableObjectiveCycleApiBackend.command()` path, not internal helpers directly.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from backend.aix_core.runtime.objective.durable_api_backend import DurableObjectiveCycleApiBackend


def _init_git_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=root, check=True)


@pytest.fixture
def svc_repo(tmp_path: Path) -> Path:
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / "config.py").write_text("SMTP_HOST = ''\n", encoding="utf-8", newline="\n")
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'svc'\n", encoding="utf-8", newline="\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_smoke.py").write_text("def test_smoke():\n    assert True\n", encoding="utf-8", newline="\n")
    _init_git_repo(tmp_path)
    return tmp_path


async def _admit_and_start(backend: DurableObjectiveCycleApiBackend, *, key: str, candidate_sha: str = "abc1234") -> tuple[dict, dict]:
    admit = await backend.command(
        operation="admit_objective", tenant_id="tenant-1", actor_id="human-1", resource_id=None,
        body={
            "workhub_task_id": "wh-task-1", "task_class": "bounded_repair", "definition_of_done": ("done",),
            "repository_id": "svc", "allowed_files": (), "objective": "Run affected tests",
        },
        idempotency_key=f"admit-{key}", expected_version=None,
    )
    started = await backend.command(
        operation="start_cycle", tenant_id="tenant-1", actor_id="human-1", resource_id=admit["objective_id"],
        body={"candidate_sha": candidate_sha, "budgets": {"max_steps": 5}},
        idempotency_key=f"start-{key}", expected_version=None,
    )
    return admit, started


@pytest.mark.asyncio
async def test_start_cycle_mints_plan_identity(svc_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import json
    monkeypatch.setenv("HYEVE_AIX_SERVICE_OBJECTIVE_CYCLE_REPOSITORY_ROOTS", json.dumps({"svc": str(svc_repo).replace("\\", "/")}))
    backend = DurableObjectiveCycleApiBackend(mode="inprocess", real_execute_enabled=True, require_workhub_load=False)
    _, started = await _admit_and_start(backend, key="mint")
    assert started["plan_id"]
    assert started["plan_revision"] == 1
    assert started["approval_gate_id"]


@pytest.mark.asyncio
async def test_legacy_approval_without_gate_proof_still_completes(svc_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Backward compatibility: a caller that supplies only `approval_reference` (no
    `approval_gate_id`/`plan_revision`) keeps today's implicit-current-gate behavior."""
    import json
    monkeypatch.setenv("HYEVE_AIX_SERVICE_OBJECTIVE_CYCLE_REPOSITORY_ROOTS", json.dumps({"svc": str(svc_repo).replace("\\", "/")}))
    backend = DurableObjectiveCycleApiBackend(mode="inprocess", real_execute_enabled=True, require_workhub_load=False)
    _, started = await _admit_and_start(backend, key="legacy")
    approved = await backend.command(
        operation="validate_approval_reference", tenant_id="tenant-1", actor_id="human-1", resource_id=started["cycle_id"],
        body={"approval_reference": "workhub://approvals/legacy"},
        idempotency_key="approve-legacy", expected_version=started["version"],
    )
    assert approved["ok"] is True
    assert approved["real_execute"]["objective_status"] == "COMPLETED"


@pytest.mark.asyncio
async def test_approval_naming_wrong_gate_id_is_rejected(svc_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A random non-empty approval_reference plus a WRONG proven gate id must not
    authorize execution -- this is the exact gap the audit found: 'any non-empty
    approval_reference string is accepted while phase is WAITING_APPROVAL'."""
    import json
    monkeypatch.setenv("HYEVE_AIX_SERVICE_OBJECTIVE_CYCLE_REPOSITORY_ROOTS", json.dumps({"svc": str(svc_repo).replace("\\", "/")}))
    backend = DurableObjectiveCycleApiBackend(mode="inprocess", real_execute_enabled=True, require_workhub_load=False)
    _, started = await _admit_and_start(backend, key="wrong-gate")
    stale = await backend.command(
        operation="validate_approval_reference", tenant_id="tenant-1", actor_id="human-1", resource_id=started["cycle_id"],
        body={"approval_reference": "workhub://approvals/stale", "approval_gate_id": "gate_does_not_exist"},
        idempotency_key="approve-wrong-gate", expected_version=started["version"],
    )
    assert stale["ok"] is False
    assert stale["error"]["code"] == "STALE_APPROVAL_GATE"
    assert stale.get("real_execute") is None


@pytest.mark.asyncio
async def test_approval_naming_wrong_plan_revision_is_rejected(svc_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import json
    monkeypatch.setenv("HYEVE_AIX_SERVICE_OBJECTIVE_CYCLE_REPOSITORY_ROOTS", json.dumps({"svc": str(svc_repo).replace("\\", "/")}))
    backend = DurableObjectiveCycleApiBackend(mode="inprocess", real_execute_enabled=True, require_workhub_load=False)
    _, started = await _admit_and_start(backend, key="wrong-rev")
    wrong = await backend.command(
        operation="validate_approval_reference", tenant_id="tenant-1", actor_id="human-1", resource_id=started["cycle_id"],
        body={"approval_reference": "workhub://approvals/wrong-rev", "plan_revision": 99},
        idempotency_key="approve-wrong-rev", expected_version=started["version"],
    )
    assert wrong["ok"] is False
    assert wrong["error"]["code"] == "WRONG_PLAN_REVISION"


@pytest.mark.asyncio
async def test_approval_naming_the_correct_current_gate_succeeds(svc_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import json
    monkeypatch.setenv("HYEVE_AIX_SERVICE_OBJECTIVE_CYCLE_REPOSITORY_ROOTS", json.dumps({"svc": str(svc_repo).replace("\\", "/")}))
    backend = DurableObjectiveCycleApiBackend(mode="inprocess", real_execute_enabled=True, require_workhub_load=False)
    _, started = await _admit_and_start(backend, key="right-gate")
    approved = await backend.command(
        operation="validate_approval_reference", tenant_id="tenant-1", actor_id="human-1", resource_id=started["cycle_id"],
        body={
            "approval_reference": "workhub://approvals/right-gate",
            "approval_gate_id": started["approval_gate_id"],
            "plan_revision": started["plan_revision"],
        },
        idempotency_key="approve-right-gate", expected_version=started["version"],
    )
    assert approved["ok"] is True
    assert approved["real_execute"]["objective_status"] == "COMPLETED"
    assert approved["approved_gate_id"] == started["approval_gate_id"]
    assert approved["approved_resources"] == ["svc"]


@pytest.mark.asyncio
async def test_replan_bumps_plan_revision_and_invalidates_prior_approval(svc_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A replan is, by definition, a plan change: the gate/revision must change and a
    caller trying to reuse the pre-replan gate id must be rejected."""
    import json
    monkeypatch.setenv("HYEVE_AIX_SERVICE_OBJECTIVE_CYCLE_REPOSITORY_ROOTS", json.dumps({"svc": str(svc_repo).replace("\\", "/")}))
    backend = DurableObjectiveCycleApiBackend(mode="inprocess", real_execute_enabled=True, require_workhub_load=False)
    _, started = await _admit_and_start(backend, key="replan")
    gate_before = started["approval_gate_id"]
    revision_before = started["plan_revision"]

    replanned = await backend.command(
        operation="replan_cycle", tenant_id="tenant-1", actor_id="human-1", resource_id=started["cycle_id"],
        body={"replan_input": {"objective": "Run affected tests (revised scope)"}},
        idempotency_key="replan-1", expected_version=started["version"],
    )
    assert replanned["ok"] is True
    assert replanned["plan_revision"] == revision_before + 1
    assert replanned["approval_gate_id"] != gate_before

    stale_reuse = await backend.command(
        operation="validate_approval_reference", tenant_id="tenant-1", actor_id="human-1", resource_id=started["cycle_id"],
        body={"approval_reference": "workhub://approvals/reused", "approval_gate_id": gate_before},
        idempotency_key="approve-stale-reuse", expected_version=replanned["version"],
    )
    assert stale_reuse["ok"] is False
    assert stale_reuse["error"]["code"] == "STALE_APPROVAL_GATE"
