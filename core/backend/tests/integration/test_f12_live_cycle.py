"""AIX Feature 12 — Live Cycle Integration Test (Phase A/C).

Exercises the live cycle path through DurableObjectiveCycleApiBackend:
1. Admits Expense Tracker objective from fixture original_objective.txt
2. Bootstraps accepted plan
3. Executes cycle against unfixed fixture deliverable (PostgreSQL psycopg2 api.py)
4. TerminalizationGuard executes within live backend → GAPS_FOUND
5. Autonomous repair via backend commands → CERTIFIED → PASS
6. GapStore survives backend restart when persist_path configured
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest

from backend.aix_core.runtime.executor.real_execute import RealExecuteResult
from backend.aix_core.runtime.governance.completion_authority import (
    decision_from_cycle_outcome,
)
from backend.aix_core.runtime.objective.durable_api_backend import (
    DurableObjectiveCycleApiBackend,
)
from backend.tests.runtime.objective_cycle.plan_bootstrap import (
    seed_accepted_bootstrap_contract,
)

FIXTURE_ROOT = (
    Path(__file__).resolve().parents[3] / "fixtures" / "expense-tracker-failed-cycle"
)


def _sqlite_repair_api_py() -> str:
    return (
        "import os\n"
        "import sqlite3\n"
        "from fastapi import FastAPI\n"
        "DATABASE_URL = 'sqlite:///./expenses.db'\n"
        "app = FastAPI(title='Expense Tracker')\n"
        "@app.get('/health')\n"
        "def health(): return {'status': 'healthy'}\n"
        "@app.post('/register')\n"
        "def register(): return {'ok': True}\n"
        "@app.post('/login')\n"
        "def login(): return {'token': 'secret'}\n"
        "@app.get('/expenses')\n"
        "def list_expenses(): return []\n"
        "@app.post('/expenses')\n"
        "def create_expense(): return {'id': 1}\n"
    )


def _apply_sqlite_repair(deliverable_dest: Path) -> None:
    (deliverable_dest / "api.py").write_text(_sqlite_repair_api_py(), encoding="utf-8")
    test_dir = deliverable_dest / "tests"
    test_dir.mkdir(parents=True, exist_ok=True)
    (test_dir / "test_api.py").write_text(
        "from fastapi.testclient import TestClient\n"
        "import sys\n"
        "from pathlib import Path\n"
        "sys.path.insert(0, str(Path(__file__).parent.parent))\n"
        "from api import app\n"
        "client = TestClient(app)\n"
        "def test_health():\n"
        "    assert client.get('/health').status_code == 200\n",
        encoding="utf-8",
    )
    (deliverable_dest.parent / "expenses.db").write_bytes(b"SQLite format 3\x00" + b"\x00" * 64)


def _setup_fixture_workspace(tmp_path: Path) -> tuple[Path, Path, str]:
    obj_text = (FIXTURE_ROOT / "original_objective.txt").read_text(encoding="utf-8").strip()
    workspace = tmp_path / "cycle_workspace"
    deliverable_dest = workspace / "backend"
    deliverable_dest.mkdir(parents=True, exist_ok=True)
    for item in (FIXTURE_ROOT / "deliverable").iterdir():
        if item.is_file():
            shutil.copy(item, deliverable_dest / item.name)
    return workspace, deliverable_dest, obj_text


def _mock_execute_factory(
    workspace: Path,
    deliverable_dest: Path,
    *,
    repair_on_attempt: int = 2,
) -> tuple[Any, dict[str, int]]:
    counter = {"n": 0}

    async def _mock_real_execute(**kwargs: Any) -> RealExecuteResult:
        counter["n"] += 1
        if counter["n"] >= repair_on_attempt:
            _apply_sqlite_repair(deliverable_dest)
        return RealExecuteResult(
            ok=True,
            code="SUCCESS",
            workspace=str(workspace),
            evidence_refs=["file://workspace/evidence.json"],
            artifacts={"deliverable": str(deliverable_dest)},
            execution_status="COMPLETED",
            verification_status="PASSED",
            objective_status="COMPLETED",
            real_operations=1,
            target_files_modified=1,
        )

    return _mock_real_execute, counter


async def _run_admit_start_approve(
    backend: DurableObjectiveCycleApiBackend,
    *,
    tenant_id: str,
    obj_text: str,
    task_suffix: str,
) -> tuple[str, str, int]:
    admit_res = await backend.command(
        operation="admit_objective",
        tenant_id=tenant_id,
        actor_id="tester",
        resource_id=None,
        body={
            "workhub_task_id": f"task-live-{task_suffix}",
            "objective": obj_text,
            "task_class": "execute",
            "definition_of_done": [
                "FastAPI + Python backend",
                "SQLite",
                "registration/login",
                "budgets and expenses CRUD",
                "uvicorn",
            ],
            "repository_id": "hyeve-aix-service",
            "allowed_files": ("backend/api.py",),
            "project_id": f"proj-live-{task_suffix}",
            "task_number": 100 + int(task_suffix),
        },
        idempotency_key=f"admit-live-{task_suffix}",
        expected_version=None,
    )
    assert admit_res["ok"], admit_res
    obj_id = admit_res["objective_id"]

    start_res = await backend.command(
        operation="start_cycle",
        tenant_id=tenant_id,
        actor_id="tester",
        resource_id=obj_id,
        body={"candidate_sha": f"abc10{task_suffix}2345"},
        idempotency_key=f"start-live-{task_suffix}",
        expected_version=None,
    )
    assert start_res["ok"], start_res
    cycle_id = start_res["cycle_id"]

    seed_accepted_bootstrap_contract(
        backend,
        objective_id=obj_id,
        cycle_id=cycle_id,
        target="backend/api.py",
        purpose="Implement complete Expense Tracker with FastAPI and SQLite",
    )

    approve_res = await backend.command(
        operation="validate_approval_reference",
        tenant_id=tenant_id,
        actor_id="tester",
        resource_id=cycle_id,
        body={"approval_reference": f"workhub://approvals/appr-live-{task_suffix}"},
        idempotency_key=f"approve-live-{task_suffix}",
        expected_version=start_res["version"],
    )
    assert approve_res.get("ok", True) or approve_res.get("phase") == "GAPS_FOUND"
    return obj_id, cycle_id, start_res["version"]


@pytest.mark.asyncio
async def test_f12_live_cycle_unfixed_fixture_terminates_in_gaps_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, deliverable_dest, obj_text = _setup_fixture_workspace(tmp_path)
    mock_execute, _ = _mock_execute_factory(workspace, deliverable_dest, repair_on_attempt=999)
    monkeypatch.setattr(
        "backend.aix_core.runtime.objective.durable_api_backend.run_real_execute",
        mock_execute,
    )

    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess",
        real_execute_enabled=True,
        require_workhub_load=False,
        gap_store_path=tmp_path / "gaps.json",
    )

    obj_id, cycle_id, _version = await _run_admit_start_approve(
        backend, tenant_id="tenant-live-1", obj_text=obj_text, task_suffix="1"
    )

    cycle = backend._cycles[cycle_id]
    term_dec = backend._terminalization_decisions.get(cycle_id)
    assert term_dec is not None
    assert term_dec.decision == "DENY_NEEDS_REPAIR"
    assert cycle.phase == "GAPS_FOUND"
    assert backend._gap_store.count_blocking_gaps(obj_id) > 0

    comp_dec = decision_from_cycle_outcome(
        tenant_id="tenant-live-1",
        objective_id=obj_id,
        cycle_id=cycle_id,
        terminal_status="COMPLETED_FOR_REVIEW",
        evidence_refs=["file://workspace/evidence.json"],
        terminalization_decision=term_dec,
    )
    assert comp_dec.verdict == "INSUFFICIENT_EVIDENCE"


@pytest.mark.asyncio
async def test_f12_live_cycle_autonomous_repair_reaches_certified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Full repair loop via backend commands only (no private helper calls)."""
    workspace, deliverable_dest, obj_text = _setup_fixture_workspace(tmp_path)
    mock_execute, counter = _mock_execute_factory(workspace, deliverable_dest, repair_on_attempt=2)
    monkeypatch.setattr(
        "backend.aix_core.runtime.objective.durable_api_backend.run_real_execute",
        mock_execute,
    )

    gap_path = tmp_path / "gaps-live.json"
    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess",
        real_execute_enabled=True,
        require_workhub_load=False,
        gap_store_path=gap_path,
    )

    obj_id, cycle_id, version = await _run_admit_start_approve(
        backend, tenant_id="tenant-live-2", obj_text=obj_text, task_suffix="2"
    )
    assert backend._cycles[cycle_id].phase == "GAPS_FOUND"

    repair_res = await backend.command(
        operation="initiate_gap_repair",
        tenant_id="tenant-live-2",
        actor_id="tester",
        resource_id=cycle_id,
        body={"repair_attempt": 1},
        idempotency_key="repair-plan-2",
        expected_version=backend._cycles[cycle_id].version,
    )
    assert repair_res["ok"], repair_res
    assert repair_res["repair_status"] == "REPAIR_PLANNED"
    version = repair_res["version"]

    dispatch_res = await backend.command(
        operation="dispatch_ready_step",
        tenant_id="tenant-live-2",
        actor_id="tester",
        resource_id=cycle_id,
        body={},
        idempotency_key="repair-dispatch-2",
        expected_version=version,
    )
    assert dispatch_res.get("ok", True), dispatch_res
    assert counter["n"] >= 2, "repair dispatch must invoke run_real_execute"

    reverify_res = await backend.command(
        operation="reverify_and_terminalize",
        tenant_id="tenant-live-2",
        actor_id="tester",
        resource_id=cycle_id,
        body={},
        idempotency_key="reverify-2",
        expected_version=backend._cycles[cycle_id].version,
    )
    assert reverify_res["ok"], reverify_res
    assert reverify_res["terminalization_decision"] == "ALLOW_CERTIFIED"

    cycle = backend._cycles[cycle_id]
    assert cycle.phase == "CERTIFIED"
    assert backend._gap_store.count_blocking_gaps(obj_id) == 0

    term_dec_final = backend._terminalization_decisions.get(cycle_id)
    final_completion = decision_from_cycle_outcome(
        tenant_id="tenant-live-2",
        objective_id=obj_id,
        cycle_id=cycle_id,
        terminal_status="CERTIFIED",
        evidence_refs=["file://workspace/evidence.json"],
        terminalization_decision=term_dec_final,
    )
    assert final_completion.verdict == "PASS"


