from __future__ import annotations

import pytest

from backend.services.config.model_routing_loader import ModelRoutingConfigLoadError
from backend.services.model_lane_policy_service import ModelLanePolicyService, resolve_model_lane_config


def test_resolve_model_lane_config_uses_yaml_planner_persona_for_planning_safe() -> None:
    selected = resolve_model_lane_config(
        metadata={},
        instruction_contract={},
        lane="planning_safe",
        phase="planning",
    )

    assert selected["role"] == "planner"
    assert selected["persona"] == "structured_logician"
    assert selected["provider"] == "openrouter"
    assert selected["model"] == "openrouter/openai/gpt-5.1-codex"
    assert len(selected["candidate_pool"]) == 3
    assert selected["candidate_pool"][0]["circuit_breaker_window_seconds"] == 1800
    assert selected["candidate_pool"][0]["circuit_breaker_failure_threshold"] == 2
    assert selected["candidate_pool"][1]["provider"] == "openrouter"
    assert selected["candidate_pool"][1]["model"] == "openrouter/deepseek/deepseek-v3.2"
    assert selected["candidate_pool"][2]["model"] == "openrouter/mistralai/codestral-2508"
    assert selected["candidate_pool"][2]["timeout_seconds"] == 60
    assert selected["candidate_pool"][1]["circuit_breaker_window_seconds"] == 1800
    assert selected["candidate_pool"][1]["circuit_breaker_failure_threshold"] == 2


def test_resolve_model_lane_config_routes_bounded_protocol_extension_to_grounded_protocol_planner() -> None:
    selected = resolve_model_lane_config(
        metadata={},
        instruction_contract={},
        lane="planning_safe",
        phase="impl",
        prompt_characteristics={"mutation_kind": "bounded_method_protocol_extension", "oversized": False},
    )

    assert selected["role"] == "planner"
    assert selected["persona"] == "grounded_protocol_planner"
    assert selected["provider"] == "openrouter"
    assert selected["model"] == "openrouter/openai/gpt-5.1-codex"
    assert selected["candidate_pool"][1]["timeout_seconds"] == 60
    assert selected["candidate_pool"][0]["circuit_breaker_window_seconds"] == 1800
    assert selected["candidate_pool"][0]["circuit_breaker_failure_threshold"] == 2
    assert selected["candidate_pool"][2]["model"] == "openrouter/deepseek/deepseek-v3.2"
    assert selected["candidate_pool"][2]["circuit_breaker_window_seconds"] == 1800
    assert selected["candidate_pool"][2]["circuit_breaker_failure_threshold"] == 2
    assert len(selected["candidate_pool"]) == 3


def test_resolve_model_lane_config_routes_oversized_protocol_extension_back_to_structured_logician() -> None:
    selected = resolve_model_lane_config(
        metadata={},
        instruction_contract={},
        lane="planning_safe",
        phase="impl",
        prompt_characteristics={"mutation_kind": "bounded_method_protocol_extension", "oversized": True},
    )

    assert selected["role"] == "planner"
    assert selected["persona"] == "structured_logician"
    assert selected["provider"] == "openrouter"
    assert selected["model"] == "openrouter/openai/gpt-5.1-codex"


def test_resolve_model_lane_config_uses_yaml_coder_persona_for_mutation() -> None:
    selected = resolve_model_lane_config(
        metadata={},
        instruction_contract={},
        lane="mutation",
        phase="mutation",
    )

    assert selected["role"] == "coder"
    assert selected["persona"] == "mutation_artisan"
    assert selected["model"] == "openrouter/openai/gpt-5.1-codex"


def test_resolve_model_lane_config_routes_mutation_repair_to_transport_diverse_persona() -> None:
    selected = resolve_model_lane_config(
        metadata={},
        instruction_contract={},
        lane="mutation_repair",
        phase="mutation",
    )

    assert selected["role"] == "coder"
    assert selected["persona"] == "mutation_repair_artisan"
    assert selected["model"] == "openrouter/openai/gpt-5.1-codex"
    assert selected["candidate_pool"][1]["provider"] == "openrouter"
    assert selected["candidate_pool"][1]["model"] == "openrouter/deepseek/deepseek-v3.2"
    assert selected["candidate_pool"][2]["model"] == "openrouter/mistralai/codestral-2508"


