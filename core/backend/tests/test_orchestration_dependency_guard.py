from __future__ import annotations

import pytest

from backend.services.orchestration_dependency_guard import (
    collect_orchestration_dependency_status,
    enforce_orchestration_dependencies,
)


class _Settings:
    TEMPORAL_ENABLED = True
    LANGGRAPH_ENABLED = True
    HAYSTACK_RETRIEVAL_ENABLED = True
    ORCHESTRATION_STRICT_DEPENDENCY_CHECK = True


def test_collect_orchestration_dependency_status_shape() -> None:
    status = collect_orchestration_dependency_status(_Settings())
    assert "temporal" in status
    assert "langgraph" in status
    assert "haystack" in status
    assert status["strict_dependency_check"] is True


def test_enforce_orchestration_dependencies_raises_when_enabled_missing() -> None:
    with pytest.raises(RuntimeError) as exc:
        enforce_orchestration_dependencies(
            settings=_Settings(),
            status={
                "strict_dependency_check": True,
                "temporal": {"enabled": True, "installed": False, "package_name": "temporalio"},
                "langgraph": {"enabled": True, "installed": True, "package_name": "langgraph"},
                "haystack": {"enabled": True, "installed": False, "package_name": "haystack-ai"},
            },
        )
    assert "ORCHESTRATION_DEPENDENCY_MISSING" in str(exc.value)
    assert "temporalio" in str(exc.value)
    assert "haystack-ai" in str(exc.value)


def test_enforce_orchestration_dependencies_skips_when_not_strict() -> None:
    status = enforce_orchestration_dependencies(
        settings=_Settings(),
        status={
            "strict_dependency_check": False,
            "temporal": {"enabled": True, "installed": False, "package_name": "temporalio"},
            "langgraph": {"enabled": True, "installed": False, "package_name": "langgraph"},
            "haystack": {"enabled": True, "installed": False, "package_name": "haystack-ai"},
        },
    )
    assert status["strict_dependency_check"] is False
