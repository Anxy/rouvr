from __future__ import annotations

# AIX autonomous lane changes must run through scripts/run_aix_lane_gauntlet.sh before deploy.
from typing import Any

from backend.services.config.model_routing_loader import (
    DEFAULT_MODEL_ROUTING_CONFIG_PATH,
    ModelRoutingConfigLoadError,
    resolve_effective_model_routing_config,
)


def _normalize_text(value: object) -> str:
    return str(value or "").strip()


def _normalize_lower(value: object) -> str:
    return _normalize_text(value).lower()


def _default_lane_name(*, phase: str) -> str:
    if phase in {"tests", "verification"}:
        return "verification"
    if phase in {"diagnostic", "analysis"}:
        return "diagnostic"
    if phase == "planning":
        return "planning"
    return "mutation"


def _coerce_prompt_characteristics(prompt_characteristics: dict[str, Any] | None) -> dict[str, Any]:
    return dict(prompt_characteristics or {}) if isinstance(prompt_characteristics, dict) else {}


def _matches_condition(*, condition: dict[str, object], prompt_characteristics: dict[str, Any]) -> bool:
    for key, expected in condition.items():
        if key == "estimated_tokens_gte":
            actual_tokens = int(prompt_characteristics.get("estimated_tokens") or 0)
            try:
                expected_min = int(str(expected or "").strip())
            except (TypeError, ValueError):
                return False
            if actual_tokens < expected_min:
                return False
            continue
        actual = prompt_characteristics.get(key)
        if isinstance(expected, str):
            if _normalize_lower(actual) != _normalize_lower(expected):
                return False
            continue
        if actual != expected:
            return False
    return True


def _resolve_route_name(
    *,
    selected_name: str,
    routing: dict[str, Any],
    prompt_characteristics: dict[str, Any],
) -> tuple[str, str | None]:
    route = routing.get(selected_name)
    if not route:
        return selected_name, None
    for condition in route.conditions:
        if _matches_condition(condition=condition.when, prompt_characteristics=prompt_characteristics):
            return selected_name, condition.persona
    return selected_name, None


def _infer_cost_tier(*, cost_tier: str | None, model_text: str) -> str:
    normalized = _normalize_lower(cost_tier)
    if normalized:
        return normalized
    return "cheap" if "mini" in model_text.lower() else "premium"


def _canonicalize_provider_model(*, provider: str, model_text: str) -> str:
    normalized_model = _normalize_text(model_text)
    if (
        _normalize_lower(provider) == "openrouter"
        and normalized_model
        and not normalized_model.lower().startswith("openrouter/")
    ):
        return f"openrouter/{normalized_model}"
    return normalized_model


