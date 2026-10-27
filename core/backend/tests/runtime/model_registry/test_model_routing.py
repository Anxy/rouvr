"""AIX-MODEL-REGISTRY-001 — routing complexity and escalation tests."""

from pathlib import Path

import pytest

from backend.aix_core.runtime.model_registry.registry import ModelRegistry


@pytest.fixture
def registry() -> ModelRegistry:
    config = Path(__file__).resolve().parents[3] / "aix_core" / "runtime" / "model_registry" / "models.json"
    return ModelRegistry.from_config(config)


def test_select_role_low_complexity(registry: ModelRegistry) -> None:
    assert registry.select_role(complexity="LOW") == "bulk_executor"


def test_select_role_high_risk(registry: ModelRegistry) -> None:
    assert registry.select_role(risk="ESCALATION") == "critical_reviewer"


def test_select_role_planning_task(registry: ModelRegistry) -> None:
    assert registry.select_role(task_kind="planning") == "planner"


def test_select_role_verification_task(registry: ModelRegistry) -> None:
    assert registry.select_role(task_kind="verification") == "verifier"


def test_repair_attempt_two_routes_critical_reviewer(registry: ModelRegistry) -> None:
    route = registry.get_route("planner", repair_attempt_count=2)
    assert route.role == "critical_reviewer"
    assert route.model_id == "openai/gpt-5.6-luna"
