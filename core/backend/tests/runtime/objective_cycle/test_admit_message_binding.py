"""AIX-REAL-EXECUTION-002: conversation -> objective message binding and universal
task_family classification at the `admit_objective` command layer."""

from __future__ import annotations

import pytest

from backend.aix_core.runtime.objective.durable_api_backend import DurableObjectiveCycleApiBackend


def _msg(message_id: str, role: str, content: str, parent: str | None = None) -> dict:
    return {"message_id": message_id, "role": role, "content": content, "parent_message_id": parent}


async def _admit(backend: DurableObjectiveCycleApiBackend, body: dict, *, key: str) -> dict:
    return await backend.command(
        operation="admit_objective",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=None,
        body=body,
        idempotency_key=key,
        expected_version=None,
    )


@pytest.mark.asyncio
async def test_admit_merges_short_refinement_onto_prior_create_objective() -> None:
    backend = DurableObjectiveCycleApiBackend(mode="inprocess")
    result = await _admit(
        backend,
        {
            "workhub_task_id": "wh-task-1",
            "objective": "client may send the short refinement only",
            "task_class": "bounded_repair",
            "definition_of_done": ("done",),
            "repository_id": "aix",
            "allowed_files": (),
            "conversation_id": "conv_refine",
            "messages": [
                _msg(
                    "m1",
                    "user",
                    "Create a simple attendance registration system with a PHP backend "
                    "and HTML frontend, allowing only the teacher to add attendance registrations.",
                ),
                _msg(
                    "m2",
                    "user",
                    "thats too long it can be short as only teacher will add there registeration",
                    parent="m1",
                ),
            ],
        },
        key="admit-bind-refine-1",
    )
    assert result["ok"] is True
    assert "attendance registration system" in result["objective"]
    assert "PHP backend" in result["objective"]
    assert "Additional constraints from user:" in result["objective"]
    assert result["objective_source_message_id"] == "m1"

    backend = DurableObjectiveCycleApiBackend(mode="inprocess")
    result = await _admit(
        backend,
        {
            "workhub_task_id": "wh-task-1",
            "objective": "placeholder client text is ignored once messages[] is supplied",
            "task_class": "bounded_repair",
            "definition_of_done": ("done",),
            "repository_id": "aix",
            "allowed_files": (),
            "conversation_id": "conv_1",
            "messages": [
                _msg("m1", "user", "Hey"),
                _msg(
                    "m2",
                    "user",
                    r"Check D:\v2.1\hyeve\services\hyeve-integration-service\.env",
                    parent="m1",
                ),
            ],
        },
        key="admit-bind-1",
    )
    assert result["ok"] is True
    assert result["objective"] == r"Check D:\v2.1\hyeve\services\hyeve-integration-service\.env"
    assert result["objective_source_message_id"] == "m2"
    assert result["message_id"] == "m2"
    assert result["parent_message_id"] == "m1"
    assert result["conversation_id"] == "conv_1"


