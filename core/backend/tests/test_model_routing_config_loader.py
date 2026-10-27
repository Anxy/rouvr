from __future__ import annotations

from pathlib import Path

import pytest
import yaml  # type: ignore[import-untyped]

from backend.services.config.model_routing_config_schema import MODEL_ROUTING_CONFIG_SCHEMA_VERSION_V2
from backend.services.config.model_routing_loader import (
    DEFAULT_MODEL_ROUTING_CONFIG_PATH,
    ModelRoutingConfigLoadError,
    load_model_routing_config,
    model_routing_config_snapshot,
    resolve_effective_model_routing_config,
)


def test_load_model_routing_config_fixture_is_valid() -> None:
    config = load_model_routing_config()
    assert config.schema_version == MODEL_ROUTING_CONFIG_SCHEMA_VERSION_V2
    assert config.roles["planner"].default_persona == "structured_logician"
    assert [candidate.model for candidate in config.personas["structured_logician"].candidates] == [
        "openrouter/openai/gpt-5.1-codex",
        "openrouter/deepseek/deepseek-v3.2",
        "openrouter/mistralai/codestral-2508",
    ]
    assert config.personas["structured_logician"].candidates[0].circuit_breaker_window_seconds == 1800
    assert config.personas["structured_logician"].candidates[0].circuit_breaker_failure_threshold == 2
    assert config.personas["structured_logician"].candidates[2].timeout_seconds == 60
    assert config.personas["structured_logician"].candidates[1].circuit_breaker_window_seconds == 1800
    assert config.personas["structured_logician"].candidates[1].circuit_breaker_failure_threshold == 2
    assert config.personas["mutation_artisan"].candidates[0].model == "openrouter/openai/gpt-5.1-codex"
    assert config.personas["mutation_repair_artisan"].candidates[2].provider == "openrouter"
    assert config.personas["mutation_repair_artisan"].candidates[2].model == "openrouter/mistralai/codestral-2508"
    assert config.personas["grounded_protocol_planner"].candidates[1].timeout_seconds == 60
    assert config.personas["grounded_protocol_planner"].candidates[0].circuit_breaker_window_seconds == 1800
    assert config.personas["grounded_protocol_planner"].candidates[0].circuit_breaker_failure_threshold == 2
    assert config.personas["domain_consultant"].candidates[0].model == "openrouter/openai/gpt-5.1-codex"
    assert config.routing["mutation_repair"].default_persona == "mutation_repair_artisan"
    assert config.routing["verification"].role == "verifier"


def test_load_model_routing_config_rejects_unknown_schema_version(tmp_path: Path) -> None:
    path = tmp_path / "model_routing_config.yaml"
    payload = yaml.safe_load(DEFAULT_MODEL_ROUTING_CONFIG_PATH.read_text(encoding="utf-8"))
    payload["schema_version"] = "model_routing_config.v9"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    with pytest.raises(ModelRoutingConfigLoadError, match="Unsupported model routing config schema_version"):
        load_model_routing_config(config_path=path)


def test_resolve_effective_model_routing_config_applies_precedence() -> None:
    resolved, diagnostics = resolve_effective_model_routing_config(
        operator_override={
            "personas": {
                "mutation_artisan": {
                    "purpose": "coder",
                    "selection_mode": "ordered_fallback",
                    "candidates": [{"provider": "openrouter", "model": "mistralai/codestral-2508", "priority": 999}],
                }
            }
        }
    )
    assert resolved.personas["mutation_artisan"].candidates[0].model == "openrouter/mistralai/codestral-2508"
    assert [item["layer"] for item in diagnostics["source_chain"]] == [
        "base_config",
        "operator_override",
    ]


def test_resolve_effective_model_routing_config_fails_closed_on_invalid_overlay() -> None:
    with pytest.raises(ModelRoutingConfigLoadError, match="MODEL_ROUTING_CONFIG_INVALID_MERGED"):
        resolve_effective_model_routing_config(
            operator_override={"roles": {"planner": {"default_persona": "missing_persona"}}}
        )


def test_resolve_effective_model_routing_config_rejects_blank_candidate_provider() -> None:
    with pytest.raises(ModelRoutingConfigLoadError, match="MODEL_ROUTING_CONFIG_INVALID_MERGED"):
        resolve_effective_model_routing_config(
            operator_override={
                "personas": {
                    "mutation_artisan": {
                        "purpose": "coder",
                        "selection_mode": "ordered_fallback",
                        "candidates": [{"provider": "", "model": "mistralai/codestral-2508", "priority": 999}],
                    }
                }
            }
        )


def test_load_model_routing_config_rejects_deprecated_openrouter_prefixed_yaml_model(tmp_path: Path) -> None:
    path = tmp_path / "model_routing_config.yaml"
    payload = yaml.safe_load(DEFAULT_MODEL_ROUTING_CONFIG_PATH.read_text(encoding="utf-8"))
    payload["personas"]["structured_logician"]["candidates"][0]["model"] = "openrouter/deepseek/deepseek-v3.2"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    with pytest.raises(ModelRoutingConfigLoadError, match="deprecated openrouter-prefixed model"):
        load_model_routing_config(config_path=path)


def test_load_model_routing_config_rejects_duplicate_candidate_bindings(tmp_path: Path) -> None:
    path = tmp_path / "model_routing_config.yaml"
    payload = yaml.safe_load(DEFAULT_MODEL_ROUTING_CONFIG_PATH.read_text(encoding="utf-8"))
    duplicate = dict(payload["personas"]["structured_logician"]["candidates"][0])
    payload["personas"]["structured_logician"]["candidates"].append(duplicate)
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    with pytest.raises(ModelRoutingConfigLoadError, match="defines duplicate candidate binding"):
        load_model_routing_config(config_path=path)


def test_model_routing_config_snapshot_includes_hash_and_persona_counts() -> None:
    config = load_model_routing_config()
    snapshot = model_routing_config_snapshot(config=config, config_path=DEFAULT_MODEL_ROUTING_CONFIG_PATH)
    assert snapshot["config_hash"]
    assert snapshot["config_path"].endswith("model_routing_config.yaml")
    assert snapshot["persona_candidate_counts"]["structured_logician"] >= 1
    assert snapshot["persona_candidates"]["structured_logician"][0]["provider"] == "openrouter"
