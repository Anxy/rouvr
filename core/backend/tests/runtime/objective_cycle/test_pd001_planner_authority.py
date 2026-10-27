"""AIX-PD-001: live planner authority, decomposition, and HTTP proofs.

Does not inject PlanRevisionV1 / StepContractV1. The production planner parser
builds those from stubbed model JSON. Dispatcher architecture is untouched.
"""
from __future__ import annotations

import json
import subprocess
import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.aix_core.runtime.executor.real_execute import RealExecuteResult
from backend.aix_core.runtime.executor.required_effects import infer_required_effects
from backend.aix_core.runtime.objective.api_security import CycleApiSecurityContext, CyclePermission
from backend.aix_core.runtime.objective.durable_api_backend import DurableObjectiveCycleApiBackend
from backend.aix_core.runtime.planner.plan_quality import evaluate_plan_quality
from backend.infrastructure.model_gateway.hyeve_integration_client import ChatCompletionResultV1
from backend.routers.objective_cycle_router import (
    get_cycle_api_backend,
    get_cycle_security_context,
    router,
)

_EVIDENCE_DIR = (
    Path(__file__).resolve().parents[5]
    / "docs"
    / "current"
    / "AIX Closed-Loop Execution Plan"
    / "planner-decomposition-sprint"
    / "evidence"
    / "runs"
)


_TMP_ROOT = Path(__file__).resolve().parents[5] / ".tmp" / "pd-001-tests"


