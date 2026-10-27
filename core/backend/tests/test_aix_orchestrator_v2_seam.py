from __future__ import annotations

import asyncio

from backend.aix_orchestrator_v2.service import AixOrchestratorV2Service
from backend.aix_orchestrator_v2.state_store import AixOrchestratorV2StateStore


class _FakeWorkHubClient:
    def __init__(self, *, task_payload: dict):
        self._task_payload = dict(task_payload)

    async def get_task_by_number(self, *, project_id: str, task_number: int, tenant_id: str | None = None):
        payload = dict(self._task_payload)
        payload.setdefault("project_id", project_id)
        payload.setdefault("task_number", task_number)
        return payload

    async def get_task_ai_context_markdown(
        self, *, project_id: str, task_number: int, tenant_id: str | None = None
    ) -> str | None:
        # Real client is best-effort and returns None on any failure; the fake mirrors
        # that default (no briefing) rather than fabricating one no test here asserts on.
        return None

    async def list_task_execution_contracts(
        self, *, project_id: str, task_number: int, tenant_id: str | None = None
    ):
        return [dict(self._task_payload.get("active_execution_contract") or {})]


async def _fake_writeback_v2_run(**_kwargs):
    return {
        "writeback": {"status": "ok"},
        "verification_evaluation": {"decision": "stored"},
        "verification_error": None,
        "transition_ok": True,
        "transition_error": None,
        "evidence_ok": True,
        "evidence_error": None,
    }


async def _fake_writeback_transition_failure(**_kwargs):
    return {
        "writeback": {"status": "ok"},
        "verification_evaluation": {"decision": "stored"},
        "verification_error": None,
        "transition_ok": False,
        "transition_error": "transition failed",
        "evidence_ok": True,
        "evidence_error": None,
    }


async def _fake_writeback_evidence_failure(**_kwargs):
    return {
        "writeback": {"status": "ok"},
        "verification_evaluation": {"decision": "stored"},
        "verification_error": None,
        "transition_ok": True,
        "transition_error": None,
        "evidence_ok": False,
        "evidence_error": "evidence failed",
    }


async def _fake_recon(**_kwargs):
    return {
        "entity_query": "example.symbol",
        "execution_contract": {},
        "context_error": None,
        "codeintel": {},
        "target_file": "backend/services/example.py",
        "target_symbol": "example.symbol",
        "target_files": ["backend/services/example.py"],
        "anchor": {
            "file_path": "backend/services/example.py",
            "snapshot_id": "snap",
            "commit_sha": "sha",
            "symbol_source_hash": "hash",
        },
        "symbol_ranges": [],
        "anchor_ranges": [],
        "validation_target": "pytest tests/test_example.py",
        "related_tests": ["tests/test_example.py"],
        "dependency_context": {},
        "slim_packet_inputs": {"task_number": 2627},
    }


def _task_payload():
    return {
        "id": "task-seam-1",
        "task_number": 2627,
        "title": "Sprint 2 seam",
        "description": "exercise v2 seam",
        "active_execution_contract": {"id": "contract-1"},
    }