@pytest.mark.asyncio
async def test_all_greetings_fail_closed_without_creating_an_objective() -> None:
    backend = DurableObjectiveCycleApiBackend(mode="inprocess")
    result = await _admit(
        backend,
        {
            "workhub_task_id": "wh-task-1",
            "objective": "unused",
            "task_class": "bounded_repair",
            "definition_of_done": ("done",),
            "repository_id": "aix",
            "allowed_files": (),
            "messages": [_msg("m1", "user", "Hey"), _msg("m2", "user", "hello!")],
        },
        key="admit-bind-2",
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "INTENT_UNCLEAR"
    assert backend._objectives == {}  # noqa: SLF001 - no objective created on fail-closed binding


@pytest.mark.asyncio
async def test_ambiguous_edit_with_no_resource_requires_clarification(monkeypatch: pytest.MonkeyPatch) -> None:
    """Gap-closure Priority 2: "fix the client" no longer hard-rejects admission (that
    made "persist the answer and continue the same objective" impossible) -- the
    objective is admitted normally and the *existing*, cycle-scoped exploration-gate
    clarification gate (`exploration_planner._clarification_question`) blocks it with
    a real question once a cycle actually tries to run, exactly like any other
    exploration-required block. See `test_clarify_cycle_answers_and_reclassifies_the_
    same_objective` below for the full persist -> reclassify -> continue loop."""
    import json as _json
    import subprocess as _subprocess
    import tempfile
    from pathlib import Path

    repo_root = Path(tempfile.mkdtemp(prefix="aix-clarify-repo-"))
    (repo_root / "src").mkdir()
    (repo_root / "src" / "client.py").write_text("def send(): pass\n", encoding="utf-8", newline="\n")
    _subprocess.run(["git", "init", "-q"], cwd=repo_root, check=True)
    _subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=repo_root, check=True)
    _subprocess.run(["git", "config", "user.name", "t"], cwd=repo_root, check=True)
    _subprocess.run(["git", "add", "-A"], cwd=repo_root, check=True)
    _subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo_root, check=True)
    monkeypatch.setenv(
        "HYEVE_AIX_SERVICE_OBJECTIVE_CYCLE_REPOSITORY_ROOTS",
        _json.dumps({"client-svc": str(repo_root).replace("\\", "/")}),
    )

    backend = DurableObjectiveCycleApiBackend(mode="inprocess", real_execute_enabled=True, require_workhub_load=False)
    admit = await _admit(
        backend,
        {
            "workhub_task_id": "wh-task-1",
            "objective": "unused",
            "task_class": "bounded_repair",
            "definition_of_done": ("done",),
            "repository_id": "client-svc",
            "allowed_files": (),
            "messages": [_msg("m1", "user", "fix the client")],
        },
        key="admit-bind-3",
    )
    assert admit["ok"] is True
    assert admit["task_family"] == "EDIT"
    assert admit["objective_id"] in backend._objectives  # noqa: SLF001 - objective IS persisted now

    started = await backend.command(
        operation="start_cycle", tenant_id="tenant-1", actor_id="human-1", resource_id=admit["objective_id"],
        body={"candidate_sha": "abc1234", "budgets": {"max_steps": 3}},
        idempotency_key="start-bind-3", expected_version=None,
    )
    approved = await backend.command(
        operation="validate_approval_reference", tenant_id="tenant-1", actor_id="human-1", resource_id=started["cycle_id"],
        body={"approval_reference": "workhub://approvals/bind-3"},
        idempotency_key="approve-bind-3", expected_version=started["version"],
    )
    assert approved["ok"] is False
    assert approved["error"]["code"] == "INTENT_UNCLEAR"
    assert approved["phase"] == "BLOCKED"


