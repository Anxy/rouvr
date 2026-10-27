"""AIX Feature 12 (Packet 10) — Model Independence Guard tests."""

import pytest

from backend.aix_core.runtime.model_registry.independence import (
    IndependenceViolation,
    ModelIndependenceGuard,
    SelfVerificationError,
    extract_model_family,
)


def test_extract_model_family() -> None:
    assert extract_model_family("openai/gpt-5.6-luna") == "gpt-5.6-luna"
    assert extract_model_family("deepseek/deepseek-v3.2") == "deepseek-v3.2"
    assert extract_model_family("qwen/qwen3-coder-next") == "qwen3-coder"
    assert extract_model_family("qwen/qwen3-coder-30b-a3b") == "qwen3-coder-30b"


def test_model_independence_passes_on_different_families() -> None:
    guard = ModelIndependenceGuard(enforce_family_separation=True)
    res = guard.validate_role_models(
        planner_model="deepseek/deepseek-v3.2",
        executor_model="qwen/qwen3-coder-next",
        verifier_model="deepseek/deepseek-v3.2",
        certifier_model="openai/gpt-5.6-luna",
    )

    assert res.is_independent
    assert len(res.violations) == 0
    assert res.provenance_record["produced_by"] == "qwen/qwen3-coder-next"
    assert res.provenance_record["verified_by"] == "deepseek/deepseek-v3.2"


def test_model_independence_fails_when_verifier_shares_family_with_executor() -> None:
    guard = ModelIndependenceGuard(enforce_family_separation=True)
    with pytest.raises(SelfVerificationError):
        guard.validate_role_models(
            planner_model="deepseek/deepseek-v3.2",
            executor_model="qwen/qwen3-coder-next",
            verifier_model="qwen/qwen3-coder-30b-a3b",
        )


def test_model_independence_allows_escalated_self_verify() -> None:
    guard = ModelIndependenceGuard(enforce_family_separation=True)
    res = guard.validate_role_models(
        planner_model="deepseek/deepseek-v3.2",
        executor_model="qwen/qwen3-coder-next",
        verifier_model="qwen/qwen3-coder-30b-a3b",
        escalated=True,
    )
    assert res.is_independent
