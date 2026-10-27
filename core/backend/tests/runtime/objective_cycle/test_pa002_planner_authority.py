"""AIX-PA-002: planner authority negatives — no accepted plan means zero executor calls.

Positive live-model proofs are PA01–PA03 against the running HTTP process.
Stubs are allowed here only for negative gates (PA04–PA06 and listed executor=0 cases).
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
from backend.aix_core.runtime.objective.api_security import CycleApiSecurityContext, CyclePermission
from backend.aix_core.runtime.objective.durable_api_backend import DurableObjectiveCycleApiBackend
from backend.infrastructure.model_gateway.hyeve_integration_client import (
    ChatCompletionResultV1,
    HyeveIntegrationChatError,
)
from backend.routers.objective_cycle_router import (
    get_cycle_api_backend,
    get_cycle_security_context,
    router,
)

pytestmark = pytest.mark.no_plan_bootstrap


_TMP_ROOT = Path(__file__).resolve().parents[5] / ".tmp" / "pa-002-tests"


def _git_repo() -> Path:
    root = _TMP_ROOT / uuid.uuid4().hex
    root.mkdir(parents=True, exist_ok=True)
    (root / "service.py").write_text("RETRY_TIMEOUT = 30\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=root, check=True)
    return root


def _bind_repo(monkeypatch: pytest.MonkeyPatch, repo: Path) -> None:
    payload = json.dumps({"pd-repo": repo.as_posix()})
    monkeypatch.setenv("HYEVE_AIX_SERVICE_OBJECTIVE_CYCLE_REPOSITORY_ROOTS", payload)
    monkeypatch.setattr(
        "backend.hyeve_aix_service.config.settings.OBJECTIVE_CYCLE_REPOSITORY_ROOTS",
        payload,
    )
    monkeypatch.setattr(
        "backend.hyeve_aix_service.config.settings.INTELLIGENCE_LAYER_SEMANTIC_VERIFIER_ENABLED",
        False,
    )


def _headers(key: str) -> dict[str, str]:
    return {
        "Authorization": "Bearer test",
        "X-Tenant-ID": "tenant-pa",
        "X-Actor-ID": "human",
        "Idempotency-Key": key,
    }


def _client(backend: DurableObjectiveCycleApiBackend) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.state.objective_cycle_api_backend = backend
    app.dependency_overrides[get_cycle_api_backend] = lambda: backend
    app.dependency_overrides[get_cycle_security_context] = lambda: CycleApiSecurityContext(
        gateway_id="gateway",
        tenant_id="tenant-pa",
        actor_id="human",
        roles=(CyclePermission.READ.value, CyclePermission.COMMAND.value),
        request_id="pa-request",
    )
    return TestClient(app)


def _install_execute_counter(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    calls = {"execute": 0}

    async def execute(**kwargs: Any) -> RealExecuteResult:
        calls["execute"] += 1
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
        )

    monkeypatch.setattr(
        "backend.aix_core.runtime.objective.durable_api_backend.run_real_execute",
        execute,
    )
    return calls


def _planner_payload(payload: dict[str, Any]) -> ChatCompletionResultV1:
    return ChatCompletionResultV1(
        output_text=json.dumps(payload),
        provider="test",
        resolved_provider="test",
        model="test-model",
        usage={"prompt_tokens": 12, "completion_tokens": 8},
    )


def _run_cycle(client: TestClient, *, key: str, objective: str) -> dict[str, Any]:
    admit = client.post(
        "/aix/objectives/admit",
        headers=_headers(f"admit-{key}"),
        json={
            "workhub_task_id": f"wh-{key}",
            "objective": objective,
            "task_class": "single_file_helper",
            "definition_of_done": [objective],
            "repository_id": "pd-repo",
            "allowed_files": ["service.py"],
        },
    )
    assert admit.status_code == 201, admit.text
    objective_id = admit.json()["objective_id"]
    started = client.post(
        f"/aix/objectives/{objective_id}/cycles",
        headers=_headers(f"start-{key}"),
        json={"candidate_sha": "abc1234", "budgets": {"max_steps": 6}},
    )
    assert started.status_code == 201, started.text
    cycle_id = started.json()["cycle_id"]
    approved = client.post(
        f"/aix/cycles/{cycle_id}/approval-reference",
        headers=_headers(f"approve-{key}"),
        json={"approval_reference": f"workhub://approvals/{key}"},
    )
    assert approved.status_code == 200, approved.text
    plan = client.get(f"/aix/cycles/{cycle_id}/plan", headers=_headers(f"plan-{key}"))
    return {
        "admit": admit.json(),
        "started": started.json(),
        "final": approved.json(),
        "plan": plan.json(),
        "plan_status": plan.status_code,
        "objective_id": objective_id,
        "cycle_id": cycle_id,
    }


def test_pa04_planner_disabled_executor_count_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    _bind_repo(monkeypatch, _git_repo())
    calls = _install_execute_counter(monkeypatch)
    monkeypatch.setattr(
        "backend.hyeve_aix_service.config.settings.INTELLIGENCE_LAYER_STEP_CONTRACT_PLANNER_ENABLED",
        False,
    )
    planner_calls = {"n": 0}

    async def chat_completion(**kwargs: Any) -> ChatCompletionResultV1:
        planner_calls["n"] += 1
        return _planner_payload({"reply": "should not run", "needs_more_evidence": False, "steps": []})

    monkeypatch.setattr(
        "backend.aix_core.runtime.planner.step_contract_planner.chat_completion",
        chat_completion,
    )
    backend = DurableObjectiveCycleApiBackend(mode="inprocess", real_execute_enabled=True, require_workhub_load=False)
    run = _run_cycle(
        _client(backend),
        key="pa04",
        objective="Increase the retry timeout used by the integration job runner.",
    )
    assert planner_calls["n"] == 0
    assert calls["execute"] == 0
    assert run["plan"].get("error", {}).get("code") == "PLAN_NOT_FOUND"
    assert (run["final"].get("error") or {}).get("code") == "PLANNER_DISABLED"
    assert backend._plan_revisions.get(run["objective_id"]) in (None, [])  # noqa: SLF001


def test_plan_not_found_executor_count_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    _bind_repo(monkeypatch, _git_repo())
    calls = _install_execute_counter(monkeypatch)
    monkeypatch.setattr(
        "backend.hyeve_aix_service.config.settings.INTELLIGENCE_LAYER_STEP_CONTRACT_PLANNER_ENABLED",
        True,
    )

    async def chat_completion(**kwargs: Any) -> ChatCompletionResultV1:
        return _planner_payload(
            {
                "reply": "need more files",
                "needs_more_evidence": True,
                "requests": [{"type": "find_file", "query": "missing_module.py"}],
            }
        )

    monkeypatch.setattr(
        "backend.aix_core.runtime.planner.step_contract_planner.chat_completion",
        chat_completion,
    )
    backend = DurableObjectiveCycleApiBackend(mode="inprocess", real_execute_enabled=True, require_workhub_load=False)
    run = _run_cycle(_client(backend), key="pa-plan-missing", objective="Change a symbol that does not exist yet.")
    assert calls["execute"] == 0
    assert run["plan"].get("error", {}).get("code") == "PLAN_NOT_FOUND"
    assert (run["final"].get("error") or {}).get("code") in {
        "NEEDS_MORE_EVIDENCE",
        "REPEATED_IDENTICAL_EVIDENCE_REQUEST",
        "NO_ACCEPTED_PLAN",
        "TARGET_SCOPE_NOT_GROUNDED_IN_EVIDENCE",
    }


def test_planner_error_executor_count_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    _bind_repo(monkeypatch, _git_repo())
    calls = _install_execute_counter(monkeypatch)
    monkeypatch.setattr(
        "backend.hyeve_aix_service.config.settings.INTELLIGENCE_LAYER_STEP_CONTRACT_PLANNER_ENABLED",
        True,
    )

    async def chat_completion(**kwargs: Any) -> ChatCompletionResultV1:
        raise HyeveIntegrationChatError("INTEGRATION_UNAVAILABLE", detail="planner transport exploded")

    monkeypatch.setattr(
        "backend.aix_core.runtime.planner.step_contract_planner.chat_completion",
        chat_completion,
    )
    backend = DurableObjectiveCycleApiBackend(mode="inprocess", real_execute_enabled=True, require_workhub_load=False)
    run = _run_cycle(_client(backend), key="pa-err", objective="Increase the retry timeout used by the job runner.")
    assert calls["execute"] == 0
    code = str((run["final"].get("error") or {}).get("code") or "")
    assert code.startswith("PLANNER_MODEL_ERROR") or code == "NO_ACCEPTED_PLAN"


def test_pa06_invalid_collapsed_plan_executor_count_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    _bind_repo(monkeypatch, _git_repo())
    calls = _install_execute_counter(monkeypatch)
    monkeypatch.setattr(
        "backend.hyeve_aix_service.config.settings.INTELLIGENCE_LAYER_STEP_CONTRACT_PLANNER_ENABLED",
        True,
    )

    async def chat_completion(**kwargs: Any) -> ChatCompletionResultV1:
        return _planner_payload(
            {
                "reply": "one catch-all",
                "needs_more_evidence": False,
                "purpose": "do everything",
                "target_scope": ["service.py"],
                "acceptance_criteria": ["done"],
                "validation_commands": ["pytest"],
                "obligation_refs": [],
            }
        )

    monkeypatch.setattr(
        "backend.aix_core.runtime.planner.step_contract_planner.chat_completion",
        chat_completion,
    )
    backend = DurableObjectiveCycleApiBackend(mode="inprocess", real_execute_enabled=True, require_workhub_load=False)
    run = _run_cycle(
        _client(backend),
        key="pa06",
        objective=(
            "Add a configurable retry limit, make the service consume it, expose it through the jobs API, "
            "add tests proving the configured behavior, and preserve the existing public interface."
        ),
    )
    assert calls["execute"] == 0
    assert backend._plan_revisions.get(run["objective_id"]) in (None, [])  # noqa: SLF001
    code = str((run["final"].get("error") or {}).get("code") or "")
    assert code in {
        "COMPLEX_OBJECTIVE_COLLAPSED",
        "MISSING_REQUESTED_TEST_COVERAGE",
        "GENERIC_ACCEPTANCE_CRITERIA",
        "OBLIGATION_COVERAGE_INCOMPLETE",
        "NO_ACCEPTED_PLAN",
        "INVALID_STEP_CONTRACT",
    }
    calls_captured = backend._planner_calls.get(run["objective_id"]) or []  # noqa: SLF001
    assert calls_captured
    assert calls_captured[0].get("raw_response") is not None


def test_pa05_nme_executor_count_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    _bind_repo(monkeypatch, _git_repo())
    calls = _install_execute_counter(monkeypatch)
    monkeypatch.setattr(
        "backend.hyeve_aix_service.config.settings.INTELLIGENCE_LAYER_STEP_CONTRACT_PLANNER_ENABLED",
        True,
    )

    async def chat_completion(**kwargs: Any) -> ChatCompletionResultV1:
        return _planner_payload(
            {
                "reply": "need grounded retry configuration",
                "needs_more_evidence": True,
                "requests": [{"type": "find_symbol", "query": "retry timeout"}],
            }
        )

    monkeypatch.setattr(
        "backend.aix_core.runtime.planner.step_contract_planner.chat_completion",
        chat_completion,
    )
    backend = DurableObjectiveCycleApiBackend(mode="inprocess", real_execute_enabled=True, require_workhub_load=False)
    run = _run_cycle(_client(backend), key="pa05", objective="Change DELETE endpoint behavior for failed jobs.")
    assert calls["execute"] == 0
    code = str((run["final"].get("error") or {}).get("code") or "")
    assert code in {
        "NEEDS_MORE_EVIDENCE",
        "REPEATED_IDENTICAL_EVIDENCE_REQUEST",
        "NO_ACCEPTED_PLAN",
        "TARGET_SCOPE_NOT_GROUNDED_IN_EVIDENCE",
    }


def test_ungrounded_plan_executor_count_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    _bind_repo(monkeypatch, _git_repo())
    calls = _install_execute_counter(monkeypatch)
    monkeypatch.setattr(
        "backend.hyeve_aix_service.config.settings.INTELLIGENCE_LAYER_STEP_CONTRACT_PLANNER_ENABLED",
        True,
    )

    async def chat_completion(**kwargs: Any) -> ChatCompletionResultV1:
        return _planner_payload(
            {
                "reply": "invented file",
                "needs_more_evidence": False,
                "steps": [
                    {
                        "purpose": "edit invented module",
                        "target_scope": ["definitely_not_in_evidence.py"],
                        "acceptance_criteria": ["changed"],
                        "validation_commands": ["pytest"],
                        "obligation_refs": [],
                    }
                ],
            }
        )

    monkeypatch.setattr(
        "backend.aix_core.runtime.planner.step_contract_planner.chat_completion",
        chat_completion,
    )
    backend = DurableObjectiveCycleApiBackend(mode="inprocess", real_execute_enabled=True, require_workhub_load=False)
    run = _run_cycle(_client(backend), key="pa-ungrounded", objective="Increase the retry timeout.")
    assert calls["execute"] == 0
    code = str((run["final"].get("error") or {}).get("code") or "")
    assert "TARGET_SCOPE_NOT_GROUNDED" in code or code in {"NO_ACCEPTED_PLAN", "NEEDS_MORE_EVIDENCE"}


def test_legacy_one_step_does_not_bypass_accepted_plan_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    _bind_repo(monkeypatch, _git_repo())
    calls = _install_execute_counter(monkeypatch)
    monkeypatch.setattr(
        "backend.hyeve_aix_service.config.settings.INTELLIGENCE_LAYER_STEP_CONTRACT_PLANNER_ENABLED",
        True,
    )

    async def chat_completion(**kwargs: Any) -> ChatCompletionResultV1:
        return _planner_payload(
            {
                "reply": "legacy shape",
                "needs_more_evidence": False,
                "purpose": "do all three things",
                "target_scope": ["service.py"],
                "acceptance_criteria": ["done"],
                "validation_commands": ["pytest"],
            }
        )

    monkeypatch.setattr(
        "backend.aix_core.runtime.planner.step_contract_planner.chat_completion",
        chat_completion,
    )
    backend = DurableObjectiveCycleApiBackend(mode="inprocess", real_execute_enabled=True, require_workhub_load=False)
    run = _run_cycle(
        _client(backend),
        key="pa-legacy",
        objective="Update config, change service behavior, and add tests.",
    )
    assert calls["execute"] == 0
    accepted = [item for item in (backend._plan_revisions.get(run["objective_id"]) or []) if str(item.status) == "ACCEPTED"]  # noqa: SLF001
    assert accepted == []