def test_resolve_model_lane_config_routes_high_complexity_mutation_extraction_to_structured_logician() -> None:
    extraction = resolve_model_lane_config(
        metadata={},
        instruction_contract={},
        lane="mutation_extraction",
        phase="impl",
        prompt_characteristics={"complexity": "high", "task_family": "mutation_extraction"},
    )

    assert extraction["requested_lane"] == "mutation_extraction"
    assert extraction["lane"] == "mutation_extraction"
    assert extraction["persona"] == "structured_logician"
    assert extraction["routed_from_lane"] == "mutation_extraction"
    assert extraction["model"] == "openrouter/openai/gpt-5.1-codex"


def test_resolve_model_lane_config_routes_oversized_mutation_extraction_to_structured_logician() -> None:
    extraction = resolve_model_lane_config(
        metadata={},
        instruction_contract={},
        lane="mutation_extraction",
        phase="impl",
        prompt_characteristics={"complexity": "normal", "task_family": "mutation_extraction", "oversized": True},
    )

    assert extraction["persona"] == "structured_logician"
    assert extraction["model"] == "openrouter/openai/gpt-5.1-codex"


def test_resolve_model_lane_config_ignores_metadata_and_instruction_model_overrides() -> None:
    selected = resolve_model_lane_config(
        metadata={
            "planner_provider": "openrouter",
            "planner_model": "openrouter/qwen/qwen3-coder-next",
            "model_lane_policy": {
                "routing": {
                    "mutation": {
                        "role": "coder",
                        "default_persona": "structured_logician",
                    }
                }
            },
        },
        instruction_contract={
            "model_lane_policy": {
                "routing": {
                    "mutation": {
                        "role": "planner",
                        "default_persona": "structured_logician",
                    }
                }
            }
        },
        lane="mutation",
        phase="mutation",
    )

    assert selected["provider"] == "openrouter"
    assert selected["model"] == "openrouter/openai/gpt-5.1-codex"
    assert selected["persona"] == "mutation_artisan"


def test_model_lane_policy_service_uses_canonical_config_by_default() -> None:
    service = ModelLanePolicyService()

    selected = service.resolve(
        metadata={},
        instruction_contract={},
        lane="verification",
        phase="verification",
    )

    assert selected["role"] == "verifier"
    assert selected["persona"] == "surgical_auditor"
    assert selected["model"] == "openrouter/openai/gpt-5.1-codex"
    assert selected["source_chain"][0]["layer"] == "base_config"


def test_model_lane_policy_service_fails_closed_when_config_missing() -> None:
    service = ModelLanePolicyService(
        config_path="C:/dev/hyeve/projects/AIX/hyeve-aix-service/backend/config/missing.yaml"
    )

    with pytest.raises(ModelRoutingConfigLoadError, match="MODEL_ROUTING_CONFIG_NOT_FOUND"):
        service.resolve(
            metadata={},
            instruction_contract={},
            lane="mutation",
            phase="mutation",
        )


def test_model_lane_policy_service_uses_candidate_provider_authoritatively() -> None:
    service = ModelLanePolicyService()

    selected = service.resolve(
        metadata={},
        instruction_contract={},
        lane="mutation",
        phase="mutation",
        prompt_characteristics=None,
    )

    assert selected["provider"] == "openrouter"
    assert selected["candidate_pool"][0]["provider"] == "openrouter"


def test_model_lane_policy_service_preserves_mixed_provider_candidate_pool() -> None:
    service = ModelLanePolicyService()

    selected = service.resolve(
        metadata={},
        instruction_contract={},
        lane="mutation",
        phase="mutation",
        prompt_characteristics=None,
    )

    assert selected["provider"] == "openrouter"
    assert selected["model"] == "openrouter/openai/gpt-5.1-codex"

    mixed_selected = service.resolve(
        metadata={},
        instruction_contract={},
        lane="mutation",
        phase="mutation",
        prompt_characteristics=None,
    )

    assert mixed_selected["candidate_pool"][1]["provider"] == "openrouter"


