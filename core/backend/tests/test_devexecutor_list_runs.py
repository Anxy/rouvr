"""AIFIRST-2: list_runs service-level tests.

Direct tests for the in-process run registry (list_runs, create_run,
get_run, cancel_run) introduced in AIFIRST-2 — not via HTTP.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.services.devexecutor_runtime_registry_service import (
    _IDEMPOTENCY,
    _RUNS,
    DevExecutorRunRecord,
    _enrich_runtime_payload,
    build_resume_context,
    cancel_run,
    create_run,
    execute_run,
    get_resumable_steps,
    get_run,
    list_runs,
)


def _clear() -> None:
    """Isolate each test from leftover run state."""
    _RUNS.clear()
    _IDEMPOTENCY.clear()


def _make_run(
    *,
    project_id: str = "proj-a",
    task_number: int = 1,
    idempotency_key: str | None = None,
    token_budget: int | None = None,
) -> DevExecutorRunRecord:
    return create_run(
        project_id=project_id,
        task_number=task_number,
        correlation_id="corr-test",
        idempotency_key=idempotency_key,
        workspace_root="/tmp/ws",
        repo_key="test-repo",
        profile_id=None,
        steps=[],
        token_budget=token_budget,
    )


# ---------------------------------------------------------------------------
# create_run
# ---------------------------------------------------------------------------


def test_create_run_returns_record_with_run_id() -> None:
    _clear()
    rec = _make_run()
    assert rec.run_id.startswith("devrun-")
    assert rec.status == "queued"
    assert rec.project_id == "proj-a"
    assert rec.task_number == 1


def test_create_run_idempotency_returns_same_record() -> None:
    _clear()
    rec1 = _make_run(idempotency_key="idem-1")
    rec2 = _make_run(idempotency_key="idem-1")
    assert rec1.run_id == rec2.run_id
    assert rec2.replayed is True


def test_create_run_different_keys_produce_different_runs() -> None:
    _clear()
    rec1 = _make_run(idempotency_key="key-a")
    rec2 = _make_run(idempotency_key="key-b")
    assert rec1.run_id != rec2.run_id


def test_enrich_runtime_payload_injects_runtime_capsule(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear()
    run = _make_run(project_id="bh-migrated-7-aix", task_number=2573)
    monkeypatch.setattr(
        "backend.services.devexecutor_runtime_registry_service.get_latest_runtime_memory_capsule",
        lambda **_kwargs: {
            "capsule_id": "capsule-2026-04-14-2573",
            "run_ledger_facts": {"slice_id": "tests"},
            "semantic_grounding": "Preserve causal helper intent.",
        },
    )
    payload = _enrich_runtime_payload(base={"mode": "docker"}, run=run, runtime_kind="command")
    assert payload is not None
    assert payload["persona_instruction_pack"][0].startswith("<capsule>\n")
    assert payload["persona_instruction_pack"][0].endswith("\n</capsule>")


def test_create_run_persists_token_budget() -> None:
    _clear()
    rec = _make_run(token_budget=321)
    assert rec.token_budget == 321


# ---------------------------------------------------------------------------
# get_run
# ---------------------------------------------------------------------------


def test_get_run_returns_record() -> None:
    _clear()
    rec = _make_run()
    fetched = get_run(run_id=rec.run_id)
    assert fetched is not None
    assert fetched.run_id == rec.run_id


def test_get_run_returns_stored_token_budget() -> None:
    _clear()
    rec = _make_run(token_budget=654)
    fetched = get_run(run_id=rec.run_id)
    assert fetched is not None
    assert fetched.token_budget == 654


def test_get_run_returns_none_for_unknown_id() -> None:
    _clear()
    assert get_run(run_id="devrun-doesnotexist") is None


# ---------------------------------------------------------------------------
# list_runs
# ---------------------------------------------------------------------------


def test_list_runs_returns_all_runs() -> None:
    _clear()
    _make_run(project_id="proj-x", task_number=10)
    _make_run(project_id="proj-x", task_number=11)
    _make_run(project_id="proj-y", task_number=10)
    results = list_runs()
    assert len(results) == 3


def test_list_runs_filters_by_project_id() -> None:
    _clear()
    _make_run(project_id="proj-x", task_number=10)
    _make_run(project_id="proj-y", task_number=10)
    results = list_runs(project_id="proj-x")
    assert len(results) == 1
    assert results[0].project_id == "proj-x"


def test_list_runs_filters_by_task_number() -> None:
    _clear()
    _make_run(project_id="proj-x", task_number=10)
    _make_run(project_id="proj-x", task_number=20)
    results = list_runs(project_id="proj-x", task_number=20)
    assert len(results) == 1
    assert results[0].task_number == 20


def test_list_runs_empty_when_no_runs() -> None:
    _clear()
    assert list_runs() == []


# ---------------------------------------------------------------------------
# cancel_run
# ---------------------------------------------------------------------------


def test_cancel_run_sets_status_cancelled() -> None:
    _clear()
    rec = _make_run()
    cancelled = cancel_run(run_id=rec.run_id, reason="operator_request")
    assert cancelled.status == "cancelled"
    assert cancelled.cancelled_at_utc is not None
    assert cancelled.diagnostics.get("cancel_reason") == "operator_request"


def test_cancel_run_is_idempotent_when_already_terminal() -> None:
    _clear()
    rec = _make_run()
    cancel_run(run_id=rec.run_id)
    cancel_run(run_id=rec.run_id)  # second cancel — should not raise
    assert rec.status == "cancelled"


def test_execute_run_records_runtime_contract_diagnostics(monkeypatch, tmp_path: Path) -> None:
    _clear()

    class _Profile:
        profile_id = "RP-TEST"

        class command_policy:  # noqa: N801
            allow_prefixes = [["python"]]

    monkeypatch.setattr(
        "backend.services.devexecutor_runtime_registry_service.resolve_effective_repo_profile",
        lambda **_kwargs: (_Profile(), {"source": "test"}),
    )
    monkeypatch.setattr(
        "backend.services.devexecutor_runtime_registry_service.execute_tool_loop",
        lambda **_kwargs: [{"tool": "get_status", "ok": True, "result": {}}],
    )

    workspace_root = tmp_path / "services" / "hyeve-codeintel-service" / "backend"
    workspace_root.mkdir(parents=True)
    rec = create_run(
        project_id="proj-a",
        task_number=7,
        correlation_id="corr-test",
        idempotency_key=None,
        workspace_root=str(workspace_root),
        repo_key="test-repo",
        profile_id=None,
        steps=[{"tool": "get_status", "args": {}}],
    )

    finished = execute_run(
        run_id=rec.run_id,
        profiles_dir=str(tmp_path),
        max_runtime_seconds=30.0,
        max_patch_attempts=3,
        command_runtime={
            "mode": "docker",
            "image": "hyeve-aix-devexecutor-runtime:latest",
            "host_workspace_root": str(tmp_path),
            "container_workspace_root": "/workspace/hyeve",
            "container_required": True,
        },
        mutation_runtime={
            "mode": "docker",
            "image": "runtime:latest",
            "host_workspace_root": str(tmp_path),
            "container_workspace_root": "/workspace/hyeve",
            "container_required": True,
        },
    )

    runtime_contract = finished.diagnostics.get("runtime_contract")
    assert runtime_contract is not None
    assert runtime_contract["command_runtime"]["workspace_mapping"]["container_workspace_root"] == (
        "/workspace/hyeve/services/hyeve-codeintel-service/backend"
    )
    assert runtime_contract["mutation_runtime"]["workspace_mapping"]["mount_source"] == str(tmp_path)


def test_execute_run_passes_token_budget_into_tool_loop(monkeypatch, tmp_path: Path) -> None:
    _clear()

    class _Profile:
        profile_id = "RP-TEST"

        class command_policy:  # noqa: N801
            allow_prefixes = [["python"]]

    captured: dict[str, object] = {}

    monkeypatch.setattr(
        "backend.services.devexecutor_runtime_registry_service.resolve_effective_repo_profile",
        lambda **_kwargs: (_Profile(), {"source": "test"}),
    )

    def fake_execute_tool_loop(**kwargs):
        captured["token_budget"] = kwargs.get("token_budget")
        return [
            {
                "tool": "read_file",
                "ok": True,
                "result": {"content": "alpha beta gamma delta"},
                "_tokens_estimate": 6,
                "_budget_used": 6,
            },
            {
                "tool": "read_file",
                "ok": False,
                "error": "TOKEN_BUDGET_EXCEEDED budget=4 used=6 step_failed=0",
                "result": {"code": "TOKEN_BUDGET_EXCEEDED", "budget": 4, "used": 6, "step_failed": 0},
                "_budget_used": 6,
            },
        ]

    monkeypatch.setattr(
        "backend.services.devexecutor_runtime_registry_service.execute_tool_loop",
        fake_execute_tool_loop,
    )

    rec = create_run(
        project_id="proj-a",
        task_number=7,
        correlation_id="corr-test",
        idempotency_key=None,
        workspace_root=str(tmp_path),
        repo_key="test-repo",
        profile_id=None,
        steps=[{"tool": "read_file", "args": {"relative_path": "demo.txt"}}],
        token_budget=4,
    )

    finished = execute_run(
        run_id=rec.run_id,
        profiles_dir=str(tmp_path),
        max_runtime_seconds=30.0,
        max_patch_attempts=3,
    )

    assert captured["token_budget"] == 4
    assert finished.status == "failed"
    assert finished.transcript[-1]["result"]["code"] == "TOKEN_BUDGET_EXCEEDED"


def test_create_run_accepts_resume_run_id_and_caps_depth() -> None:
    _clear()
    parent = _make_run()
    resumed = create_run(
        project_id="proj-a",
        task_number=2,
        correlation_id="corr-test",
        idempotency_key=None,
        workspace_root="/tmp/ws",
        repo_key="test-repo",
        profile_id=None,
        steps=[{"tool": "get_status", "args": {}}],
        resume_run_id=parent.run_id,
    )
    assert resumed.resume_run_id == parent.run_id
    assert resumed.resume_depth == 1

    level2 = create_run(
        project_id="proj-a",
        task_number=3,
        correlation_id="corr-test",
        idempotency_key=None,
        workspace_root="/tmp/ws",
        repo_key="test-repo",
        profile_id=None,
        steps=[{"tool": "get_status", "args": {}}],
        resume_run_id=resumed.run_id,
    )
    level3 = create_run(
        project_id="proj-a",
        task_number=4,
        correlation_id="corr-test",
        idempotency_key=None,
        workspace_root="/tmp/ws",
        repo_key="test-repo",
        profile_id=None,
        steps=[{"tool": "get_status", "args": {}}],
        resume_run_id=level2.run_id,
    )
    assert level3.resume_depth == 3

    with pytest.raises(RuntimeError, match="RESUME_CHAIN_LIMIT_EXCEEDED"):
        create_run(
            project_id="proj-a",
            task_number=5,
            correlation_id="corr-test",
            idempotency_key=None,
            workspace_root="/tmp/ws",
            repo_key="test-repo",
            profile_id=None,
            steps=[{"tool": "get_status", "args": {}}],
            resume_run_id=level3.run_id,
        )


def test_build_resume_context_includes_parent_lineage_and_remaining_steps() -> None:
    _clear()
    parent = create_run(
        project_id="proj-a",
        task_number=7,
        correlation_id="corr-parent",
        idempotency_key=None,
        workspace_root="/tmp/ws",
        repo_key="test-repo",
        profile_id=None,
        steps=[
            {"tool": "read_file", "args": {}},
            {"tool": "edit_file", "args": {}},
        ],
    )
    parent.transcript = [
        {"index": 0, "ok": True},
        {"index": 1, "ok": False},
    ]
    resumed = create_run(
        project_id="proj-a",
        task_number=7,
        correlation_id="corr-child",
        idempotency_key=None,
        workspace_root="/tmp/ws",
        repo_key="test-repo",
        profile_id=None,
        steps=[],
        resume_run_id=parent.run_id,
    )

    context = build_resume_context(run=resumed)

    assert context["resume_run_id"] == parent.run_id
    assert context["parent_found"] is True
    assert context["parent_task_number"] == 7
    assert context["parent_correlation_id"] == "corr-parent"
    assert context["resume_supported"] is True
    assert context["inherited_successful_step_count"] == 1
    assert context["remaining_resumable_step_count"] == 1


def test_get_resumable_steps_returns_remaining_unsuccessful_steps() -> None:
    _clear()
    run = create_run(
        project_id="proj-a",
        task_number=7,
        correlation_id="corr-test",
        idempotency_key=None,
        workspace_root="/tmp/ws",
        repo_key="test-repo",
        profile_id=None,
        steps=[
            {"tool": "read_file", "args": {"relative_path": "a.txt"}},
            {"tool": "write_file", "args": {"relative_path": "b.txt", "content": "x"}},
            {"tool": "get_status", "args": {}},
        ],
    )
    run.transcript = [
        {"index": 0, "tool": "read_file", "ok": True, "step_status": "ok"},
        {
            "index": 1,
            "tool": "write_file",
            "ok": False,
            "step_status": "failed",
            "result": {"code": "WRITE_FAILED"},
        },
    ]

    remaining = get_resumable_steps(run_id=run.run_id)
    assert remaining == [
        {"tool": "write_file", "args": {"relative_path": "b.txt", "content": "x"}},
        {"tool": "get_status", "args": {}},
    ]


def test_execute_run_prefixes_parent_successful_transcript_when_resumed(monkeypatch, tmp_path: Path) -> None:
    _clear()

    class _Profile:
        profile_id = "RP-TEST"

        class command_policy:  # noqa: N801
            allow_prefixes = [["python"]]

    monkeypatch.setattr(
        "backend.services.devexecutor_runtime_registry_service.resolve_effective_repo_profile",
        lambda **_kwargs: (_Profile(), {"source": "test"}),
    )

    parent = create_run(
        project_id="proj-a",
        task_number=7,
        correlation_id="corr-parent",
        idempotency_key=None,
        workspace_root=str(tmp_path),
        repo_key="test-repo",
        profile_id=None,
        steps=[{"tool": "read_file", "args": {"relative_path": "a.txt"}}],
    )
    parent.transcript = [
        {"index": 0, "tool": "read_file", "ok": True, "result": {"content": "alpha"}, "step_status": "ok"}
    ]
    parent.status = "failed"

    monkeypatch.setattr(
        "backend.services.devexecutor_runtime_registry_service.execute_tool_loop",
        lambda **_kwargs: [{"index": 0, "tool": "get_status", "ok": True, "result": {}}],
    )

    resumed = create_run(
        project_id="proj-a",
        task_number=8,
        correlation_id="corr-child",
        idempotency_key=None,
        workspace_root=str(tmp_path),
        repo_key="test-repo",
        profile_id=None,
        steps=[{"tool": "get_status", "args": {}}],
        resume_run_id=parent.run_id,
    )

    finished = execute_run(
        run_id=resumed.run_id,
        profiles_dir=str(tmp_path),
        max_runtime_seconds=30.0,
        max_patch_attempts=3,
    )

    assert finished.transcript[0]["tool"] == "read_file"
    assert finished.transcript[1]["tool"] == "get_status"