def _git_repo(root: Path | None = None) -> Path:
    root = root or (_TMP_ROOT / uuid.uuid4().hex)
    root.mkdir(parents=True, exist_ok=True)
    (root / "config.py").write_text("RETRY_TIMEOUT = 30\nRETRY_LIMIT = 1\n", encoding="utf-8")
    (root / "service.py").write_text(
        "from config import RETRY_TIMEOUT\n\ndef retry_job():\n    return RETRY_TIMEOUT\n",
        encoding="utf-8",
    )
    (root / "router.py").write_text(
        "from service import retry_job\n\ndef jobs_api():\n    return retry_job()\n",
        encoding="utf-8",
    )
    (root / "retry_timeout.py").write_text("RETRY_TIMEOUT = 30\n", encoding="utf-8")
    (root / "validation.py").write_text(
        "ALLOWED_SPREADSHEETS = ('.xlsx', '.xls')\n\n"
        "def validate_upload(name: str) -> str:\n"
        "    return 'ok'\n",
        encoding="utf-8",
    )
    tests = root / "tests"
    tests.mkdir()
    (tests / "test_service.py").write_text(
        "from service import retry_job\n\ndef test_retry():\n    assert retry_job() == 30\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=root, check=True)
    return root


def _headers(key: str) -> dict[str, str]:
    return {
        "Authorization": "Bearer test",
        "X-Tenant-ID": "tenant-pd",
        "X-Actor-ID": "human",
        "Idempotency-Key": key,
    }


def _evidence_blob(system: str) -> dict[str, Any]:
    return json.loads(system.split("Evidence:\n", 1)[1])


def _steps_for_evidence(evidence: dict[str, Any]) -> list[dict[str, Any]]:
    files = [str(p) for p in (evidence.get("files_read") or []) if str(p).strip()]
    excerpts = evidence.get("file_excerpts") or {}
    for path in excerpts:
        if path not in files:
            files.append(str(path))
    if not files:
        files = ["service.py"]
    obligations = list(evidence.get("obligations") or [])
    if not obligations:
        obligations = [{"id": "ob-fallback", "description": evidence.get("objective") or "change"}]

    def _file_for(description: str) -> str:
        blob = description.casefold()
        if "test" in blob:
            return next((p for p in files if "test" in p.replace("\\", "/").casefold()), files[-1])
        if "api" in blob or "router" in blob or "endpoint" in blob:
            return next((p for p in files if "router" in p.casefold() or "api" in p.casefold()), files[0])
        if "config" in blob or "timeout" in blob or "limit" in blob or "setting" in blob:
            return next((p for p in files if "config" in p.casefold()), files[0])
        if "service" in blob or "consum" in blob:
            return next((p for p in files if "service" in p.casefold()), files[0])
        return files[min(len(files) - 1, 0)]

    steps: list[dict[str, Any]] = []
    for index, obligation in enumerate(obligations):
        description = str(obligation.get("description") or f"step {index}")
        target = _file_for(description)
        purpose = description if description.casefold() != str(evidence.get("objective") or "").casefold() else f"Implement {description[:80]}"
        steps.append(
            {
                "purpose": purpose,
                "target_scope": [target],
                "acceptance_criteria": [f"{description} is implemented and independently verifiable"],
                "validation_commands": [f"python -m py_compile {target}"],
                "prohibited_actions": ["do not expand scope"],
                "obligation_refs": [obligation["id"]],
                "required_effects": [{"kind": "RUN_DECLARED_VALIDATION"}],
                "depends_on": [index - 1] if index else [],
                "risk_tier": "LOW",
            }
        )
    return steps


def _install_live_planner_stub(
    monkeypatch: pytest.MonkeyPatch,
    repo: Path,
    *,
    execute_calls: list[str],
    nme_first: bool = False,
    nme_always: bool = False,
    force_legacy_one_step: bool = False,
) -> dict[str, int]:
    monkeypatch.setenv(
        "HYEVE_AIX_SERVICE_OBJECTIVE_CYCLE_REPOSITORY_ROOTS",
        json.dumps({"pd-repo": repo.as_posix()}),
    )
    monkeypatch.setattr(
        "backend.hyeve_aix_service.config.settings.INTELLIGENCE_LAYER_STEP_CONTRACT_PLANNER_ENABLED",
        True,
    )
    monkeypatch.setattr(
        "backend.hyeve_aix_service.config.settings.INTELLIGENCE_LAYER_SEMANTIC_VERIFIER_ENABLED",
        False,
    )
    calls = {"planner": 0, "execute": 0}

    async def chat_completion(**kwargs: Any) -> ChatCompletionResultV1:
        calls["planner"] += 1
        evidence = _evidence_blob(kwargs["messages"][0].content)
        files = list(evidence.get("files_read") or []) or list(evidence.get("file_excerpts") or {})
        if nme_always or (nme_first and calls["planner"] == 1) or not files:
            payload = {
                "reply": "Need grounded retry configuration and related tests",
                "needs_more_evidence": True,
                "requests": [
                    {"type": "find_symbol", "query": "existing retry configuration"},
                    {"type": "related_tests", "query": "integration job retry"},
                ],
            }
            return ChatCompletionResultV1(
                output_text=json.dumps(payload),
                provider="test",
                resolved_provider="test",
                model="test-model",
                usage={},
            )
        steps = _steps_for_evidence(evidence)
        if force_legacy_one_step:
            first = steps[0]
            payload = {
                "reply": "legacy one-step",
                "needs_more_evidence": False,
                **first,
            }
        else:
            payload = {
                "reply": "bounded multi-step plan",
                "needs_more_evidence": False,
                "steps": steps,
            }
        return ChatCompletionResultV1(
            output_text=json.dumps(payload),
            provider="test",
            resolved_provider="test",
            model="test-model",
            usage={},
        )

    async def execute(**kwargs: Any) -> RealExecuteResult:
        calls["execute"] += 1
        step = kwargs.get("step_contract")
        execute_calls.append(step.id if step is not None else "no-contract")
        return RealExecuteResult(
            ok=True,
            code="REAL_EXECUTE_OK",
            workspace="ws",
            evidence_refs=[],
            workhub_loaded=True,
            artifacts={},
            execution_status="COMPLETED",
            verification_status="PASSED",
            objective_status="COMPLETED",
            real_operations=1,
            target_files_modified=1,
            completion_gates={"gates": {"verification_ran": True}, "passed": True},
            diff_text=f"+ {step.id if step is not None else 'no-contract'} attempt-{calls['execute']}\n",
        )

    monkeypatch.setattr(
        "backend.aix_core.runtime.planner.step_contract_planner.chat_completion",
        chat_completion,
    )
    monkeypatch.setattr(
        "backend.aix_core.runtime.objective.durable_api_backend.run_real_execute",
        execute,
    )
    return calls


def _client(backend: DurableObjectiveCycleApiBackend) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.state.objective_cycle_api_backend = backend
    app.dependency_overrides[get_cycle_api_backend] = lambda: backend
    app.dependency_overrides[get_cycle_security_context] = lambda: CycleApiSecurityContext(
        gateway_id="gateway",
        tenant_id="tenant-pd",
        actor_id="human",
        roles=(CyclePermission.READ.value, CyclePermission.COMMAND.value),
        request_id="pd-request",
    )
    return TestClient(app)


def _persist_run(name: str, payload: dict[str, Any]) -> None:
    _EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    serializable = {key: value for key, value in payload.items() if key != "obligation_models"}
    (_EVIDENCE_DIR / f"{name}.json").write_text(
        json.dumps(serializable, indent=2, default=str),
        encoding="utf-8",
    )


def _run_http_cycle(
    client: TestClient,
    backend: DurableObjectiveCycleApiBackend,
    *,
    key: str,
    objective: str,
    allowed_files: tuple[str, ...] = (),
    definition_of_done: str | None = None,
) -> dict[str, Any]:
    admit = client.post(
        "/aix/objectives/admit",
        headers=_headers(f"admit-{key}"),
        json={
            "workhub_task_id": f"wh-{key}",
            "objective": objective,
            "task_class": "single_file_helper",
            "definition_of_done": [definition_of_done or objective],
            "repository_id": "pd-repo",
            "allowed_files": list(allowed_files),
        },
    )
    assert admit.status_code == 201, admit.text
    objective_id = admit.json()["objective_id"]
    started = client.post(
        f"/aix/objectives/{objective_id}/cycles",
        headers=_headers(f"start-{key}"),
        json={"candidate_sha": "abc1234", "budgets": {"max_steps": 12}},
    )
    assert started.status_code == 201, started.text
    cycle_id = started.json()["cycle_id"]
    approved = client.post(
        f"/aix/cycles/{cycle_id}/approval-reference",
        headers=_headers(f"approve-{key}"),
        json={"approval_reference": f"workhub://approvals/{key}"},
    )
    assert approved.status_code == 200, approved.text
    body = approved.json()
    dispatch_count = 0
    while body.get("dispatch_required") and dispatch_count < 12:
        dispatch_count += 1
        nxt = client.post(
            f"/aix/cycles/{cycle_id}/dispatch",
            headers=_headers(f"dispatch-{key}-{dispatch_count}"),
            json={},
        )
        assert nxt.status_code == 200, nxt.text
        body = nxt.json()
    plan = client.get(f"/aix/cycles/{cycle_id}/plan", headers=_headers(f"plan-{key}"))
    steps = client.get(f"/aix/cycles/{cycle_id}/steps", headers=_headers(f"steps-{key}"))
    return {
        "admit": admit.json(),
        "started": started.json(),
        "final": body,
        "plan": plan.json() if plan.status_code == 200 else {"status_code": plan.status_code, "text": plan.text},
        "steps": steps.json() if steps.status_code == 200 else {"status_code": steps.status_code, "text": steps.text},
        "objective_id": objective_id,
        "cycle_id": cycle_id,
        "obligations": [item.model_dump(mode="json") for item in backend._obligations.get(objective_id, ())],  # noqa: SLF001
        "obligation_count": len(backend._obligations.get(objective_id, ())),  # noqa: SLF001
        "obligation_models": backend._obligations.get(objective_id, ()),  # noqa: SLF001
        "trace": backend._planner_trace.get(objective_id, []),  # noqa: SLF001
        "evidence_revisions": backend._evidence_revisions.get(objective_id, []),  # noqa: SLF001
        "plan_revisions": [item.model_dump(mode="json") for item in backend._plan_revisions.get(objective_id, ())],  # noqa: SLF001
        "events": list(backend._cycles[cycle_id].events),  # noqa: SLF001
    }


def test_keyword_hints_do_not_force_extract_or_deletion() -> None:
    delete_kinds = {item.kind for item in infer_required_effects(purpose="Change DELETE endpoint behavior for failed jobs")}
    assert "EXTRACT_HELPER" not in delete_kinds
    boilerplate = {item.kind for item in infer_required_effects(purpose="remove dead/commented boilerplate comments")}
    assert "EXTRACT_HELPER" not in boilerplate
    helper = {item.kind for item in infer_required_effects(purpose="Add a helper endpoint for retry status")}
    assert "EXTRACT_HELPER" not in helper


def test_pd01_atomic_timeout_http(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _git_repo()
    captured: list[str] = []
    calls = _install_live_planner_stub(monkeypatch, repo, execute_calls=captured)
    backend = DurableObjectiveCycleApiBackend(mode="inprocess", real_execute_enabled=True, require_workhub_load=False)
    objective = "Change the existing retry timeout from 30 to 60 while preserving all other configuration."
    run = _run_http_cycle(
        _client(backend), backend, key="pd01", objective=objective, allowed_files=("config.py",),
    )
    assert run["obligation_count"] == 1
    accepted = [item for item in run["plan_revisions"] if item.get("status") == "ACCEPTED"]
    print(run["final"])
    assert accepted
    assert len(accepted[-1]["steps"]) == 1
    assert captured
    assert calls["execute"] >= 1
    _persist_run("PD01", {**run, "execute_calls": captured, "planner_calls": calls["planner"]})


def test_pd02_service_and_router_http(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _git_repo()
    captured: list[str] = []
    calls = _install_live_planner_stub(monkeypatch, repo, execute_calls=captured)
    backend = DurableObjectiveCycleApiBackend(mode="inprocess", real_execute_enabled=True, require_workhub_load=False)
    objective = "Add a retry operation to the integration service and expose it through the existing integration jobs API."
    run = _run_http_cycle(
        _client(backend),
        backend,
        key="pd02",
        objective=objective,
        allowed_files=("service.py", "router.py"),
    )
    assert run["obligation_count"] >= 2
    accepted = [item for item in run["plan_revisions"] if item.get("status") == "ACCEPTED"]
    assert accepted
    assert len(accepted[-1]["steps"]) >= 2
    assert len(captured) >= 2
    _persist_run("PD02", {**run, "execute_calls": captured, "planner_calls": calls["planner"]})


def test_pd03_config_service_tests_http(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _git_repo()
    captured: list[str] = []
    calls = _install_live_planner_stub(monkeypatch, repo, execute_calls=captured)
    backend = DurableObjectiveCycleApiBackend(mode="inprocess", real_execute_enabled=True, require_workhub_load=False)
    objective = "Add a configurable retry limit, make the service consume it, and add tests proving the configured behavior."
    run = _run_http_cycle(
        _client(backend),
        backend,
        key="pd03",
        objective=objective,
        allowed_files=("config.py", "service.py", "tests/test_service.py"),
    )
    assert run["obligation_count"] >= 3
    accepted = [item for item in run["plan_revisions"] if item.get("status") == "ACCEPTED"]
    assert accepted
    assert len(accepted[-1]["steps"]) >= 3
    assert len(captured) >= 3
    _persist_run("PD03", {**run, "execute_calls": captured, "planner_calls": calls["planner"]})


def test_pd03_legacy_one_step_is_quality_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _git_repo()
    captured: list[str] = []
    _install_live_planner_stub(monkeypatch, repo, execute_calls=captured, force_legacy_one_step=True)
    backend = DurableObjectiveCycleApiBackend(mode="inprocess", real_execute_enabled=True, require_workhub_load=False)
    objective = "Add a configurable retry limit, make the service consume it, and add tests proving the configured behavior."
    run = _run_http_cycle(
        _client(backend),
        backend,
        key="pd03-legacy",
        objective=objective,
        allowed_files=("config.py", "service.py", "tests/test_service.py"),
    )
    assert captured == []
    assert run["plan_revisions"] == []
    assert run["final"].get("error", {}).get("code") in {
        "COMPLEX_OBJECTIVE_COLLAPSED",
        "MISSING_REQUESTED_TEST_COVERAGE",
        "GENERIC_ACCEPTANCE_CRITERIA",
        "OBLIGATION_COVERAGE_INCOMPLETE",
        "NO_ACCEPTED_PLAN",
    }


def test_pd04_structured_evidence_then_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _git_repo()
    captured: list[str] = []
    calls = _install_live_planner_stub(monkeypatch, repo, execute_calls=captured, nme_first=True)
    backend = DurableObjectiveCycleApiBackend(mode="inprocess", real_execute_enabled=True, require_workhub_load=False)
    objective = "Make uploaded file validation reject unsupported spreadsheet formats with a clear error."
    run = _run_http_cycle(_client(backend), backend, key="pd04", objective=objective)
    assert run["evidence_revisions"]
    assert any(item.get("requests") for item in run["evidence_revisions"])
    assert calls["planner"] >= 2
    accepted = [item for item in run["plan_revisions"] if item.get("status") == "ACCEPTED"]
    assert accepted
    assert captured
    _persist_run("PD04", {**run, "execute_calls": captured, "planner_calls": calls["planner"]})


def test_pd05_nme_with_bound_files_never_executes(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _git_repo()
    captured: list[str] = []
    calls = _install_live_planner_stub(monkeypatch, repo, execute_calls=captured, nme_always=True)
    backend = DurableObjectiveCycleApiBackend(mode="inprocess", real_execute_enabled=True, require_workhub_load=False)
    objective = "Change DELETE endpoint behavior for failed integration jobs."
    run = _run_http_cycle(
        _client(backend),
        backend,
        key="pd05",
        objective=objective,
        allowed_files=("router.py",),
    )
    assert calls["execute"] == 0
    assert captured == []
    assert run["plan_revisions"] == []
    code = str((run["final"].get("error") or {}).get("code") or "")
    assert code in {
        "NEEDS_MORE_EVIDENCE",
        "REPEATED_IDENTICAL_EVIDENCE_REQUEST",
        "NO_ACCEPTED_PLAN",
        "TARGET_SCOPE_NOT_GROUNDED_IN_EVIDENCE",
    }
    _persist_run("PD05", {**run, "execute_calls": captured, "planner_calls": calls["planner"], "execute_count": calls["execute"]})


def test_pd06_delete_endpoint_is_not_code_deletion(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _git_repo()
    captured: list[str] = []
    _install_live_planner_stub(monkeypatch, repo, execute_calls=captured)
    backend = DurableObjectiveCycleApiBackend(mode="inprocess", real_execute_enabled=True, require_workhub_load=False)
    objective = "Change DELETE endpoint behavior so failed integration jobs return a structured error."
    run = _run_http_cycle(
        _client(backend), backend, key="pd06", objective=objective, allowed_files=("router.py",),
    )
    accepted = [item for item in run["plan_revisions"] if item.get("status") == "ACCEPTED"]
    assert accepted
    kinds = [
        effect.get("kind")
        for step in accepted[-1]["steps"]
        for effect in step.get("required_effects") or []
    ]
    assert "EXTRACT_HELPER" not in kinds
    blob = " ".join(step["purpose"].casefold() for step in accepted[-1]["steps"])
    assert "delete file" not in blob
    _persist_run("PD06", run)


def test_pd07_boilerplate_does_not_force_extract_helper(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _git_repo()
    captured: list[str] = []
    _install_live_planner_stub(monkeypatch, repo, execute_calls=captured)
    backend = DurableObjectiveCycleApiBackend(mode="inprocess", real_execute_enabled=True, require_workhub_load=False)
    objective = "Remove dead and commented boilerplate from the integration service without extracting helpers."
    run = _run_http_cycle(
        _client(backend), backend, key="pd07", objective=objective, allowed_files=("service.py",),
    )
    accepted = [item for item in run["plan_revisions"] if item.get("status") == "ACCEPTED"]
    assert accepted
    kinds = [
        effect.get("kind")
        for step in accepted[-1]["steps"]
        for effect in step.get("required_effects") or []
    ]
    assert "EXTRACT_HELPER" not in kinds
    _persist_run("PD07", run)


def test_pd08_api_service_tests_http(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _git_repo()
    captured: list[str] = []
    calls = _install_live_planner_stub(monkeypatch, repo, execute_calls=captured)
    backend = DurableObjectiveCycleApiBackend(mode="inprocess", real_execute_enabled=True, require_workhub_load=False)
    objective = "Add an endpoint for retrying failed integration jobs, reuse existing status update behavior, and cover it with tests."
    run = _run_http_cycle(
        _client(backend),
        backend,
        key="pd08",
        objective=objective,
        allowed_files=("router.py", "service.py", "tests/test_service.py"),
    )
    assert run["obligation_count"] >= 3
    accepted = [item for item in run["plan_revisions"] if item.get("status") == "ACCEPTED"]
    assert accepted
    assert len(accepted[-1]["steps"]) >= 3
    assert len(captured) >= 3
    _persist_run("PD08", {**run, "execute_calls": captured, "planner_calls": calls["planner"]})


def test_pd09_unknown_target_discovers_source(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _git_repo()
    captured: list[str] = []
    calls = _install_live_planner_stub(monkeypatch, repo, execute_calls=captured)
    backend = DurableObjectiveCycleApiBackend(mode="inprocess", real_execute_enabled=True, require_workhub_load=False)
    objective = "Increase the retry timeout used by the integration job runner and prove it with tests."
    run = _run_http_cycle(_client(backend), backend, key="pd09", objective=objective)
    accepted = [item for item in run["plan_revisions"] if item.get("status") == "ACCEPTED"]
    assert accepted, run["final"]
    scopes = [path for step in accepted[-1]["steps"] for path in step.get("target_scope") or []]
    assert scopes
    assert captured
    _persist_run("PD09", {**run, "execute_calls": captured, "planner_calls": calls["planner"]})


def test_pd10_complex_five_plus_steps_from_live_planner(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _git_repo()
    captured: list[str] = []
    calls = _install_live_planner_stub(monkeypatch, repo, execute_calls=captured)
    backend = DurableObjectiveCycleApiBackend(mode="inprocess", real_execute_enabled=True, require_workhub_load=False)
    objective = (
        "Add a configurable retry limit, make the service consume it, expose it through the existing jobs API, "
        "add tests proving the configured behavior, and preserve the existing public interface."
    )
    run = _run_http_cycle(
        _client(backend),
        backend,
        key="pd10",
        objective=objective,
        allowed_files=("config.py", "service.py", "router.py", "tests/test_service.py"),
    )
    assert run["obligation_count"] >= 5
    accepted = [item for item in run["plan_revisions"] if item.get("status") == "ACCEPTED"]
    assert accepted
    assert len(accepted[-1]["steps"]) >= 5
    client = _client(backend)
    extra = 0
    while len(set(captured)) < len(accepted[-1]["steps"]) and extra < 8:
        extra += 1
        nxt = client.post(
            f"/aix/cycles/{run['cycle_id']}/dispatch",
            headers=_headers(f"dispatch-pd10-extra-{extra}"),
            json={},
        )
        assert nxt.status_code == 200, nxt.text
        if nxt.json().get("phase") in {"COMPLETED_FOR_REVIEW", "BLOCKED", "FAILED"}:
            break
    assert len(set(captured)) >= 5
    assert calls["planner"] >= 1
    _persist_run("PD10", {**run, "execute_calls": captured, "planner_calls": calls["planner"]})
    quality = evaluate_plan_quality(
        plan=backend._plan_revisions[run["objective_id"]][-1],  # noqa: SLF001
        obligations=run["obligation_models"],
        objective=objective,
    )
    assert quality.ok is True