def test_v2_service_inline_happy_path_persists_state(tmp_path, monkeypatch):
    from backend.aix_orchestrator_v2 import service as service_module
    from backend.legacy.aix_orchestrator_v2 import writeback_service as writeback_service_module

    store = AixOrchestratorV2StateStore(root=tmp_path)
    svc = AixOrchestratorV2Service(workhub_client=_FakeWorkHubClient(task_payload=_task_payload()), run_store=store)
    monkeypatch.setattr(service_module, "build_recon", _fake_recon)
    monkeypatch.setattr(
        service_module,
        "plan_task",
        lambda **_kwargs: {
            "status": "planned",
            "steps": [{"tool": "get_status", "args": {}}],
            "planner_packet": {"acceptance_criteria": ["tests pass"]},
            "planner_trace": {"local_trace_ref": "local://planner-trace"},
            "declared_validation": "pytest tests/test_example.py",
            "file_targets": ["backend/services/example.py"],
            "symbol_targets": ["example.symbol"],
            "assumptions": [],
            "blocked_reason": None,
            "input_summary": {"planner_prompt": "prompt"},
            "raw_output_text": '{"steps":[]}',
            "normalized_plan": [{"tool": "get_status", "args": {}}],
            "validation_failures": [],
            "trace_artifact_ref": "local://planner-trace",
        },
    )
    monkeypatch.setattr(
        service_module,
        "execute_plan",
        lambda **_kwargs: {
            "status": "completed",
            "run_result": {"status": "completed"},
            "changed_files": ["backend/services/example.py"],
            "commands": [{"ok": True, "command_text": "pytest tests/test_example.py"}],
            "tests": {"detected": True},
            "diff_refs": ["local://diff"],
            "failure": None,
            "trace": {"evidence_refs": ["local://diff"]},
            "envelope": {"envelope": {"specversion": "1.0"}},
        },
    )
    monkeypatch.setattr(
        service_module,
        "verify_run",
        lambda **_kwargs: {
            "classification": "success",
            "summary": "ok",
            "checks": [{"name": "declared_validation", "passed": True}],
            "reasons": [],
            "verifier_decision": {"decision": "verified"},
        },
    )
    monkeypatch.setattr(writeback_service_module, "writeback_v2_run", _fake_writeback_v2_run)

    state = asyncio.run(
        svc.run_inline(
            tenant_id="default",
            project_id="bh-migrated-7-aix",
            task_number=2627,
            workspace_root=str(tmp_path),
            repo_key="hyeve-aix-service",
            explicit_profile_id=None,
            actor_id="tester",
            writeback=True,
        )
    )

    saved = store.get_by_run_id(state.run_id)
    assert state.current_phase == "writeback"
    assert state.status == "writeback_complete"
    assert saved is not None
    assert saved.execution is not None
    assert saved.verification is not None
    artifact_names = [item.name for item in saved.artifacts]
    assert "recon" in artifact_names
    assert "planner_input_summary" in artifact_names
    assert "planner_raw" in artifact_names
    assert "normalized_plan" in artifact_names
    assert "execution_summary" in artifact_names
    assert "changed_files" in artifact_names
    assert "diff_refs" in artifact_names
    assert "verifier_result" in artifact_names
    assert "packet_audit" in artifact_names
    assert "run_summary" in artifact_names


def test_v2_service_inline_non_happy_path_classifies_failure(tmp_path, monkeypatch):
    from backend.aix_orchestrator_v2 import service as service_module
    from backend.legacy.aix_orchestrator_v2 import writeback_service as writeback_service_module

    store = AixOrchestratorV2StateStore(root=tmp_path)
    svc = AixOrchestratorV2Service(workhub_client=_FakeWorkHubClient(task_payload=_task_payload()), run_store=store)
    monkeypatch.setattr(service_module, "build_recon", _fake_recon)
    monkeypatch.setattr(
        service_module,
        "plan_task",
        lambda **_kwargs: {
            "status": "blocked",
            "steps": [],
            "planner_packet": {"acceptance_criteria": ["tests pass"]},
            "planner_trace": {"local_trace_ref": "local://planner-trace"},
            "declared_validation": "pytest tests/test_example.py",
            "file_targets": ["backend/services/example.py"],
            "symbol_targets": ["example.symbol"],
            "assumptions": [],
            "blocked_reason": "validation missing",
            "input_summary": {"planner_prompt": "prompt"},
            "raw_output_text": None,
            "normalized_plan": [],
            "validation_failures": ["validation missing"],
            "trace_artifact_ref": "local://planner-trace",
        },
    )
    monkeypatch.setattr(
        service_module,
        "verify_run",
        lambda **_kwargs: {
            "classification": "blocked",
            "summary": "validation missing",
            "checks": [{"name": "declared_validation", "passed": False}],
            "reasons": [{"message": "validation missing"}],
            "verifier_decision": {"decision": "needs_fix"},
        },
    )
    monkeypatch.setattr(writeback_service_module, "writeback_v2_run", _fake_writeback_v2_run)

    state = asyncio.run(
        svc.run_inline(
            tenant_id="default",
            project_id="bh-migrated-7-aix",
            task_number=2627,
            workspace_root=str(tmp_path),
            repo_key="hyeve-aix-service",
            explicit_profile_id=None,
            actor_id="tester",
            writeback=True,
        )
    )

    saved = store.get_by_run_id(state.run_id)
    assert state.failure is not None
    assert state.failure.classification == "POLICY_FAIL"
    assert saved is not None
    assert saved.failure is not None
    artifact_names = [item.name for item in saved.artifacts]
    assert "recon" in artifact_names
    assert "planner_raw" in artifact_names
    assert "verifier_result" in artifact_names
    assert "run_summary" in artifact_names
    assert "execution_summary" not in artifact_names