@pytest.mark.asyncio
async def test_f12_gap_store_survives_backend_restart(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """AC-25: gaps written during live cycle survive a new backend instance."""
    workspace, deliverable_dest, obj_text = _setup_fixture_workspace(tmp_path)
    mock_execute, _ = _mock_execute_factory(workspace, deliverable_dest, repair_on_attempt=999)
    monkeypatch.setattr(
        "backend.aix_core.runtime.objective.durable_api_backend.run_real_execute",
        mock_execute,
    )

    gap_path = tmp_path / "gaps-restart.json"
    backend1 = DurableObjectiveCycleApiBackend(
        mode="inprocess",
        real_execute_enabled=True,
        require_workhub_load=False,
        gap_store_path=gap_path,
    )
    obj_id, cycle_id, _ = await _run_admit_start_approve(
        backend1, tenant_id="tenant-live-3", obj_text=obj_text, task_suffix="3"
    )
    assert backend1._gap_store.count_blocking_gaps(obj_id) > 0
    assert gap_path.exists()

    backend2 = DurableObjectiveCycleApiBackend(
        mode="inprocess",
        real_execute_enabled=True,
        require_workhub_load=False,
        gap_store_path=gap_path,
    )
    assert backend2._gap_store.count_blocking_gaps(obj_id) > 0
    loaded = backend2._gap_store.load(obj_id)
    assert any(g.ov_id == "OV-002" for g in loaded)


@pytest.mark.asyncio
async def test_f12_live_cycle_repair_cycle_single_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Full repair loop via single repair_cycle backend command."""
    workspace, deliverable_dest, obj_text = _setup_fixture_workspace(tmp_path)
    mock_execute, counter = _mock_execute_factory(workspace, deliverable_dest, repair_on_attempt=2)
    monkeypatch.setattr(
        "backend.aix_core.runtime.objective.durable_api_backend.run_real_execute",
        mock_execute,
    )

    gap_path = tmp_path / "gaps-live-repair-cycle.json"
    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess",
        real_execute_enabled=True,
        require_workhub_load=False,
        gap_store_path=gap_path,
    )

    obj_id, cycle_id, _version = await _run_admit_start_approve(
        backend, tenant_id="tenant-live-4", obj_text=obj_text, task_suffix="4"
    )
    assert backend._cycles[cycle_id].phase == "GAPS_FOUND"

    repair_res = await backend.command(
        operation="repair_cycle",
        tenant_id="tenant-live-4",
        actor_id="tester",
        resource_id=cycle_id,
        body={"repair_attempt": 1},
        idempotency_key="repair-cycle-4",
        expected_version=backend._cycles[cycle_id].version,
    )
    assert repair_res["ok"], repair_res
    assert repair_res["terminalization_decision"] == "ALLOW_CERTIFIED"
    assert counter["n"] >= 2

    cycle = backend._cycles[cycle_id]
    assert cycle.phase == "CERTIFIED"
    assert backend._gap_store.count_blocking_gaps(obj_id) == 0


@pytest.mark.asyncio
async def test_f12_http_repair_cycle_via_objective_cycle_router(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """HTTP :8100-equivalent path — repair via objective_cycle_router (TestClient)."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.aix_core.runtime.objective.api_security import CycleApiSecurityContext, CyclePermission
    from backend.routers.objective_cycle_router import get_cycle_api_backend, get_cycle_security_context, router

    workspace, deliverable_dest, obj_text = _setup_fixture_workspace(tmp_path)
    mock_execute, _counter = _mock_execute_factory(workspace, deliverable_dest, repair_on_attempt=2)
    monkeypatch.setattr(
        "backend.aix_core.runtime.objective.durable_api_backend.run_real_execute",
        mock_execute,
    )

    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess",
        real_execute_enabled=True,
        require_workhub_load=False,
        gap_store_path=tmp_path / "gaps-http.json",
    )
    app = FastAPI()
    app.include_router(router)
    app.state.objective_cycle_api_backend = backend
    app.dependency_overrides[get_cycle_api_backend] = lambda: backend
    app.dependency_overrides[get_cycle_security_context] = lambda: CycleApiSecurityContext(
        gateway_id="aix-bff",
        tenant_id="tenant-http-1",
        actor_id="tester",
        roles=(CyclePermission.READ.value, CyclePermission.COMMAND.value),
        request_id="http-repair-1",
    )
    client = TestClient(app)

    obj_id, cycle_id, _ = await _run_admit_start_approve(
        backend, tenant_id="tenant-http-1", obj_text=obj_text, task_suffix="5"
    )
    assert backend._cycles[cycle_id].phase == "GAPS_FOUND"

    repair = client.post(
        f"/aix/cycles/{cycle_id}/repair",
        headers={
            "Idempotency-Key": "http-repair-cycle-5",
            "If-Match": str(backend._cycles[cycle_id].version),
        },
        json={"repair_attempt": 1},
    )
    assert repair.status_code == 200, repair.text
    body = repair.json()
    assert body.get("ok"), body
    assert body.get("terminalization_decision") == "ALLOW_CERTIFIED"
    assert backend._cycles[cycle_id].phase == "CERTIFIED"
    assert backend._gap_store.count_blocking_gaps(obj_id) == 0