def test_model_lane_policy_service_supports_mixed_provider_overrides() -> None:
    service = ModelLanePolicyService()
    base_policy = {
        "roles": {"coder": type("Role", (), {"default_persona": "mutation_artisan"})()},
        "personas": {
            "mutation_artisan": type(
                "Persona",
                (),
                {
                    "candidates": [
                        type(
                            "Candidate",
                            (),
                            {
                                "provider": "onlineai",
                                "model": "gpt-4.1-mini",
                                "priority": 100,
                                "cost_tier": "premium",
                                "timeout_seconds": 60,
                                "max_retries": 0,
                                "retry_backoff_ms": 250,
                                "temperature": None,
                                "max_steps": None,
                                "tags": ["coder", "primary"],
                            },
                        )(),
                        type(
                            "Candidate",
                            (),
                            {
                                "provider": "openrouter",
                                "model": "deepseek/deepseek-r1",
                                "priority": 80,
                                "cost_tier": "balanced",
                                "timeout_seconds": 60,
                                "max_retries": 0,
                                "retry_backoff_ms": 250,
                                "temperature": None,
                                "max_steps": None,
                                "tags": ["coder", "fallback"],
                            },
                        )(),
                    ]
                },
            )()
        },
        "routing": {
            "mutation": type("Route", (), {"role": "coder", "default_persona": "mutation_artisan", "conditions": []})()
        },
        "policy": object(),
    }

    selected = service.resolve(
        metadata={},
        instruction_contract={},
        lane="mutation",
        phase="mutation",
    )

    assert selected["provider"] == "openrouter"
    assert selected["model"] == "openrouter/openai/gpt-5.1-codex"

    overridden = __import__(
        "backend.services.model_lane_policy_service", fromlist=["_resolve_model_lane_config"]
    )._resolve_model_lane_config(
        metadata={},
        instruction_contract={},
        base_policy=base_policy,
        lane="mutation",
        phase="mutation",
    )

    assert overridden["provider"] == "onlineai"
    assert overridden["model"] == "gpt-4.1-mini"
    assert overridden["candidate_pool"][0]["provider"] == "onlineai"
    assert overridden["candidate_pool"][0]["model"] == "gpt-4.1-mini"
    assert overridden["candidate_pool"][1]["provider"] == "openrouter"
    assert overridden["candidate_pool"][1]["model"] == "openrouter/deepseek/deepseek-r1"


def test_resolve_model_lane_config_rejects_non_planner_role_for_planning_lane() -> None:
    module = __import__("backend.services.model_lane_policy_service", fromlist=["_resolve_model_lane_config"])

    bad_policy = {
        "roles": {"observer": type("Role", (), {"default_persona": "high_speed_scout"})()},
        "personas": {
            "high_speed_scout": type(
                "Persona",
                (),
                {
                    "purpose": "observer",
                    "candidates": [
                        type(
                            "Candidate",
                            (),
                            {
                                "provider": "openrouter",
                                "model": "nvidia/nemotron-3-super-120b-a12b",
                                "priority": 100,
                                "cost_tier": "balanced",
                                "timeout_seconds": 180,
                                "max_retries": 0,
                                "retry_backoff_ms": 0,
                                "temperature": None,
                                "max_steps": None,
                                "tags": ["observer"],
                            },
                        )()
                    ],
                },
            )()
        },
        "routing": {
            "planning_safe": type(
                "Route", (), {"role": "observer", "default_persona": "high_speed_scout", "conditions": []}
            )()
        },
        "policy": {},
    }

    with pytest.raises(ModelRoutingConfigLoadError, match="MODEL_LANE_POLICY_INVALID_PLANNER_ROLE"):
        module._resolve_model_lane_config(
            metadata={},
            instruction_contract={},
            base_policy=bad_policy,
            lane="planning_safe",
            phase="planning",
            prompt_characteristics={},
        )
