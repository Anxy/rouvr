from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.aix_orchestrator_v2.models import AixOrchestratorV2RunState
from backend.aix_orchestrator_v2.state_store import AixOrchestratorV2StateStore
from backend.routers import aix_orchestrator_v2_router as router_module
from backend.routers.aix_orchestrator_v2_router import router


class _FakeTemporalRuntime:
    def _sdk_loader(self):
        return object()

    async def start_v2_workflow(self, *, payload: dict):
        return {
            "workflow": "AixOrchestratorV2Workflow",
            "workflow_id": payload["workflow_id"],
            "run_id": "temporal-run-1",
            "task_queue": "aix.execution.current",
            "namespace": "hyeve-aix",
        }

    @property
    def binding(self):
        return type("Binding", (), {"execution_queue": "aix.execution.current"})()


class _FailingTemporalRuntime:
    def _sdk_loader(self):
        return object()

    async def start_v2_workflow(self, *, payload: dict):
        raise RuntimeError("connection refused")

    @property
    def binding(self):
        return type("Binding", (), {"execution_queue": "aix.execution.current"})()


def test_temporal_mode_starts_workflow_and_persists_state(tmp_path, monkeypatch):
    store = AixOrchestratorV2StateStore(root=tmp_path)
    monkeypatch.setattr(router_module, "state_store", store)

    async def _fake_load_task(self, *, project_id: str, task_number: int, workspace_root: str, tenant_id: str | None = None):
        state = AixOrchestratorV2RunState(
            task_id="task-1",
            task_payload={"id": "task-1", "task_number": task_number, "title": "Sprint 2"},
            packet={
                "task_ref": {"project_id": project_id, "task_number": task_number, "task_id": "task-1"},
                "workspace_ref": {"workspace_root": workspace_root},
            },
        )
        store.save(state)
        return state

    monkeypatch.setattr(router_module.AixOrchestratorV2Service, "load_task", _fake_load_task)

    app = FastAPI()
    app.include_router(router)
    app.state.temporal_runtime = _FakeTemporalRuntime()

    client = TestClient(app)
    response = client.post(
        "/api/aix/orchestrator-v2/runs",
        headers={"Authorization": "Bearer test"},
        json={
            "project_id": "bh-migrated-7-aix",
            "task_number": 2627,
            "workspace_root": str(tmp_path),
            "mode": "temporal",
        },
    )

    # C004-T03: mutation authority retired; GET adapters remain.
    assert response.status_code == 410
    detail = response.json().get("detail") or response.json()
    assert (detail.get("code") if isinstance(detail, dict) else None) == router_module.ORCHESTRATOR_V2_RETIREMENT_CODE


def test_run_retrieval_endpoint_reads_durable_state(tmp_path, monkeypatch):
    store = AixOrchestratorV2StateStore(root=tmp_path)
    monkeypatch.setattr(router_module, "state_store", store)
    state = AixOrchestratorV2RunState(
        task_id="task-2",
        run_id="aixv2-retrieve-1",
        current_phase="planner",
        status="blocked",
        task_payload={"id": "task-2", "task_number": 2627},
        plan={
            "status": "blocked",
            "blocked_reason": "RECON_TARGET_FILE_REQUIRED",
            "validation_failures": ["RECON_TARGET_FILE_REQUIRED"],
            "input_summary": {"task_title": "Sprint 2 temporal lane"},
            "raw_output_text": '{"steps":[]}',
            "normalized_plan": [],
        },
        failure={
            "classification": "POLICY_FAIL",
            "phase": "planner",
            "code": "RECON_TARGET_FILE_REQUIRED",
            "message": "target file missing",
        },
        artifacts=[
            {
                "name": "planner_trace_ref",
                "ref": "local://planner-trace.json",
                "payload": {"trace_artifact_ref": "local://planner-trace.json"},
            }
        ],
    )
    store.save(state)

    app = FastAPI()
    app.include_router(router)

    client = TestClient(app)
    response = client.get(
        "/api/aix/orchestrator-v2/runs/aixv2-retrieve-1",
        headers={"Authorization": "Bearer test"},
    )

    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["current_phase"] == "planner"
    assert payload["summary"]["phase_failure_classification"] == "plan_invalid"
    assert payload["phase_results"]["planner"]["validation_failures"] == ["RECON_TARGET_FILE_REQUIRED"]
    assert payload["phase_results"]["planner"]["trace_artifact_ref"] == "local://planner-trace.json"
    assert payload["phase_results"]["planner"]["raw_output"]["present"] is True
    assert payload["phase_results"]["planner"]["normalized_plan"]["step_count"] == 0
    assert payload["retrieval"]["task_number"] == 2627
    assert "packet" not in payload
    assert "task_payload" not in payload


