"""001N-W05: Feature flags and staged rollout — fail-closed defaults, no safety bypass."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

RolloutStage = Literal["off", "canary", "staged", "on"]


@dataclass(frozen=True)
class FeatureFlagV1:
    key: str
    stage: RolloutStage
    safety_critical: bool
    allow_bypass: bool = False


DEFAULT_FLAGS: tuple[FeatureFlagV1, ...] = (
    FeatureFlagV1("objective_cycle_workflow", "canary", True, False),
    FeatureFlagV1("direct_provider_route", "off", True, False),
    FeatureFlagV1("auto_accept_review", "off", True, False),
    FeatureFlagV1("cycle_ui_controls", "staged", False, False),
    FeatureFlagV1("memurai_streams_publish", "canary", True, False),
    FeatureFlagV1("intelligence_layer_v1", "on", False, False),
)


class FeatureFlagError(ValueError):
    pass


class FeatureFlagRegistry:
    def __init__(self, flags: tuple[FeatureFlagV1, ...] = DEFAULT_FLAGS) -> None:
        self._flags = {f.key: f for f in flags}

    def is_enabled(self, key: str, *, stage_now: RolloutStage = "canary") -> bool:
        flag = self._flags.get(key)
        if flag is None:
            # Fail closed for unknown flags
            return False
        if flag.allow_bypass:
            raise FeatureFlagError("SAFETY_BYPASS_FORBIDDEN")
        order = {"off": 0, "canary": 1, "staged": 2, "on": 3}
        return order[flag.stage] > 0 and order[stage_now] >= order[flag.stage]

    def promote(self, key: str, to_stage: RolloutStage) -> FeatureFlagV1:
        flag = self._flags[key]
        if flag.safety_critical and to_stage == "on" and flag.key in {"direct_provider_route", "auto_accept_review"}:
            raise FeatureFlagError("SAFETY_CRITICAL_CANNOT_ENABLE")
        updated = FeatureFlagV1(flag.key, to_stage, flag.safety_critical, False)
        self._flags[key] = updated
        return updated

    def rollback(self, key: str) -> FeatureFlagV1:
        flag = self._flags[key]
        updated = FeatureFlagV1(flag.key, "off", flag.safety_critical, False)
        self._flags[key] = updated
        return updated
