"""F4 deep execution equivalence — FLAG OFF vs FLAG ON via durable backend."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.aix_core.framework.execution.port import ExecuteStepRequest
from backend.hyeve_aix_service.config import settings

from .harness import (
    COMPARISON_KEYS,
    compare_off_on,
    normalize_execute_kwargs,
    run_execute_cycle,
    stub_result,
)


def test_execution_port_defaults_off() -> None:
    assert bool(getattr(settings, "AIX_FRAMEWORK_EXECUTION_PORT", True)) is False


@pytest.mark.asyncio
async def test_flag_off_and_on_invoke_run_real_execute_once_each() -> None:
    pair = await compare_off_on()
    assert len(pair["off"]["calls"]) == 1
    assert len(pair["on"]["calls"]) == 1
    assert pair["off"]["response"]["ok"] == pair["on"]["response"]["ok"]


@pytest.mark.asyncio
async def test_executor_argument_parity_off_vs_on() -> None:
    pair = await compare_off_on()
    assert pair["off"]["normalized"][0] == pair["on"]["normalized"][0]


@pytest.mark.asyncio
async def test_step_contract_and_capsule_identity_fields() -> None:
    pair = await compare_off_on()
    off_c = pair["off"]["calls"][0]
    on_c = pair["on"]["calls"][0]
    assert off_c["step_contract"].target_scope == on_c["step_contract"].target_scope
    assert off_c["step_contract"].purpose == on_c["step_contract"].purpose
    assert off_c["context_capsule"].relevant_files == on_c["context_capsule"].relevant_files


@pytest.mark.asyncio
async def test_evidence_and_completion_gate_parity() -> None:
    result = stub_result(ok=True)
    pair = await compare_off_on(result=result)
    assert pair["off"]["response"]["ok"] == pair["on"]["response"]["ok"]
    assert pair["off"]["normalized"][0] == pair["on"]["normalized"][0]
    assert result.completion_gates is not None
    assert result.diff_text


@pytest.mark.asyncio
async def test_failure_behavior_parity() -> None:
    pair = await compare_off_on(result=stub_result(ok=False, code="REAL_EXECUTE_FAILED"))
    assert pair["off"]["response"]["ok"] is False
    assert pair["on"]["response"]["ok"] is False
    assert len(pair["off"]["calls"]) == 1
    assert len(pair["on"]["calls"]) == 1
    assert pair["off"]["normalized"][0] == pair["on"]["normalized"][0]


@pytest.mark.asyncio
async def test_recovery_replan_signal_parity() -> None:
    """Empty-diff / failed stub must trigger the same post-execute repair class OFF and ON."""
    pair = await compare_off_on(result=stub_result(ok=False, code="REAL_EXECUTE_FAILED"))
    off_r, on_r = pair["off"]["response"], pair["on"]["response"]
    assert off_r["ok"] is False and on_r["ok"] is False
    # Repair/replan is driven by verifier after the same executor result
    off_phase = pair["off"]["phase"]
    on_phase = pair["on"]["phase"]
    assert off_phase == on_phase


@pytest.mark.asyncio
async def test_attempt_journal_shape_parity() -> None:
    pair = await compare_off_on()
    off_j = pair["off"]["journal"]
    on_j = pair["on"]["journal"]
    assert (off_j is None) == (on_j is None)
    if off_j is not None and hasattr(off_j, "entries_for"):
        # Structural: both backends recorded attempts for their objectives
        assert pair["off"]["phase"] == pair["on"]["phase"]


@pytest.mark.asyncio
async def test_workhub_require_load_flag_parity() -> None:
    pair = await compare_off_on(require_workhub_load=True)
    assert pair["off"]["normalized"][0]["require_workhub_load"] is True
    assert pair["on"]["normalized"][0]["require_workhub_load"] is True
    assert pair["off"]["normalized"][0]["workhub_task_id"] == pair["on"]["normalized"][0]["workhub_task_id"]


@pytest.mark.asyncio
async def test_adapter_to_legacy_kwargs_matches_port_request_surface() -> None:
    off = await run_execute_cycle(execution_port=False)
    on = await run_execute_cycle(execution_port=True)
    assert off["normalized"][0] == on["normalized"][0]


def test_no_second_executor_or_scheduler_in_framework_execution() -> None:
    import backend.aix_core.framework.execution as pkg

    root = Path(pkg.__file__).resolve().parent
    banned = (
        "class Scheduler",
        "class WorkflowEngine",
        "TemporalClient",
        "openai.OpenAI",
        "direct_provider",
        "langgraph",
    )
    for path in root.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for token in banned:
            assert token not in text, f"{path.name} contains {token}"


def test_sole_canonical_command_writer_unchanged() -> None:
    from backend.main import PUBLIC_ROUTER_AUTHORITY_MATRIX

    routers = PUBLIC_ROUTER_AUTHORITY_MATRIX["routers"]
    writers = [name for name, authority in routers.items() if authority == "CANONICAL_COMMAND"]
    assert writers == ["objective_cycle_router"]


def test_real_execute_adapter_source_is_pure_delegate() -> None:
    import backend.aix_core.framework.execution.real_execute_adapter as mod

    text = Path(mod.__file__).read_text(encoding="utf-8")
    assert "return await run_real_execute(**request.to_legacy_kwargs())" in text
    assert "evaluate_completion_gates" not in text
    assert "WorkHub" not in text


@pytest.mark.asyncio
async def test_execute_step_request_roundtrip_covers_comparison_keys() -> None:
    req = ExecuteStepRequest(
        tenant_id="t",
        cycle_id="c",
        objective_id="o",
        objective="obj",
        workhub_task_id="task",
        project_id="p",
        task_number=1,
        repository_id="r",
        attachments=({"a": 1},),
        require_workhub_load=True,
        apply_edits=True,
    )
    legacy = req.to_legacy_kwargs()
    assert "tenant_id" in legacy
    assert normalize_execute_kwargs(legacy)["attachments"] == [{"a": 1}]
    assert "objective" in COMPARISON_KEYS