@pytest.mark.asyncio
async def test_f12_gap_store_postgres_via_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    """Postgres GapStore round-trip when backend is constructed with pg_conn."""
    from unittest.mock import AsyncMock

    from backend.aix_core.runtime.verification.gap import GapStore, GapV1

    mock_conn = AsyncMock()
    stored: list[dict] = []

    async def mock_execute(query: str, *args: Any) -> None:
        if "INSERT INTO gaps" in query:
            stored.append({"id": args[0], "objective_id": args[1], "payload": args[11]})

    async def mock_fetch(query: str, *args: Any) -> list[dict]:
        if "SELECT payload FROM gaps" in query:
            return [{"payload": r["payload"]} for r in stored if r["objective_id"] == args[0]]
        return []

    mock_conn.execute.side_effect = mock_execute
    mock_conn.fetch.side_effect = mock_fetch

    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess",
        real_execute_enabled=False,
        require_workhub_load=False,
        gap_store_pg_conn=mock_conn,
    )
    gap = GapV1(
        gap_id="gap-backend-pg-1",
        objective_id="obj_pg_backend",
        verification_id="v-pg-1",
        ov_id="OV-002",
        category="OBJECTIVE_DEVIATION",
        expected="SQLite",
        observed="PostgreSQL",
        severity="BLOCKER",
        status="OPEN",
    )
    await backend._persist_gap(gap)
    assert len(stored) == 1

    fresh = GapStore(pg_conn=mock_conn)
    loaded = await fresh.load_pg("obj_pg_backend")
    assert len(loaded) == 1
    assert loaded[0].gap_id == "gap-backend-pg-1"
