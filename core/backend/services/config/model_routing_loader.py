from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

from backend.services.config.model_routing_config_schema import ModelRoutingConfigV2
from backend.services.config.model_routing_validator import validate_model_routing_config

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_MODEL_ROUTING_CONFIG_PATH = BACKEND_ROOT / "config" / "model_routing_config.yaml"


class ModelRoutingConfigLoadError(RuntimeError):
    pass


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def _load_yaml_dict(config_path: str | Path) -> dict[str, Any]:
    path = Path(config_path)
    if not path.exists():
        raise ModelRoutingConfigLoadError(f"MODEL_ROUTING_CONFIG_NOT_FOUND path={path}")
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:  # pragma: no cover - defensive
        raise ModelRoutingConfigLoadError(f"MODEL_ROUTING_CONFIG_PARSE_FAILED path={path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ModelRoutingConfigLoadError(f"MODEL_ROUTING_CONFIG_INVALID_ROOT path={path}")
    return payload


def load_model_routing_config(*, config_path: str | Path = DEFAULT_MODEL_ROUTING_CONFIG_PATH) -> ModelRoutingConfigV2:
    payload = _load_yaml_dict(config_path)
    try:
        return validate_model_routing_config(payload)
    except Exception as exc:
        raise ModelRoutingConfigLoadError(f"MODEL_ROUTING_CONFIG_INVALID path={config_path}: {exc}") from exc


def resolve_effective_model_routing_config(
    *,
    config_path: str | Path = DEFAULT_MODEL_ROUTING_CONFIG_PATH,
    repo_override: dict[str, Any] | None = None,
    tenant_override: dict[str, Any] | None = None,
    operator_override: dict[str, Any] | None = None,
) -> tuple[ModelRoutingConfigCurrent, dict[str, Any]]:
    base_payload = _load_yaml_dict(config_path)
    merged_payload = deepcopy(base_payload)
    applied_layers: list[dict[str, Any]] = [{"layer": "base_config", "source": str(Path(config_path))}]

    for layer_name, overlay in (
        ("repo_override", repo_override),
        ("tenant_override", tenant_override),
        ("operator_override", operator_override),
    ):
        if not overlay:
            continue
        if not isinstance(overlay, dict):
            raise ModelRoutingConfigLoadError(f"MODEL_ROUTING_CONFIG_INVALID_OVERLAY layer={layer_name}")
        merged_payload = _deep_merge(merged_payload, overlay)
        applied_layers.append({"layer": layer_name, "paths": sorted(overlay.keys())})

    try:
        resolved = validate_model_routing_config(merged_payload)
    except Exception as exc:
        raise ModelRoutingConfigLoadError(f"MODEL_ROUTING_CONFIG_INVALID_MERGED: {exc}") from exc

    diagnostics = {
        "source_chain": applied_layers,
        "role_names": sorted(resolved.roles.keys()),
        "persona_names": sorted(resolved.personas.keys()),
        "routing_names": sorted(resolved.routing.keys()),
        "snapshot": model_routing_config_snapshot(config=resolved, config_path=config_path),
    }
    return resolved, diagnostics


def model_routing_config_snapshot(
    *,
    config: ModelRoutingConfigV2,
    config_path: str | Path,
) -> dict[str, Any]:
    normalized_payload = config.model_dump(mode="json")
    payload_hash = hashlib.sha256(
        json.dumps(normalized_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    persona_candidate_counts = {
        name: len(persona.candidates or []) for name, persona in sorted(config.personas.items())
    }
    persona_candidates = {
        name: [
            {
                "provider": candidate.provider,
                "model": candidate.model,
                "priority": int(candidate.priority),
            }
            for candidate in sorted(persona.candidates, key=lambda item: int(item.priority), reverse=True)
        ]
        for name, persona in sorted(config.personas.items())
    }
    return {
        "config_path": str(Path(config_path)),
        "schema_version": config.schema_version,
        "config_id": config.config_id,
        "version": int(config.version),
        "config_hash": payload_hash,
        "role_names": sorted(config.roles.keys()),
        "persona_names": sorted(config.personas.keys()),
        "routing_names": sorted(config.routing.keys()),
        "persona_candidate_counts": persona_candidate_counts,
        "persona_candidates": persona_candidates,
    }
