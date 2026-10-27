"""Map runtime AgentRole names ↔ framework Role for F7 handoff authority."""

from __future__ import annotations

from backend.aix_core.framework.agent.models import Role

# Runtime P14 names → F7 five-role taxonomy (where equivalent exists).
RUNTIME_TO_FRAMEWORK_ROLE: dict[str, Role] = {
    "planner": Role.PLANNER,
    "coder": Role.EXECUTOR_CODER,
    "tester": Role.VERIFIER,
    "reviewer": Role.REVIEW_PACKET_CURATOR,
    # recovery_planner not in P14 registry; framework-only
}


def to_framework_role(runtime_role: str | Role) -> Role | None:
    if isinstance(runtime_role, Role):
        return runtime_role
    key = str(runtime_role or "").strip().lower()
    return RUNTIME_TO_FRAMEWORK_ROLE.get(key)


def agent_binding_enabled(settings: object | None = None) -> bool:
    if settings is not None and hasattr(settings, "AIX_FRAMEWORK_AGENT_BINDING"):
        return bool(getattr(settings, "AIX_FRAMEWORK_AGENT_BINDING"))
    try:
        from backend.hyeve_aix_service.config import settings as _settings

        return bool(getattr(_settings, "AIX_FRAMEWORK_AGENT_BINDING", False))
    except Exception:
        return False


__all__ = [
    "RUNTIME_TO_FRAMEWORK_ROLE",
    "agent_binding_enabled",
    "to_framework_role",
]