def test_v2_service_inline_blocked_path_writeback_uses_canonical_run_result_shape(tmp_path, monkeypatch):
    from backend.aix_orchestrator_v2 import service as service_module
    from backend.legacy.aix_orchestrator_v2 import writeback_service as writeback_service_module
    from backend.services.aix_enveloped_run_service import RunResult

    store = AixOrchestratorV2StateStore(root=tmp_path)
    svc = AixOrchestratorV2Service(workhub_client=_FakeWorkHubClient(task_payload=_task_payload()), run_store=store)
    monkeypatch.setattr(service_module, "build_recon", _fake_recon)
    monkeypatch.setattr(
        service_module,
        "plan_task",
        lambda **_kwargs: {
            "status": "blocked",
            "steps": [],
            "planner_packet": {"acceptance_criteria": ["tests pass"]},
            "planner_trace": {"local_trace_ref": "local://planner-trace"},
            "declared_validation": "pytest tests/test_example.py",
            "file_targets": ["backend/services/example.py"],
            "symbol_targets": ["example.symbol"],
            "assumptions": [],
            "blocked_reason": "validation missing",
            "input_summary": {"planner_prompt": "prompt"},
            "raw_output_text": None,
            "normalized_plan": [],
            "validation_failures": ["validation missing"],
            "trace_artifact_ref": "local://planner-trace",
        },
    )
    monkeypatch.setattr(
        service_module,
        "verify_run",
        lambda **_kwargs: {
            "classification": "blocked",
            "summary": "validation missing",
            "checks": [{"name": "declared_validation", "passed": False}],
            "reasons": [{"message": "validation missing"}],
            "verifier_decision": {"decision": "needs_fix"},
        },
    )

    async def _validate_blocked_writeback(**kwargs):
        run_result = kwargs["execution"]["run_result"]
        validated = RunResult.model_validate(run_result)
        assert validated.error is not None
        assert "classification" not in run_result["error"]
        assert validated.error.details["classification"] == "POLICY_FAIL"
        return {
            "writeback": {"status": "skipped", "reason": "execution_not_attempted"},
            "verification_evaluation": None,
            "transition_ok": True,
            "transition_error": None,
            "evidence_ok": True,
            "evidence_error": None,
        }

    monkeypatch.setattr(writeback_service_module, "writeback_v2_run", _validate_blocked_writeback)

    state = asyncio.run(
        svc.run_inline(
            tenant_id="default",
            project_id="bh-migrated-7-aix",
            task_number=2627,
            workspace_root=str(tmp_path),
            repo_key="hyeve-aix-service",
            explicit_profile_id=None,
            actor_id="tester",
            writeback=True,
        )
    )

    saved = store.get_by_run_id(state.run_id)
    assert state.status == "blocked"
    assert state.failure is not None
    assert state.failure.classification == "POLICY_FAIL"
    assert state.writeback is not None
    assert state.writeback.transition_ok is True
    assert state.writeback.evidence_ok is True
    assert saved is not None
    artifact_names = [item.name for item in saved.artifacts]
    assert "writeback" in artifact_names
    assert "run_summary" in artifact_names


