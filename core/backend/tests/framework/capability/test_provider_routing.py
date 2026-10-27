"""F2-P2 — Capability provider resolution: parity + fail-closed."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.aix_core.framework.capability import capability_facade_enabled
from backend.aix_core.framework.capability.models import (
    CapabilityDescriptor,
    CapabilityExecutionRequest,
    EffectKind,
    ProviderKind,
)
from backend.aix_core.framework.capability.provider import CapabilityProviderResolver
from backend.aix_core.framework.capability.registry_facade import CapabilityRegistryFacade
from backend.aix_core.runtime.objective.contracts import ActionRequestV1
from backend.infrastructure.model_gateway.provider_routing import GovernedProviderRouter
from backend.hyeve_aix_service.config import settings


def _read_req() -> ActionRequestV1:
    return ActionRequestV1(
        action_id="a1",
        capability="filesystem",
        operation="read",
        arguments={"path": "README.md"},
    )


def test_flag_still_defaults_off() -> None:
    assert bool(getattr(settings, "AIX_FRAMEWORK_CAPABILITY_FACADE", True)) is False
    assert capability_facade_enabled() is False


def test_local_tool_resolution_flow(tmp_path: Path) -> None:
    resolver = CapabilityProviderResolver()
    result = resolver.resolve(
        _read_req(),
        repository_id="repo-1",
        repository_root=tmp_path,
        allowed_files=("README.md",),
        apply_edits=False,
    )
    assert result.allowed is True
    assert result.capability_id == "filesystem"
    assert result.operation == "read"
    assert result.provider_kind == ProviderKind.LOCAL_TOOL
    assert result.descriptor is not None
    assert result.policy is not None and result.policy.allowed is True
    assert "PROVIDER_RESOLVED" in result.reason_codes


def test_missing_capability_fail_closed(tmp_path: Path) -> None:
    resolver = CapabilityProviderResolver()
    req = ActionRequestV1(
        action_id="a2",
        capability="nope.capability",
        operation="explode",
        arguments={},
    )
    result = resolver.resolve(req, repository_id="repo-1", repository_root=tmp_path)
    assert result.allowed is False
    assert "CAPABILITY_NOT_AVAILABLE" in result.reason_codes


def test_policy_denied_fail_closed(tmp_path: Path) -> None:
    resolver = CapabilityProviderResolver()
    req = ActionRequestV1(
        action_id="a3",
        capability="filesystem",
        operation="patch",
        arguments={"path": "secret.py"},
    )
    result = resolver.resolve(
        req,
        repository_id="repo-1",
        repository_root=tmp_path,
        allowed_files=("other.py",),
        apply_edits=True,
    )
    assert result.allowed is False
    assert result.policy is not None
    assert result.policy.allowed is False
    assert result.reason_codes[0] == result.policy.code


def test_require_registered_provider_fail_closed(tmp_path: Path) -> None:
    resolver = CapabilityProviderResolver()
    result = resolver.resolve(
        _read_req(),
        repository_id="repo-1",
        repository_root=tmp_path,
        allowed_files=("README.md",),
        apply_edits=False,
        require_registered_provider=True,
    )
    assert result.allowed is False
    assert "PROVIDER_NOT_REGISTERED" in result.reason_codes


def test_llm_route_parity_with_governed_provider_router() -> None:
    """Resolver must reuse GovernedProviderRouter — identical ALLOW identity."""
    gpr = GovernedProviderRouter()
    direct = gpr.resolve(role="executor", model_ref="claude-3-7-sonnet")
    assert direct.disposition == "ALLOW"

    # Inject a synthetic LLM descriptor without mutating foundation catalog.
    facade = CapabilityRegistryFacade()
    llm_desc = CapabilityDescriptor(
        capability_id="llm.test",
        description="test llm capability",
        supported_operations=("chat_completion",),
        provider_kind=ProviderKind.LLM_INFERENCE,
        effect_kind=EffectKind.EXECUTE,
    )
    facade._descriptors["llm.test"] = llm_desc  # noqa: SLF001 — test-only inject

    # Make resolve() succeed for llm.test by monkeypatching facade.resolve
    original_resolve = facade.resolve

    def _resolve(capability: str, operation: str):
        if capability == "llm.test" and operation == "chat_completion":
            from backend.aix_core.runtime.foundation.capability_registry import OperationSpec

            return (
                "llm.test",
                "chat_completion",
                OperationSpec(name="chat_completion", arguments=(), mutates=False),
            )
        return original_resolve(capability, operation)

    facade.resolve = _resolve  # type: ignore[method-assign]

    # Skip policy for synthetic id by stubbing authorize to allow
    from backend.aix_core.runtime.foundation.policy_gateway import PolicyDecision

    facade.authorize = lambda *a, **k: PolicyDecision(allowed=True)  # type: ignore[method-assign]

    resolver = CapabilityProviderResolver(facade=facade, governed_router=gpr)
    req = ActionRequestV1(
        action_id="llm1",
        capability="llm.test",
        operation="chat_completion",
        arguments={},
    )
    result = resolver.resolve(
        req,
        repository_id="repo-1",
        caller_role="executor",
        model_ref="claude-3-7-sonnet",
    )
    assert result.allowed is True
    assert result.llm_route is not None
    assert result.llm_route.disposition == "ALLOW"
    assert result.llm_route.route is not None
    assert direct.route is not None
    assert result.llm_route.route.provider_ref == direct.route.provider_ref
    assert result.llm_route.route.route_ref == direct.route.route_ref


def test_llm_direct_provider_bypass_denied() -> None:
    gpr = GovernedProviderRouter()
    facade = CapabilityRegistryFacade()
    facade._descriptors["llm.test"] = CapabilityDescriptor(  # noqa: SLF001
        capability_id="llm.test",
        description="test",
        supported_operations=("chat_completion",),
        provider_kind=ProviderKind.LLM_INFERENCE,
        effect_kind=EffectKind.EXECUTE,
    )

    def _resolve(capability: str, operation: str):
        if capability == "llm.test":
            from backend.aix_core.runtime.foundation.capability_registry import OperationSpec

            return ("llm.test", "chat_completion", OperationSpec("chat_completion", ()))
        return None

    facade.resolve = _resolve  # type: ignore[method-assign]
    from backend.aix_core.runtime.foundation.policy_gateway import PolicyDecision

    facade.authorize = lambda *a, **k: PolicyDecision(allowed=True)  # type: ignore[method-assign]

    resolver = CapabilityProviderResolver(facade=facade, governed_router=gpr)
    req = ActionRequestV1(
        action_id="llm2",
        capability="llm.test",
        operation="chat_completion",
        arguments={},
    )
    result = resolver.resolve(
        req,
        repository_id="repo-1",
        caller_role="executor",
        model_ref="claude-3-7-sonnet",
        route_kind="direct_provider",
    )
    assert result.allowed is False
    assert "DIRECT_PROVIDER_ROUTE_FORBIDDEN" in result.reason_codes


def test_route_and_execute_fail_closed_without_provider() -> None:
    resolver = CapabilityProviderResolver()

    async def _run() -> None:
        out = await resolver.route_and_execute(
            CapabilityExecutionRequest(
                capability_id="filesystem",
                operation="read",
                caller_role="executor",
                arguments={"path": "a.py"},
            ),
            kind=ProviderKind.LOCAL_TOOL,
        )
        assert out.success is False
        assert out.error_code == "PROVIDER_NOT_REGISTERED"

    import asyncio

    asyncio.run(_run())


def test_resolver_reuses_same_governed_router_instance() -> None:
    gpr = GovernedProviderRouter()
    resolver = CapabilityProviderResolver(governed_router=gpr)
    assert resolver.governed_router is gpr
    assert type(resolver.governed_router) is GovernedProviderRouter


def test_no_second_governed_router_class_in_provider_module() -> None:
    import backend.aix_core.framework.capability.provider as mod

    assert not hasattr(mod, "GovernedProviderRouter") or mod.GovernedProviderRouter is GovernedProviderRouter
    # Must import GPR from infrastructure — not redefine rules
    src = Path(mod.__file__).read_text(encoding="utf-8")
    assert "class GovernedProviderRouter" not in src
    assert "FORBIDDEN_ROUTE_KINDS" not in src
    assert "from backend.infrastructure.model_gateway.provider_routing import" in src


def test_no_objective_cycle_wiring() -> None:
    """Provider module may be imported from capability_executor under flag — OC stays clean."""
    root = Path(__file__).resolve().parents[3] / "aix_core" / "runtime" / "objective"
    offenders: list[str] = []
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "CapabilityProviderResolver" in text or "framework.capability.provider" in text:
            offenders.append(str(path))
    assert offenders == []
