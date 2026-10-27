"""AIX-MODEL-REGISTRY-001 — canonical role→model registry tests."""

from pathlib import Path

import pytest

from backend.aix_core.runtime.model_registry import (
    ModelRegistry,
    get_model_registry,
)
from backend.aix_core.runtime.model_registry.independence import (
    IndependenceViolation,
    ModelIndependenceGuard,
    SelfVerificationError,
    extract_model_family,
)

CANONICAL = {
    "objective_understanding": ("openai/gpt-5.6-luna", []),
    "planner": ("deepseek/deepseek-v3.2", ["openai/gpt-5.6-luna"]),
    "complex_planner": ("openai/gpt-5.6-luna", []),
    "executor": ("qwen/qwen3-coder-next", ["qwen/qwen3-coder-30b-a3b"]),
    "bulk_executor": ("qwen/qwen3-coder-30b-a3b", []),
    "tester": ("qwen/qwen3-coder-30b-a3b", ["qwen/qwen3-coder-next"]),
    "verifier": ("deepseek/deepseek-v3.2", ["openai/gpt-5.6-luna"]),
    "critical_reviewer": ("openai/gpt-5.6-luna", []),
}


@pytest.fixture
def registry() -> ModelRegistry:
    config = Path(__file__).resolve().parents[3] / "aix_core" / "runtime" / "model_registry" / "models.json"
    return ModelRegistry.from_config(config)


def test_models_json_has_four_profiles(registry: ModelRegistry) -> None:
    assert len(registry._profiles) == 4  # noqa: SLF001
    assert registry.get_profile("openai/gpt-5.6-luna") is not None
    assert registry.get_profile("deepseek/deepseek-v3.2") is not None
    assert registry.get_profile("qwen/qwen3-coder-next") is not None
    assert registry.get_profile("qwen/qwen3-coder-30b-a3b") is not None


@pytest.mark.parametrize("role,expected", list(CANONICAL.items()))
def test_canonical_primary_and_fallback(registry: ModelRegistry, role: str, expected: tuple) -> None:
    primary, fallbacks = expected
    policy = registry.get_policy(role)  # type: ignore[arg-type]
    assert policy is not None
    assert policy.primary_model_id == primary
    assert list(policy.fallback_model_ids) == fallbacks


def test_get_route_planner_normal(registry: ModelRegistry) -> None:
    route = registry.get_route("planner", complexity="NORMAL")
    assert route.model_id == "deepseek/deepseek-v3.2"
    assert route.family == "deepseek-v3.2"


def test_get_route_executor_low_uses_bulk(registry: ModelRegistry) -> None:
    route = registry.get_route("executor", complexity="LOW")
    assert route.model_id == "qwen/qwen3-coder-30b-a3b"


def test_get_route_escalation_uses_luna(registry: ModelRegistry) -> None:
    route = registry.get_route("verifier", risk="ESCALATION", repair_attempt_count=2, escalated=True)
    assert route.model_id == "openai/gpt-5.6-luna"
    assert route.role == "critical_reviewer"


def test_get_route_planner_fallback_on_retry(registry: ModelRegistry) -> None:
    route = registry.get_route(
        "planner",
        retry_history=("deepseek/deepseek-v3.2",),
    )
    assert route.model_id == "openai/gpt-5.6-luna"
    assert route.fallback_used is True


def test_get_model_registry_singleton() -> None:
    assert get_model_registry().get_route("planner").model_id == "deepseek/deepseek-v3.2"


def test_extract_model_family_canonical_ids() -> None:
    assert extract_model_family("openai/gpt-5.6-luna") == "gpt-5.6-luna"
    assert extract_model_family("deepseek/deepseek-v3.2") == "deepseek-v3.2"
    assert extract_model_family("qwen/qwen3-coder-next") == "qwen3-coder"
    assert extract_model_family("qwen/qwen3-coder-30b-a3b") == "qwen3-coder-30b"


def test_independence_planner_executor_verifier_differ() -> None:
    guard = ModelIndependenceGuard()
    res = guard.validate_role_models(
        planner_model="deepseek/deepseek-v3.2",
        executor_model="qwen/qwen3-coder-next",
        verifier_model="deepseek/deepseek-v3.2",
    )
    assert res.is_independent


def test_independence_blocks_executor_verifier_same_family() -> None:
    guard = ModelIndependenceGuard()
    with pytest.raises(SelfVerificationError):
        guard.validate_role_models(
            planner_model="deepseek/deepseek-v3.2",
            executor_model="qwen/qwen3-coder-next",
            verifier_model="qwen/qwen3-coder-30b-a3b",
        )


def test_independence_blocks_planner_executor_same_family() -> None:
    guard = ModelIndependenceGuard()
    with pytest.raises(IndependenceViolation):
        guard.validate_role_models(
            planner_model="deepseek/deepseek-v3.2",
            executor_model="deepseek/deepseek-v3.2",
            verifier_model="openai/gpt-5.6-luna",
        )