def test_v2_service_writeback_transition_failure_becomes_writeback_failed(tmp_path, monkeypatch):
    from backend.aix_orchestrator_v2 import service as service_module
    from backend.legacy.aix_orchestrator_v2 import writeback_service as writeback_service_module

    store = AixOrchestratorV2StateStore(root=tmp_path)
    svc = AixOrchestratorV2Service(workhub_client=_FakeWorkHubClient(task_payload=_task_payload()), run_store=store)
    monkeypatch.setattr(service_module, "build_recon", _fake_recon)
    monkeypatch.setattr(
        service_module,
        "plan_task",
        lambda **_kwargs: {
            "status": "planned",
            "steps": [{"tool": "get_status", "args": {}}],
            "planner_packet": {"acceptance_criteria": ["tests pass"]},
            "planner_trace": {"local_trace_ref": "local://planner-trace"},
            "declared_validation": "pytest tests/test_example.py",
            "file_targets": ["backend/services/example.py"],
            "symbol_targets": ["example.symbol"],
            "assumptions": [],
            "blocked_reason": None,
            "input_summary": {"planner_prompt": "prompt"},
            "raw_output_text": '{"steps":[]}',
            "normalized_plan": [{"tool": "get_status", "args": {}}],
            "validation_failures": [],
            "trace_artifact_ref": "local://planner-trace",
        },
    )
    monkeypatch.setattr(
        service_module,
        "execute_plan",
        lambda **_kwargs: {
            "status": "completed",
            "run_result": {"status": "completed"},
            "changed_files": ["backend/services/example.py"],
            "commands": [{"ok": True, "command_text": "pytest tests/test_example.py"}],
            "tests": {"detected": True},
            "diff_refs": ["local://diff"],
            "failure": None,
            "trace": {"evidence_refs": ["local://diff"]},
            "envelope": {"envelope": {"specversion": "1.0"}},
        },
    )
    monkeypatch.setattr(
        service_module,
        "verify_run",
        lambda **_kwargs: {
            "classification": "success",
            "summary": "ok",
            "checks": [{"name": "declared_validation", "passed": True}],
            "reasons": [],
            "verifier_decision": {"decision": "verified"},
        },
    )
    monkeypatch.setattr(writeback_service_module, "writeback_v2_run", _fake_writeback_transition_failure)

    state = asyncio.run(
        svc.run_inline(
            tenant_id="default",
            project_id="bh-migrated-7-aix",
            task_number=2627,
            workspace_root=str(tmp_path),
            repo_key="hyeve-aix-service",
            explicit_profile_id=None,
            actor_id="tester",
            writeback=True,
        )
    )

    assert state.status == "failed"
    assert state.failure is not None
    assert state.failure.classification == "CODE_FAIL"
    assert state.failure.message == "transition failed"
    assert state.writeback is not None
    assert state.writeback.transition_ok is False