def test_latest_task_run_endpoint_returns_durable_phase_results(tmp_path, monkeypatch):
    store = AixOrchestratorV2StateStore(root=tmp_path)
    monkeypatch.setattr(router_module, "state_store", store)
    state = AixOrchestratorV2RunState(
        task_id="task-4",
        run_id="aixv2-latest-1",
        current_phase="writeback",
        status="writeback_complete",
        task_payload={"id": "task-4", "task_number": 2761},
        execution={
            "status": "completed",
            "run_result": {"status": "completed"},
            "changed_files": ["backend/a.py"],
            "commands": [{"command_text": "pytest tests/test_a.py -q"}],
            "tests": {},
            "diff_refs": ["local://diff"],
            "trace": {},
            "envelope": {},
        },
        verification={
            "classification": "success",
            "summary": "ok",
            "checks": [{"name": "declared_validation", "passed": True}],
            "reasons": [],
            "verifier_decision": {},
        },
        writeback={
            "writeback": {"status": "completed"},
            "transition_ok": True,
            "evidence_ok": True,
        },
        artifacts=[
            {"name": "execution_summary", "payload": {"status": "completed"}},
            {"name": "recon", "payload": {}},
            {"name": "planner_input_summary", "payload": {}},
            {"name": "planner_raw", "payload": {}},
            {"name": "normalized_plan", "payload": {}},
            {"name": "verifier_result", "payload": {}},
            {"name": "writeback", "payload": {}},
            {"name": "diff_refs", "ref": "local://diff", "payload": {"diff_refs": ["local://diff"]}},
        ],
    )
    store.save(state)

    app = FastAPI()
    app.include_router(router)

    client = TestClient(app)
    response = client.get(
        "/api/aix/orchestrator-v2/tasks/task-4/runs/latest",
        headers={"Authorization": "Bearer test"},
    )

    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["summary"]["classification"] == "success"
    assert payload["phase_results"]["execution"]["changed_files"] == ["backend/a.py"]
    assert payload["phase_results"]["execution"]["commands"][0]["command_text"] == "pytest tests/test_a.py -q"
    assert payload["phase_results"]["writeback"]["transition_ok"] is True
    assert payload["retrieval"]["artifact_ref_count"] == 1
    assert "packet" not in payload
    assert "task_payload" not in payload


def test_temporal_mode_start_failure_persists_blocked_state(tmp_path, monkeypatch):
    store = AixOrchestratorV2StateStore(root=tmp_path)
    monkeypatch.setattr(router_module, "state_store", store)

    async def _fake_load_task(self, *, project_id: str, task_number: int, workspace_root: str, tenant_id: str | None = None):
        state = AixOrchestratorV2RunState(
            task_id="task-3",
            task_payload={"id": "task-3", "task_number": task_number, "title": "Sprint 2 unhappy path"},
            packet={
                "task_ref": {"project_id": project_id, "task_number": task_number, "task_id": "task-3"},
                "workspace_ref": {"workspace_root": workspace_root},
            },
        )
        store.save(state)
        return state

    monkeypatch.setattr(router_module.AixOrchestratorV2Service, "load_task", _fake_load_task)

    app = FastAPI()
    app.include_router(router)
    app.state.temporal_runtime = _FailingTemporalRuntime()

    client = TestClient(app)
    response = client.post(
        "/api/aix/orchestrator-v2/runs",
        headers={"Authorization": "Bearer test"},
        json={
            "project_id": "bh-migrated-7-aix",
            "task_number": 2631,
            "workspace_root": str(tmp_path),
            "mode": "temporal",
        },
    )

    # C004-T03: mutation fails closed before Temporal start side effects.
    assert response.status_code == 410
    assert store.get_latest_by_task_id("task-3") is None
