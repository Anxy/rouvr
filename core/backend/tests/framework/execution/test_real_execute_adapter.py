from __future__ import annotations

from typing import Any

import pytest

from backend.aix_core.framework.execution.port import ExecuteStepRequest, ExecutionPort
from backend.aix_core.framework.execution.real_execute_adapter import RealExecuteAdapter
from backend.aix_core.runtime.executor.real_execute import RealExecuteResult


def _request(**overrides: Any) -> ExecuteStepRequest:
    values: dict[str, Any] = {
        "tenant_id": "tenant-1",
        "cycle_id": "cycle-1",
        "objective_id": "objective-1",
        "objective": "Make the requested change",
        "workhub_task_id": "task-1",
        "project_id": None,
        "task_number": None,
        "repository_id": "repo-1",
    }
    values.update(overrides)
    return ExecuteStepRequest(**values)


def test_adapter_satisfies_execution_port() -> None:
    port: ExecutionPort = RealExecuteAdapter()
    assert port is not None


def test_request_preserves_none_empty_and_callback_identity() -> None:
    callback = lambda: False
    settings = object()
    context: dict[str, Any] = {"files_read": []}
    request = _request(
        attachments=(), settings=settings, allowed_files=(), admission_allowed_files=None,
        execution_context=context, is_cancelled=callback,
    )
    kwargs = request.to_legacy_kwargs()
    assert kwargs["attachments"] == []
    assert kwargs["allowed_files"] == ()
    assert kwargs["admission_allowed_files"] is None
    assert kwargs["settings"] is settings
    assert kwargs["execution_context"] is context
    assert kwargs["is_cancelled"] is callback


@pytest.mark.asyncio
async def test_adapter_calls_legacy_once_and_returns_same_object(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []
    expected = RealExecuteResult(
        ok=True, code="REAL_EXECUTE_OK", workspace="ws", evidence_refs=[], workhub_loaded=False,
    )

    async def fake(**kwargs: Any) -> RealExecuteResult:
        calls.append(kwargs)
        return expected

    monkeypatch.setattr(
        "backend.aix_core.framework.execution.real_execute_adapter.run_real_execute", fake
    )
    request = _request(attachments=({"id": "attachment-1"},))
    actual = await RealExecuteAdapter().execute_step(request)
    assert actual is expected
    assert len(calls) == 1
    assert calls[0] == request.to_legacy_kwargs()


@pytest.mark.asyncio
async def test_adapter_preserves_exception_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = RuntimeError("same exception")

    async def fail(**kwargs: Any) -> RealExecuteResult:
        raise expected

    monkeypatch.setattr(
        "backend.aix_core.framework.execution.real_execute_adapter.run_real_execute", fail
    )
    with pytest.raises(RuntimeError) as caught:
        await RealExecuteAdapter().execute_step(_request())
    assert caught.value is expected
