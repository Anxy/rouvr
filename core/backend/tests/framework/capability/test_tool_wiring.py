"""F2-P3 — ToolCapabilityAdapter wiring tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.aix_core.framework.capability.models import (
    CapabilityExecutionRequest,
    ProviderKind,
)
from backend.aix_core.framework.capability.provider import CapabilityProviderResolver
from backend.aix_core.framework.capability.tool_wiring import (
    ToolCapabilityAdapter,
    bind_local_tool_provider,
)
from backend.aix_core.runtime.objective.contracts import ActionObservationV1, ActionRequestV1
from backend.hyeve_aix_service.config import settings


def test_flag_still_off() -> None:
    assert bool(getattr(settings, "AIX_FRAMEWORK_CAPABILITY_FACADE", True)) is False


@pytest.mark.asyncio
async def test_tool_adapter_delegates_to_execute_action() -> None:
    calls: list[ActionRequestV1] = []

    def _fake_execute(request: ActionRequestV1, **kwargs: object) -> ActionObservationV1:
        calls.append(request)
        return ActionObservationV1(
            action_id=request.action_id,
            status="SUCCESS",
            changed=False,
            observation={"path": request.arguments.get("path"), "read": "hello"},
            evidence=("ev://1",),
        )

    adapter = ToolCapabilityAdapter(
        repository_id="repo-1",
        allowed_files=("a.py",),
        apply_edits=False,
        execute_fn=_fake_execute,
    )
    out = await adapter.execute(
        CapabilityExecutionRequest(
            capability_id="filesystem",
            operation="read",
            caller_role="executor",
            arguments={"path": "a.py"},
            request_id="req-1",
        )
    )
    assert out.success is True
    assert out.output == {"path": "a.py", "read": "hello"}
    assert out.evidence_paths == ("ev://1",)
    assert len(calls) == 1
    assert calls[0].capability == "filesystem"
    assert calls[0].operation == "read"


@pytest.mark.asyncio
async def test_tool_adapter_maps_failure() -> None:
    def _fake_execute(request: ActionRequestV1, **kwargs: object) -> ActionObservationV1:
        return ActionObservationV1(
            action_id=request.action_id,
            status="FAILED",
            changed=False,
            observation={},
            error_code="POLICY_DENIED",
            error_message="denied",
        )

    adapter = ToolCapabilityAdapter(repository_id="repo-1", execute_fn=_fake_execute)
    out = await adapter.execute(
        CapabilityExecutionRequest(
            capability_id="filesystem",
            operation="patch",
            caller_role="executor",
            arguments={"path": "x.py"},
        )
    )
    assert out.success is False
    assert out.error_code == "POLICY_DENIED"


@pytest.mark.asyncio
async def test_bind_and_route_and_execute(tmp_path: Path) -> None:
    resolver = CapabilityProviderResolver()

    def _fake_execute(request: ActionRequestV1, **kwargs: object) -> ActionObservationV1:
        return ActionObservationV1(
            action_id=request.action_id,
            status="SUCCESS",
            changed=False,
            observation={"hits": []},
        )

    bind_local_tool_provider(
        resolver,
        repository_id="repo-1",
        allowed_files=("src/a.py",),
        apply_edits=False,
        execute_fn=_fake_execute,
    )
    assert resolver.get_provider(ProviderKind.LOCAL_TOOL) is not None

    result = await resolver.route_and_execute(
        CapabilityExecutionRequest(
            capability_id="filesystem",
            operation="search",
            caller_role="executor",
            arguments={"query": "foo", "path": "src"},
            request_id="s1",
        ),
        kind=ProviderKind.LOCAL_TOOL,
    )
    assert result.success is True
    assert result.output == {"hits": []}


def test_no_oc_imports_tool_wiring() -> None:
    """Tool wiring is reached via capability_executor flag branch, not direct OC imports."""
    root = Path(__file__).resolve().parents[3] / "aix_core" / "runtime" / "objective"
    offenders: list[str] = []
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "ToolCapabilityAdapter" in text or "framework.capability.tool_wiring" in text:
            offenders.append(str(path))
    assert offenders == []