class ModelLanePolicyService:
    def __init__(self, *, config_path: str | None = None) -> None:
        self._config_path = config_path or str(DEFAULT_MODEL_ROUTING_CONFIG_PATH)

    def _load_base_policy(self) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        config, diagnostics = resolve_effective_model_routing_config(config_path=self._config_path)
        base_policy = {
            "roles": config.roles,
            "personas": config.personas,
            "routing": config.routing,
            "policy": config.policy,
            "metadata": dict(config.metadata or {}),
        }
        return base_policy, list(diagnostics.get("source_chain") or [])

    def resolve(
        self,
        *,
        metadata: dict[str, Any],
        instruction_contract: dict[str, Any],
        lane: str,
        phase: str,
        prompt_characteristics: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        base_policy, source_chain = self._load_base_policy()
        result = _resolve_model_lane_config(
            metadata=metadata,
            instruction_contract=instruction_contract,
            base_policy=base_policy,
            lane=lane,
            phase=phase,
            prompt_characteristics=prompt_characteristics,
        )
        result["source_chain"] = source_chain
        result["config_path"] = self._config_path
        return result

    def explain(
        self,
        *,
        metadata: dict[str, Any],
        instruction_contract: dict[str, Any],
        lane: str,
        phase: str,
        prompt_characteristics: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        resolved = self.resolve(
            metadata=metadata,
            instruction_contract=instruction_contract,
            lane=lane,
            phase=phase,
            prompt_characteristics=prompt_characteristics,
        )
        return {
            "lane": resolved["lane"],
            "requested_lane": resolved["requested_lane"],
            "provider": resolved["provider"],
            "model": resolved["model"],
            "role": resolved["role"],
            "persona": resolved["persona"],
            "selection_reason": resolved["selection_reason"],
            "source_chain": resolved["source_chain"],
            "config_path": resolved["config_path"],
        }

    def resolve_pool(
        self,
        *,
        metadata: dict[str, Any],
        instruction_contract: dict[str, Any],
        lane: str,
        phase: str,
        prompt_characteristics: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        base_policy, source_chain = self._load_base_policy()
        result = _resolve_model_lane_config(
            metadata=metadata,
            instruction_contract=instruction_contract,
            base_policy=base_policy,
            lane=lane,
            phase=phase,
            prompt_characteristics=prompt_characteristics,
        )
        result["source_chain"] = source_chain
        result["config_path"] = self._config_path
        return result


def _resolve_model_lane_config(
    *,
    metadata: dict[str, Any],
    instruction_contract: dict[str, Any],
    base_policy: dict[str, Any] | None = None,
    lane: str,
    phase: str,
    prompt_characteristics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    _ = (metadata, instruction_contract)
    policy = dict(base_policy or {})
    routing = dict(policy.get("routing") or {}) if isinstance(policy.get("routing"), dict) else {}
    personas = dict(policy.get("personas") or {}) if isinstance(policy.get("personas"), dict) else {}
    roles = dict(policy.get("roles") or {}) if isinstance(policy.get("roles"), dict) else {}
    yaml_policy = policy.get("policy")
    requested_name = _normalize_text(lane) or _default_lane_name(phase=phase)
    prompt_characteristics = _coerce_prompt_characteristics(prompt_characteristics)
    selected_name, routed_persona = _resolve_route_name(
        selected_name=requested_name,
        routing=routing,
        prompt_characteristics=prompt_characteristics,
    )
    route = routing.get(selected_name)
    if route is None:
        raise ModelRoutingConfigLoadError(
            f"MODEL_LANE_POLICY_UNRESOLVED lane={selected_name or requested_name} phase={phase}"
        )
    role_name = route.role
    if (
        selected_name in {"planning", "planning_safe", "structured_logician"}
        and _normalize_lower(role_name) != "planner"
    ):
        raise ModelRoutingConfigLoadError(
            f"MODEL_LANE_POLICY_INVALID_PLANNER_ROLE lane={selected_name or requested_name} role={role_name}"
        )
    role = roles.get(role_name)
    persona_name = routed_persona or route.default_persona or (role.default_persona if role else None)
    if not persona_name:
        raise ModelRoutingConfigLoadError(
            f"MODEL_LANE_POLICY_UNRESOLVED_PERSONA lane={selected_name or requested_name} phase={phase}"
        )
    persona = personas.get(persona_name)
    if persona is None or not getattr(persona, "candidates", None):
        raise ModelRoutingConfigLoadError(
            f"MODEL_LANE_POLICY_UNRESOLVED_PERSONA lane={selected_name or requested_name} persona={persona_name}"
        )
    persona_purpose = _normalize_lower(getattr(persona, "purpose", ""))
    if selected_name in {"planning", "planning_safe", "structured_logician"} and persona_purpose != "planner":
        raise ModelRoutingConfigLoadError(
            f"MODEL_LANE_POLICY_INVALID_PLANNER_PERSONA lane={selected_name or requested_name} persona={persona_name}"
        )
    candidate = sorted(persona.candidates, key=lambda item: int(item.priority), reverse=True)[0]
    sorted_candidates = sorted(persona.candidates, key=lambda item: int(item.priority), reverse=True)
    provider = _normalize_lower(candidate.provider)
    model_text = _canonicalize_provider_model(provider=provider, model_text=_normalize_text(candidate.model))
    if not provider or not model_text:
        raise ModelRoutingConfigLoadError(
            f"MODEL_LANE_POLICY_UNRESOLVED_BINDING lane={selected_name or requested_name} persona={persona_name}"
        )
    cost_tier = _infer_cost_tier(cost_tier=candidate.cost_tier, model_text=model_text)
    selection_reason = f"{phase}_{persona_name}"
    fallback_lane = None
    if len(persona.candidates) > 1:
        fallback_lane = selected_name
    return {
        "requested_lane": requested_name,
        "lane": selected_name,
        "role": role_name,
        "persona": persona_name,
        "candidate_pool": [
            {
                "provider": _normalize_lower(item.provider),
                "model": _canonicalize_provider_model(
                    provider=_normalize_lower(item.provider),
                    model_text=_normalize_text(item.model),
                ),
                "priority": int(item.priority),
                "cost_tier": item.cost_tier,
                "timeout_seconds": item.timeout_seconds,
                "max_retries": item.max_retries,
                "retry_backoff_ms": item.retry_backoff_ms,
                "temperature": item.temperature,
                "max_steps": item.max_steps,
                "circuit_breaker_window_seconds": getattr(item, "circuit_breaker_window_seconds", None),
                "circuit_breaker_failure_threshold": getattr(item, "circuit_breaker_failure_threshold", None),
                "tags": list(item.tags or []),
            }
            for item in sorted_candidates
        ],
        "cost_tier": cost_tier,
        "selection_reason": selection_reason,
        "provider": provider,
        "model": model_text or None,
        "temperature": candidate.temperature,
        "max_steps": candidate.max_steps,
        "timeout_seconds": candidate.timeout_seconds,
        "max_retries": candidate.max_retries,
        "retry_backoff_ms": candidate.retry_backoff_ms,
        "fallback_lane": fallback_lane,
        "policy_present": bool(yaml_policy),
        "capability_profile": None,
        "provider_model_capability_present": False,
        "routed_from_lane": requested_name if persona_name != route.default_persona else None,
    }


_DEFAULT_SERVICE = ModelLanePolicyService()


def resolve_model_lane_config(
    *,
    metadata: dict[str, Any],
    instruction_contract: dict[str, Any],
    lane: str,
    phase: str,
    prompt_characteristics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return _DEFAULT_SERVICE.resolve(
        metadata=metadata,
        instruction_contract=instruction_contract,
        lane=lane,
        phase=phase,
        prompt_characteristics=prompt_characteristics,
    )


__all__ = ["ModelLanePolicyService", "resolve_model_lane_config"]
