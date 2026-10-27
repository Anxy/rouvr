"""EXEC-CREATE-CTX: greenfield CREATE with empty target directory + declared scopes."""

from __future__ import annotations

from typing import Any

import pytest

from backend.aix_core.runtime.executor.real_execute import (
    _directory_only_create_scopes,
    _step_contract_with_mkdir_actions,
)
from backend.aix_core.runtime.executor.source_patch_codegen import (
    _discovery_context_prompt,
    _edits_from_model_payload,
    _empty_create_target_paths,
    generate_source_patch,
)
from backend.aix_core.runtime.objective.contracts import (
    BudgetV1,
    RiskTier,
    StepContractV1,
)
from backend.aix_core.runtime.objective.discovery import DiscoveryReport
from backend.infrastructure.model_gateway.hyeve_integration_client import (
    ChatCompletionResultV1,
)


def test_directory_only_create_scopes_detects_empty_product_root() -> None:
    assert _directory_only_create_scopes(("proposed/erp-system/",)) == (
        "proposed/erp-system/",
    )
    assert _directory_only_create_scopes(("proposed/attendance",)) == (
        "proposed/attendance",
    )
    assert _directory_only_create_scopes(
        ("proposed/attendance/index.html", "proposed/attendance/styles.css")
    ) == ()
    assert _directory_only_create_scopes(
        ("proposed/erp-system/", "proposed/erp-system/index.html")
    ) == ()


def test_step_contract_with_mkdir_actions_attaches_filesystem_mkdir() -> None:
    budget = BudgetV1(
        time_seconds=60, tokens=1000, cost_usd=1.0, retries=1, steps=5, concurrency=1
    )
    step = StepContractV1(
        cycle_id="cyc_t",
        tenant_id="tenant-t",
        step_index=0,
        purpose="Create governed product directory under create_root",
        target_scope=("proposed/erp-system/",),
        expected_output_schema_ref="aix.step_contract.v1.generic_edit",
        acceptance_criteria=("proposed/erp-system/ exists",),
        validation_commands=("test -d proposed/erp-system/",),
        assigned_role="executor",
        step_budget=budget,
        risk_tier=RiskTier.MEDIUM,
        status="PENDING",
    )
    with_mkdir = _step_contract_with_mkdir_actions(step, ("proposed/erp-system/",))
    assert any(
        a.capability == "filesystem" and a.operation == "mkdir" for a in with_mkdir.actions
    )
    assert with_mkdir.actions[0].arguments["path"] == "proposed/erp-system/"


def test_empty_create_targets_listed_in_discovery_prompt() -> None:
    discovery = DiscoveryReport(
        config_files={
            "proposed/attendance/index.html": "",
            "proposed/attendance/styles.css": "",
        }
    )
    assert _empty_create_target_paths(discovery) == (
        "proposed/attendance/index.html",
        "proposed/attendance/styles.css",
    )
    prompt = _discovery_context_prompt(discovery)
    assert "GREENFIELD CREATE TARGETS" in prompt
    assert "proposed/attendance/index.html" in prompt
    assert "Empty directory is expected" in prompt
    assert "(discovery found no manifest/config/relevant files)" not in prompt


def test_edits_accept_new_content_for_empty_seeded_create_target() -> None:
    discovery = DiscoveryReport(
        config_files={"proposed/attendance/index.html": ""}
    )
    parsed = {
        "edits": [
            {
                "path": "proposed/attendance/index.html",
                "new_content": "<!doctype html><title>Attendance</title>\n",
                "reason": "greenfield create",
            }
        ],
        "no_files_identified": False,
        "reason": "",
    }
    edits, reason = _edits_from_model_payload(parsed, discovery=discovery)
    assert reason == ""
    assert len(edits) == 1
    assert edits[0].path == "proposed/attendance/index.html"
    assert "Attendance" in edits[0].new_content


def test_edits_reject_create_outside_declared_targets() -> None:
    discovery = DiscoveryReport(
        config_files={"proposed/attendance/index.html": ""}
    )
    parsed = {
        "edits": [
            {
                "path": "proposed/other/secret.js",
                "new_content": "alert(1)\n",
                "reason": "outside scope",
            }
        ],
        "no_files_identified": False,
        "reason": "",
    }
    edits, _reason = _edits_from_model_payload(parsed, discovery=discovery)
    assert edits == []