@pytest.mark.asyncio
async def test_clarify_cycle_answers_and_reclassifies_the_same_objective(monkeypatch: pytest.MonkeyPatch) -> None:
    """Full persist-answer -> reclassify -> continue loop for a clarification-blocked
    cycle, driven through the real `clarify_cycle` command -- not a parallel
    clarification implementation."""
    import json as _json
    import subprocess as _subprocess
    import tempfile
    from pathlib import Path

    tmp_path = Path(tempfile.mkdtemp(prefix="aix-clarify-test-"))
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / "client.py").write_text("def send(): pass\n", encoding="utf-8", newline="\n")
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8", newline="\n")
    _subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    _subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=tmp_path, check=True)
    _subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    _subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    _subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True)

    monkeypatch.setenv(
        "HYEVE_AIX_SERVICE_OBJECTIVE_CYCLE_REPOSITORY_ROOTS",
        _json.dumps({"clarify-svc": str(tmp_path).replace("\\", "/")}),
    )
    backend = DurableObjectiveCycleApiBackend(mode="inprocess", real_execute_enabled=True, require_workhub_load=False)
    admit = await _admit(
        backend,
        {
            "workhub_task_id": "wh-task-1",
            "objective": "unused",
            "task_class": "bounded_repair",
            "definition_of_done": ("done",),
            "repository_id": "clarify-svc",
            "allowed_files": (),
            "messages": [_msg("m1", "user", "fix the client")],
        },
        key="admit-clarify-1",
    )
    assert admit["ok"] is True
    started = await backend.command(
        operation="start_cycle", tenant_id="tenant-1", actor_id="human-1", resource_id=admit["objective_id"],
        body={"candidate_sha": "abc1234", "budgets": {"max_steps": 3}},
        idempotency_key="start-clarify-1", expected_version=None,
    )
    approved = await backend.command(
        operation="validate_approval_reference", tenant_id="tenant-1", actor_id="human-1", resource_id=started["cycle_id"],
        body={"approval_reference": "workhub://approvals/clarify-1"},
        idempotency_key="approve-clarify-1", expected_version=started["version"],
    )
    assert approved["error"]["code"] == "INTENT_UNCLEAR"

    clarified = await backend.command(
        operation="clarify_cycle", tenant_id="tenant-1", actor_id="human-1", resource_id=started["cycle_id"],
        body={"clarification": {"answer": "backend/client.py"}},
        idempotency_key="clarify-1", expected_version=None,
    )
    # P1: the answer is a UserTurn. Intent / original_objective stay the admit freeze.
    record = backend._objectives[admit["objective_id"]]  # noqa: SLF001
    intent = backend._provenance.require_intent(admit["objective_id"])  # noqa: SLF001
    turns = backend._provenance.turns(admit["objective_id"])  # noqa: SLF001
    assert intent.text == "fix the client"
    assert record.objective == "fix the client"
    assert record.original_objective == "fix the client"
    assert any("backend/client.py" in turn.text for turn in turns)
    assert record.task_family == "EDIT"
    assert clarified is not None


@pytest.mark.asyncio
async def test_explicit_selected_message_id_overrides_latest() -> None:
    backend = DurableObjectiveCycleApiBackend(mode="inprocess")
    result = await _admit(
        backend,
        {
            "workhub_task_id": "wh-task-1",
            "objective": "unused",
            "task_class": "bounded_repair",
            "definition_of_done": ("done",),
            "repository_id": "aix",
            "allowed_files": (),
            "selected_message_id": "m1",
            "messages": [
                _msg("m1", "user", "Review the auth service"),
                _msg("m2", "user", "Actually review the billing service"),
            ],
        },
        key="admit-bind-4",
    )
    assert result["ok"] is True
    assert result["objective"] == "Review the auth service"
    assert result["objective_source_message_id"] == "m1"
    assert result["task_family"] == "REVIEW"


@pytest.mark.asyncio
async def test_legacy_admission_without_messages_is_unaffected() -> None:
    backend = DurableObjectiveCycleApiBackend(mode="inprocess")
    result = await _admit(
        backend,
        {
            "workhub_task_id": "wh-task-1",
            "objective": "Add SMTP_PASSWORD support to the mail configuration",
            "task_class": "bounded_repair",
            "definition_of_done": ("done",),
            "repository_id": "aix",
            "allowed_files": (),
        },
        key="admit-bind-5",
    )
    assert result["ok"] is True
    assert result["objective"] == "Add SMTP_PASSWORD support to the mail configuration"
    assert result["objective_source_message_id"] is None
    assert result["task_family"] == "EDIT"


@pytest.mark.asyncio
async def test_legacy_admission_may_supply_objective_source_message_id_directly() -> None:
    backend = DurableObjectiveCycleApiBackend(mode="inprocess")
    result = await _admit(
        backend,
        {
            "workhub_task_id": "wh-task-1",
            "objective": "Add SMTP_PASSWORD support to the mail configuration",
            "task_class": "bounded_repair",
            "definition_of_done": ("done",),
            "repository_id": "aix",
            "allowed_files": (),
            "objective_source_message_id": "external-msg-1",
        },
        key="admit-bind-6",
    )
    assert result["ok"] is True
    assert result["objective_source_message_id"] == "external-msg-1"
