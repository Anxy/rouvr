"""Fail-closed WorkHub task identity: placeholders never reach WorkHub HTTP."""

from __future__ import annotations

from typing import Any

import pytest

from backend.aix_core.runtime.executor.real_execute import (
    is_workhub_task_id_placeholder,
    load_workhub_task,
)


class _RecordingClient:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def get_task_by_number(self, **kwargs: Any) -> dict[str, Any] | None:
        self.calls.append("by_number")
        return {"id": "wh-by-number", "task_number": kwargs.get("task_number")}

    async def get_aix_adapter_read_contract(self, **kwargs: Any) -> tuple[bool, str | None, dict[str, Any] | None]:
        self.calls.append(f"read-contract:{kwargs.get('task_id')}")
        return True, None, {"id": kwargs.get("task_id")}


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("auto", True),
        ("CREATE", True),
        ("new", True),
        ("", False),
        ("wh-real-1", False),
        ("01KYCPDEPJ9STR36FB8P8DT31F", False),
    ],
)
def test_is_workhub_task_id_placeholder(value: str, expected: bool) -> None:
    assert is_workhub_task_id_placeholder(value) is expected


@pytest.mark.asyncio
@pytest.mark.parametrize("marker", ["auto", "create", "new", "Auto"])
async def test_load_rejects_placeholder_before_http(marker: str) -> None:
    client = _RecordingClient()
    task, err = await load_workhub_task(
        client=client,  # type: ignore[arg-type]
        project_id="proj-1",
        workhub_task_id=marker,
        task_number=None,
        tenant_id="tenant-1",
    )
    assert task is None
    assert err == "WORKHUB_TASK_ID_UNRESOLVED"
    assert client.calls == []


@pytest.mark.asyncio
async def test_load_rejects_empty_task_id_before_http() -> None:
    client = _RecordingClient()
    task, err = await load_workhub_task(
        client=client,  # type: ignore[arg-type]
        project_id="proj-1",
        workhub_task_id="  ",
        task_number=None,
        tenant_id="tenant-1",
    )
    assert task is None
    assert err == "WORKHUB_TASK_ID_UNRESOLVED"
    assert client.calls == []


@pytest.mark.asyncio
async def test_load_allows_real_task_id() -> None:
    client = _RecordingClient()
    task, err = await load_workhub_task(
        client=client,  # type: ignore[arg-type]
        project_id="proj-1",
        workhub_task_id="wh-real-1",
        task_number=None,
        tenant_id="tenant-1",
    )
    assert err is None
    assert task == {"id": "wh-real-1"}
    assert client.calls == ["read-contract:wh-real-1"]


@pytest.mark.asyncio
async def test_load_prefers_task_number_even_if_id_is_placeholder() -> None:
    """task_number is a resolved identity; placeholder id must not block that path."""
    client = _RecordingClient()
    task, err = await load_workhub_task(
        client=client,  # type: ignore[arg-type]
        project_id="proj-1",
        workhub_task_id="auto",
        task_number=42,
        tenant_id="tenant-1",
    )
    assert err is None
    assert task == {"id": "wh-by-number", "task_number": 42}
    assert client.calls == ["by_number"]
