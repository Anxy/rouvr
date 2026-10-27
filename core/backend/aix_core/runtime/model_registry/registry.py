"""AIX Model Registry — config-driven routing (models.json)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from backend.aix_core.runtime.model_registry.model_profile import ModelProfileV1
from backend.aix_core.runtime.model_registry.role_policy import (
    AgentRoleKind,
    ComplexityKind,
    RiskKind,
    RoleModelPolicyV1,
)

_DEFAULT_CONFIG = Path(__file__).resolve().parent / "models.json"


@dataclass(frozen=True)
class ModelRoute:
    model_id: str
    family: str
    attempt: int
    role: str
    fallback_used: bool = False
    model_ref: str = ""

    def __post_init__(self) -> None:
        if not self.model_ref:
            object.__setattr__(self, "model_ref", f"openrouter/{self.model_id}")


class ModelRegistry:
    """Loads profiles and role policies from models.json — no hardcoded model IDs."""

    def __init__(
        self,
        *,
        profiles: dict[str, ModelProfileV1],
        policies: dict[str, RoleModelPolicyV1],
    ) -> None:
        self._profiles = profiles
        self._policies = policies

    @classmethod
    def from_config(cls, path: Path | str | None = None) -> ModelRegistry:
        config_path = Path(path) if path is not None else _default_config_path()
        raw = json.loads(config_path.read_text(encoding="utf-8"))
        profiles: dict[str, ModelProfileV1] = {}
        for item in raw.get("profiles", []):
            profile = ModelProfileV1.model_validate(item)
            profiles[profile.model_id] = profile
        policies: dict[str, RoleModelPolicyV1] = {}
        for item in raw.get("policies", []):
            pol = RoleModelPolicyV1.model_validate(
                {
                    **item,
                    "fallback_model_ids": tuple(item.get("fallback_model_ids") or ()),
                    "require_different_family_from": tuple(
                        item.get("require_different_family_from") or ()
                    ),
                }
            )
            policies[pol.role] = pol
        return cls(profiles=profiles, policies=policies)

    def get_profile(self, model_id: str) -> ModelProfileV1 | None:
        return self._profiles.get(model_id)

    def get_policy(self, role: AgentRoleKind) -> RoleModelPolicyV1 | None:
        return self._policies.get(role)

    def register_profile(self, profile: ModelProfileV1) -> None:
        self._profiles[profile.model_id] = profile

    def register_policy(self, policy: RoleModelPolicyV1) -> None:
        self._policies[policy.role] = policy

    def select_role(
        self,
        *,
        complexity: ComplexityKind = "NORMAL",
        risk: RiskKind = "NORMAL",
        task_kind: str | None = None,
    ) -> AgentRoleKind:
        """Map complexity/risk to a routing role key."""
        if risk == "ESCALATION" or risk == "HIGH":
            return "critical_reviewer"
        if task_kind in {"planning", "replan", "repair_planning"}:
            return "planner"
        if task_kind in {"verification", "verify"}:
            return "verifier"
        if task_kind in {"testing", "test"}:
            return "tester"
        if task_kind in {"objective_understanding", "understanding"}:
            return "objective_understanding"
        if complexity == "LOW":
            return "bulk_executor"
        if complexity == "HIGH":
            return "complex_planner"
        return "executor"

    def get_route(
        self,
        role: AgentRoleKind | str,
        *,
        complexity: ComplexityKind = "NORMAL",
        risk: RiskKind = "NORMAL",
        retry_history: tuple[str, ...] | list[str] = (),
        cost_consumed: float = 0.0,
        repair_attempt_count: int = 0,
        escalated: bool = False,
    ) -> ModelRoute:
        """Resolve model for role with fallback chain and escalation rules."""
        role_key = self._resolve_effective_role(
            str(role),
            complexity=complexity,
            risk=risk,
            repair_attempt_count=repair_attempt_count,
            escalated=escalated,
        )
        policy = self._policies.get(role_key)
        if policy is None:
            raise ValueError(f"MODEL_POLICY_MISSING:{role_key}")

        if policy.escalation_only and not escalated and repair_attempt_count < 2 and risk != "ESCALATION":
            if role_key == "complex_planner":
                role_key = "planner"
                policy = self._policies.get("planner")
            elif role_key == "critical_reviewer":
                role_key = "verifier"
                policy = self._policies.get("verifier")
            if policy is None:
                raise ValueError(f"MODEL_POLICY_MISSING:{role_key}")

        if cost_consumed >= policy.cost_budget_usd and policy.fallback_model_ids:
            model_id = policy.fallback_model_ids[0]
            return self._build_route(role_key, model_id, attempt=len(retry_history) + 1, fallback_used=True)

        attempt = len(retry_history) + 1
        if attempt == 1 or not retry_history:
            model_id = policy.primary_model_id
            return self._build_route(role_key, model_id, attempt=attempt, fallback_used=False)

        prior = retry_history[-1]
        if prior == policy.primary_model_id and policy.fallback_model_ids:
            idx = min(attempt - 2, len(policy.fallback_model_ids) - 1)
            model_id = policy.fallback_model_ids[idx]
            return self._build_route(role_key, model_id, attempt=attempt, fallback_used=True)

        if attempt <= policy.max_attempts:
            return self._build_route(role_key, policy.primary_model_id, attempt=attempt, fallback_used=False)

        if policy.fallback_model_ids:
            model_id = policy.fallback_model_ids[-1]
            return self._build_route(role_key, model_id, attempt=attempt, fallback_used=True)

        return self._build_route(role_key, policy.primary_model_id, attempt=attempt, fallback_used=False)

    def resolve_model_for_role(self, role: AgentRoleKind | str) -> str:
        """Backward-compatible helper."""
        return self.get_route(str(role)).model_id

    def model_ref_for_role(self, role: AgentRoleKind | str, **kwargs: Any) -> str:
        return self.get_route(str(role), **kwargs).model_ref

    def _resolve_effective_role(
        self,
        role: str,
        *,
        complexity: ComplexityKind,
        risk: RiskKind,
        repair_attempt_count: int,
        escalated: bool,
    ) -> AgentRoleKind:
        if repair_attempt_count >= 2 or escalated or risk == "ESCALATION":
            if role in {"planner", "complex_planner", "verifier", "critical_reviewer"}:
                return "critical_reviewer"
        if role == "planner" and (complexity == "HIGH" or risk == "HIGH"):
            return "complex_planner"
        if role == "executor" and complexity == "LOW":
            return "bulk_executor"
        return role  # type: ignore[return-value]

    def _build_route(
        self,
        role: str,
        model_id: str,
        *,
        attempt: int,
        fallback_used: bool,
    ) -> ModelRoute:
        profile = self._profiles.get(model_id)
        if profile is None:
            raise ValueError(f"MODEL_PROFILE_MISSING:{model_id}")
        return ModelRoute(
            model_id=model_id,
            family=profile.family,
            attempt=attempt,
            role=role,
            fallback_used=fallback_used,
            model_ref=f"openrouter/{model_id}",
        )


def _default_config_path() -> Path:
    env_path = str(os.getenv("AIX_MODEL_REGISTRY_CONFIG") or "").strip()
    if env_path:
        return Path(env_path)
    return _DEFAULT_CONFIG


@lru_cache(maxsize=1)
def get_model_registry() -> ModelRegistry:
    return ModelRegistry.from_config()


__all__ = [
    "ModelRoute",
    "ModelRegistry",
    "get_model_registry",
]