@pytest.mark.asyncio
async def test_generate_source_patch_empty_attendance_produces_create_edit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[dict[str, Any]] = []

    async def _fake_chat(**kwargs: Any) -> ChatCompletionResultV1:
        captured.append(kwargs)
        return ChatCompletionResultV1(
            output_text=(
                '{"edits":[{"path":"proposed/attendance/index.html",'
                '"new_content":"<!doctype html><h1>Attendance</h1>\\n",'
                '"replacements":[]}],"no_files_identified":false,"reason":""}'
            ),
            provider="auto",
            resolved_provider="openrouter",
            model="openrouter/openai/gpt-4o-mini",
            usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        )

    monkeypatch.setattr(
        "backend.aix_core.runtime.executor.source_patch_codegen.chat_completion",
        _fake_chat,
    )
    discovery = DiscoveryReport(
        config_files={"proposed/attendance/index.html": ""}
    )
    result = await generate_source_patch(
        objective=(
            "Create greenfield attendance under proposed/attendance/, "
            "including proposed/attendance/index.html"
        ),
        discovery=discovery,
        preferred_path="proposed/attendance/index.html",
        tenant_id="tenant-1",
        actor_id="actor-1",
        authorization_header="Bearer tok",
    )
    assert result.edits
    assert result.edits[0].path == "proposed/attendance/index.html"
    assert result.failure_code is None
    assert captured
    system = captured[0]["messages"][0].content
    user = captured[0]["messages"][1].content
    assert "authorized greenfield" in system.casefold() or "CREATE TARGET" in user
    assert "proposed/attendance/index.html" in user
    assert "GREENFIELD CREATE TARGETS" in user


@pytest.mark.asyncio
async def test_generate_source_patch_grounds_accepted_plan_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Accepted plan declares exact greenfield content → deterministic edit, no LLM.

    Live lane: small chat models intermittently return shape-noncompliant
    payloads on greenfield prompts. The plan action's declared content is the
    authorized deliverable and must ground the edit deterministically.
    """
    from backend.aix_core.runtime.planner.step_contract_planner import _parse_actions

    captured: list[dict[str, Any]] = []

    async def _forbidden_chat(**kwargs: Any) -> ChatCompletionResultV1:  # pragma: no cover
        captured.append(kwargs)
        raise AssertionError("model must not be called when plan content grounds the edit")

    monkeypatch.setattr(
        "backend.aix_core.runtime.executor.source_patch_codegen.chat_completion",
        _forbidden_chat,
    )
    monkeypatch.setattr(
        "backend.aix_core.runtime.executor.source_patch_codegen.governed_chat_completion",
        _forbidden_chat,
        raising=False,
    )
    discovery = DiscoveryReport(config_files={"proposed/notes.md": ""})
    actions = _parse_actions(
        [
            {
                "capability": "filesystem",
                "operation": "patch",
                "arguments": {
                    "path": "proposed/notes.md",
                    "content": "# Live Run 3\n\n* First bullet line\n* Second bullet line\n",
                },
            }
        ]
    )
    result = await generate_source_patch(
        objective="Create a file named notes.md in the cycle workspace",
        discovery=discovery,
        preferred_path="proposed/notes.md",
        tenant_id="tenant-1",
        actor_id="actor-1",
        authorization_header="Bearer t",
        plan_actions=actions,
    )
    assert captured == []
    assert result.failure_code is None
    assert len(result.edits) == 1
    assert result.edits[0].path == "proposed/notes.md"
    assert result.edits[0].new_content.startswith("# Live Run 3")
    assert result.strategy_id == "aix.plan_content.greenfield.v1"


@pytest.mark.asyncio
async def test_generate_source_patch_fails_closed_without_create_scope() -> None:
    result = await generate_source_patch(
        objective="Create something under proposed/ but no files named",
        discovery=DiscoveryReport(config_files={}),
        tenant_id="tenant-1",
        actor_id="actor-1",
        authorization_header="Bearer tok",
    )
    assert result.failure_code == "CREATE_CONTEXT_ABSENT"
    assert "CREATE_CONTEXT_ABSENT" in (result.reason or "")
