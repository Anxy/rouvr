from __future__ import annotations

from typing import Any

from backend.services.config.model_routing_config_schema import (
    SUPPORTED_MODEL_ROUTING_CONFIG_VERSIONS,
    ModelRoutingConfigV2,
)


def _normalize_provider(value: object) -> str:
    return str(value or "").strip().lower()


def _normalize_model_for_provider(*, provider: str, model: object) -> str:
    text = str(model or "").strip()
    if provider == "openrouter" and text and not text.lower().startswith("openrouter/"):
        return f"openrouter/{text}"
    return text


def _validate_raw_candidate_payloads(payload: dict[str, Any]) -> None:
    personas = payload.get("personas")
    if not isinstance(personas, dict):
        return
    for persona_name, persona_payload in personas.items():
        if not isinstance(persona_payload, dict):
            continue
        candidates = persona_payload.get("candidates")
        if not isinstance(candidates, list):
            continue
        seen_bindings: set[tuple[str, str]] = set()
        for index, candidate_payload in enumerate(candidates):
            if not isinstance(candidate_payload, dict):
                continue
            provider = _normalize_provider(candidate_payload.get("provider"))
            raw_model = str(candidate_payload.get("model") or "").strip()
            if provider == "openrouter" and raw_model.lower().startswith("openrouter/"):
                raise ValueError(
                    f"persona '{persona_name}' candidate[{index}] uses deprecated openrouter-prefixed model "
                    f"'{raw_model}'. Use bare upstream slugs such as 'google/gemini-3.1-pro-preview'."
                )
            normalized_binding = (provider, _normalize_model_for_provider(provider=provider, model=raw_model).lower())
            if normalized_binding in seen_bindings:
                raise ValueError(
                    f"persona '{persona_name}' defines duplicate candidate binding provider='{provider}' "
                    f"model='{normalized_binding[1]}'"
                )
            seen_bindings.add(normalized_binding)


def validate_model_routing_config(payload: dict) -> ModelRoutingConfigV2:
    schema_version = str(payload.get("schema_version") or "").strip()
    if schema_version not in SUPPORTED_MODEL_ROUTING_CONFIG_VERSIONS:
        raise ValueError(
            f"Unsupported model routing config schema_version '{schema_version}'. "
            f"Supported: {sorted(SUPPORTED_MODEL_ROUTING_CONFIG_VERSIONS)}"
        )
    _validate_raw_candidate_payloads(payload)
    config = ModelRoutingConfigV2.model_validate(payload)
    if not config.roles:
        raise ValueError("Model routing config must define at least one role")
    if not config.personas:
        raise ValueError("Model routing config must define at least one persona")
    if not config.routing:
        raise ValueError("Model routing config must define at least one routing entry")
    if not config.policy.yaml_only:
        raise ValueError("Model routing config policy must enforce yaml_only=true")
    return config
