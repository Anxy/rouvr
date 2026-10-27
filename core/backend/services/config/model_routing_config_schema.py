from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MODEL_ROUTING_CONFIG_SCHEMA_VERSION_V2 = "aix.model_routing_config.v2"
SUPPORTED_MODEL_ROUTING_CONFIG_VERSIONS = {MODEL_ROUTING_CONFIG_SCHEMA_VERSION_V2}


class ModelRoutingCandidateV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    model: str
    priority: int = 100
    cost_tier: str | None = None
    timeout_seconds: int | None = None
    max_retries: int | None = None
    retry_backoff_ms: int | None = None
    temperature: float | None = None
    max_steps: int | None = None
    supports_json_object: bool | None = None
    circuit_breaker_window_seconds: int | None = None
    circuit_breaker_failure_threshold: int | None = None
    tags: list[str] = Field(default_factory=list)

    @field_validator("provider")
    @classmethod
    def _normalize_provider(cls, value: str) -> str:
        text = str(value or "").strip().lower()
        if not text:
            raise ValueError("candidate provider is required")
        return text

    @field_validator("model")
    @classmethod
    def _validate_model_present(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("candidate model is required")
        return text

    @model_validator(mode="after")
    def _normalize_provider_model(self) -> ModelRoutingCandidateV2:
        if self.provider == "openrouter" and not self.model.lower().startswith("openrouter/"):
            self.model = f"openrouter/{self.model}"
        return self


class ModelRoutingPersonaV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    purpose: str
    selection_mode: Literal["ordered_fallback"] = "ordered_fallback"
    retry_budget: dict[str, int] = Field(default_factory=dict)
    candidates: list[ModelRoutingCandidateV2]

    @model_validator(mode="after")
    def _ensure_candidates(self) -> ModelRoutingPersonaV2:
        if not self.candidates:
            raise ValueError("persona must define at least one candidate")
        return self


class ModelRoutingRoleV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_persona: str


class ModelRoutingConditionRuleV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    when: dict[str, object] = Field(default_factory=dict)
    persona: str


class ModelRoutingRouteV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: str
    default_persona: str | None = None
    conditions: list[ModelRoutingConditionRuleV2] = Field(default_factory=list)


class ModelRoutingPolicyV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    yaml_only: bool = True
    allow_metadata_override: bool = False
    allow_instruction_override: bool = False
    allow_repo_profile_override: bool = False
    allow_builtin_fallbacks: bool = False


class ModelRoutingConfigV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str
    config_id: str
    version: int
    roles: dict[str, ModelRoutingRoleV2]
    personas: dict[str, ModelRoutingPersonaV2]
    routing: dict[str, ModelRoutingRouteV2]
    policy: ModelRoutingPolicyV2
    metadata: dict[str, object] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _ensure_cross_references(self) -> ModelRoutingConfigV2:
        persona_names = set(self.personas.keys())
        role_names = set(self.roles.keys())

        for role_name, role in self.roles.items():
            if role.default_persona not in persona_names:
                raise ValueError(f"role '{role_name}' default_persona '{role.default_persona}' is undefined")

        for route_name, route in self.routing.items():
            if route.role not in role_names:
                raise ValueError(f"routing '{route_name}' references undefined role '{route.role}'")
            if route.default_persona and route.default_persona not in persona_names:
                raise ValueError(f"routing '{route_name}' default_persona '{route.default_persona}' is undefined")
            for condition in route.conditions:
                if condition.persona not in persona_names:
                    raise ValueError(f"routing '{route_name}' condition persona '{condition.persona}' is undefined")
        return self


def get_model_routing_config_schema_v2() -> dict[str, object]:
    return ModelRoutingConfigV2.model_json_schema()