def test_v2_service_writeback_evidence_failure_becomes_writeback_failed(tmp_path, monkeypatch):
    from backend.aix_orchestrator_v2 import service as service_module
    from backend.legacy.aix_orchestrator_v2 import writeback_service as writeback_service_module

    store = AixOrchestratorV2StateStore(root=tmp_path)
    svc = AixOrchestratorV2Service(workhub_client=_FakeWorkHubClient(task_payload=_task_payload()), run_store=store)
    monkeypatch.setattr(service_module, "build_recon", _fake_recon)
    monkeypatch.setattr(
        service_module,
        "plan_task",
        lambda **_kwargs: {
            "status": "planned",
            "steps": [{"tool": "get_status", "args": {}}],
            "planner_packet": {"acceptance_criteria": ["tests pass"]},
            "planner_trace": {"local_trace_ref": "local://planner-trace"},
            "declared_validation": "pytest tests/test_example.py",
            "file_targets": ["backend/services/example.py"],
            "symbol_targets": ["example.symbol"],
            "assumptions": [],
            "blocked_reason": None,
            "input_summary": {"planner_prompt": "prompt"},
            "raw_output_text": '{"steps":[]}',
            "normalized_plan": [{"tool": "get_status", "args": {}}],
            "validation_failures": [],
            "trace_artifact_ref": "local://planner-trace",
        },
    )
    monkeypatch.setattr(
        service_module,
        "execute_plan",
        lambda **_kwargs: {
            "status": "completed",
            "run_result": {"status": "completed"},
            "changed_files": ["backend/services/example.py"],
            "commands": [{"ok": True, "command_text": "pytest tests/test_example.py"}],
            "tests": {"detected": True},
            "diff_refs": ["local://diff"],
            "failure": None,
            "trace": {"evidence_refs": ["local://diff"]},
            "envelope": {"envelope": {"specversion": "1.0"}},
        },
    )
    monkeypatch.setattr(
        service_module,
        "verify_run",
        lambda **_kwargs: {
            "classification": "success",
            "summary": "ok",
            "checks": [{"name": "declared_validation", "passed": True}],
            "reasons": [],
            "verifier_decision": {"decision": "verified"},
        },
    )
    monkeypatch.setattr(writeback_service_module, "writeback_v2_run", _fake_writeback_evidence_failure)

    state = asyncio.run(
        svc.run_inline(
            tenant_id="default",
            project_id="bh-migrated-7-aix",
            task_number=2627,
            workspace_root=str(tmp_path),
            repo_key="hyeve-aix-service",
            explicit_profile_id=None,
            actor_id="tester",
            writeback=True,
        )
    )

    assert state.status == "failed"
    assert state.failure is not None
    assert state.failure.classification == "CODE_FAIL"
    assert state.failure.message == "evidence failed"
    assert state.writeback is not None
    assert state.writeback.evidence_ok is False


class _FakeWorkHubClientWithBriefing(_FakeWorkHubClient):
    """Variant that returns a real markdown briefing, to prove load_task actually
    stores and surfaces it -- the base _FakeWorkHubClient always returns None, which
    only proves the no-briefing-available path doesn't crash, not that a real one is
    wired through correctly."""

    def __init__(self, *, task_payload: dict, briefing_markdown: str):
        super().__init__(task_payload=task_payload)
        self._briefing_markdown = briefing_markdown

    async def get_task_ai_context_markdown(
        self, *, project_id: str, task_number: int, tenant_id: str | None = None
    ) -> str | None:
        return self._briefing_markdown


def test_load_task_stores_and_records_workhub_ai_context_briefing(tmp_path):
    store = AixOrchestratorV2StateStore(root=tmp_path)
    briefing = "# Task Briefing: WH-2627 — Sprint 2 seam\n\n## Objective\nexercise v2 seam\n"
    svc = AixOrchestratorV2Service(
        workhub_client=_FakeWorkHubClientWithBriefing(task_payload=_task_payload(), briefing_markdown=briefing),
        run_store=store,
    )

    state = asyncio.run(
        svc.load_task(project_id="proj-1", task_number=2627, workspace_root=str(tmp_path))
    )

    assert state.packet["ai_context_briefing"] == briefing
    briefing_artifacts = [a for a in state.artifacts if a.name == "workhub_ai_context_briefing"]
    assert len(briefing_artifacts) == 1
    assert briefing_artifacts[0].payload["markdown"] == briefing
    assert briefing_artifacts[0].payload["format"] == "markdown"


def test_load_task_handles_missing_briefing_without_failing(tmp_path):
    store = AixOrchestratorV2StateStore(root=tmp_path)
    svc = AixOrchestratorV2Service(
        workhub_client=_FakeWorkHubClient(task_payload=_task_payload()),
        run_store=store,
    )

    state = asyncio.run(
        svc.load_task(project_id="proj-1", task_number=2627, workspace_root=str(tmp_path))
    )

    assert state.status == "loaded"
    assert state.packet["ai_context_briefing"] is None
    assert not any(a.name == "workhub_ai_context_briefing" for a in state.artifacts)
