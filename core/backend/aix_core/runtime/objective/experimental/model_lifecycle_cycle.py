"""001D-W06: Model lifecycle for objective-cycle attempts (pin/deprecate/health/fallback eligibility)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal


def _utc() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


ModelHealth = Literal["healthy", "degraded", "unhealthy", "deprecated"]


@dataclass(frozen=True)
class ModelRecordV1:
    model_ref: str
    version_ref: str
    health: ModelHealth = "healthy"
    fallback_eligible: bool = False
    rollback_version_ref: str | None = None


@dataclass(frozen=True)
class ModelPinDecisionV1:
    allowed: bool
    model_ref: str
    version_ref: str
    reason_codes: tuple[str, ...]
    pinned_at: str = field(default_factory=_utc)


class CycleModelLifecycle:
    def __init__(self) -> None:
        self._models: dict[str, ModelRecordV1] = {}
        self._pins: dict[str, ModelPinDecisionV1] = {}  # attempt_id -> pin

    def upsert(self, record: ModelRecordV1) -> None:
        self._models[record.model_ref] = record

    def pin_for_attempt(
        self,
        *,
        attempt_id: str,
        model_ref: str,
        allow_fallback: bool = False,
        requested_fallback_ref: str | None = None,
    ) -> ModelPinDecisionV1:
        if requested_fallback_ref and not allow_fallback:
            return ModelPinDecisionV1(
                allowed=False,
                model_ref=model_ref,
                version_ref="",
                reason_codes=("SILENT_FALLBACK_FORBIDDEN",),
            )
        rec = self._models.get(model_ref)
        if rec is None:
            return ModelPinDecisionV1(
                allowed=False,
                model_ref=model_ref,
                version_ref="",
                reason_codes=("MODEL_UNKNOWN",),
            )
        if rec.health == "deprecated":
            return ModelPinDecisionV1(
                allowed=False,
                model_ref=model_ref,
                version_ref=rec.version_ref,
                reason_codes=("MODEL_DEPRECATED",),
            )
        if rec.health == "unhealthy":
            if allow_fallback and rec.fallback_eligible and requested_fallback_ref:
                fb = self._models.get(requested_fallback_ref)
                if fb and fb.health == "healthy":
                    decision = ModelPinDecisionV1(
                        allowed=True,
                        model_ref=fb.model_ref,
                        version_ref=fb.version_ref,
                        reason_codes=("EXPLICIT_FALLBACK_PINNED",),
                    )
                    self._pins[attempt_id] = decision
                    return decision
            return ModelPinDecisionV1(
                allowed=False,
                model_ref=model_ref,
                version_ref=rec.version_ref,
                reason_codes=("MODEL_UNHEALTHY",),
            )
        decision = ModelPinDecisionV1(
            allowed=True,
            model_ref=rec.model_ref,
            version_ref=rec.version_ref,
            reason_codes=("MODEL_PINNED",),
        )
        self._pins[attempt_id] = decision
        return decision

    def get_pin(self, attempt_id: str) -> ModelPinDecisionV1 | None:
        return self._pins.get(attempt_id)

    def rollback_pin(self, attempt_id: str) -> ModelPinDecisionV1:
        pin = self._pins.get(attempt_id)
        if pin is None:
            return ModelPinDecisionV1(False, "", "", ("PIN_MISSING",))
        rec = self._models.get(pin.model_ref)
        if not rec or not rec.rollback_version_ref:
            return ModelPinDecisionV1(False, pin.model_ref, pin.version_ref, ("ROLLBACK_VERSION_MISSING",))
        decision = ModelPinDecisionV1(
            allowed=True,
            model_ref=pin.model_ref,
            version_ref=rec.rollback_version_ref,
            reason_codes=("MODEL_ROLLED_BACK",),
        )
        self._pins[attempt_id] = decision
        return decision
