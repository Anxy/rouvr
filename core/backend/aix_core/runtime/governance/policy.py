"""Versioned, fail-closed risk and task-class policy for objective admission."""

from __future__ import annotations

from enum import StrEnum
from typing import Iterable

from pydantic import BaseModel, ConfigDict, Field

POLICY_VERSION = "aix.objective-cycle.task-risk.v1"

SUPPORTED_TASK_CLASSES = frozenset(
    {
        "docs_only_change",
        "test_fixture_change",
        "single_file_helper",
        "source_edit",
        "test_generation",
        "verification_only",
    }
)

_MUTATING_TASK_CLASSES = frozenset(
    {
        "docs_only_change",
        "test_fixture_change",
        "single_file_helper",
        "source_edit",
        "test_generation",
    }
)

_FORBIDDEN_EFFECTS = frozenset(
    {
        "auto_accept",
        "auto_close",
        "auto_create_workhub_task",
        "direct_provider_route",
        "production_deploy",
        "production_mutation",
        "workhub_lifecycle_mutation",
    }
)

_ALLOWED_EFFECTS_BY_TASK_CLASS: dict[str, frozenset[str]] = {
    "docs_only_change": frozenset(
        {"attach_evidence", "read_source", "run_validation", "write_scoped_docs"}
    ),
    "test_fixture_change": frozenset(
        {"attach_evidence", "read_source", "run_validation", "write_scoped_test_fixture"}
    ),
    "single_file_helper": frozenset(
        {"attach_evidence", "read_source", "run_validation", "write_scoped_source"}
    ),
    "source_edit": frozenset(
        {"attach_evidence", "read_source", "run_validation", "write_scoped_source"}
    ),
    "test_generation": frozenset(
        {"attach_evidence", "read_source", "run_validation", "write_scoped_tests"}
    ),
    "verification_only": frozenset(
        {"attach_evidence", "read_source", "run_validation"}
    ),
}


class PolicyDisposition(StrEnum):
    ADMIT = "ADMIT"
    REJECT = "REJECT"
    CLARIFY = "CLARIFY"


class RiskTier(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    PROHIBITED = "PROHIBITED"


class PolicyDecision(BaseModel):
    """Auditable policy output consumed by admission and planning."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_version: str = POLICY_VERSION
    disposition: PolicyDisposition
    task_class: str | None = None
    risk_tier: RiskTier
    reason_codes: tuple[str, ...] = Field(min_length=1)
    requires_human_approval: bool
    allowed_effects: tuple[str, ...] = ()


def _normalized(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted({str(value).strip().lower() for value in values if str(value).strip()}))


class TaskClassPolicy:
    """Evaluate objective scope without granting lifecycle or provider authority."""

    def __init__(
        self,
        *,
        version: str = POLICY_VERSION,
        supported_task_classes: Iterable[str] = SUPPORTED_TASK_CLASSES,
    ) -> None:
        self.version = str(version).strip()
        if not self.version:
            raise ValueError("policy version is required")
        self.supported_task_classes = frozenset(_normalized(supported_task_classes))
        if not self.supported_task_classes:
            raise ValueError("at least one supported task class is required")

    def evaluate(
        self,
        *,
        task_class: str | None,
        requested_effects: Iterable[str] = (),
        environment: str = "development",
    ) -> PolicyDecision:
        normalized_class = str(task_class or "").strip().lower()
        effects = _normalized(requested_effects)
        normalized_environment = str(environment or "").strip().lower()

        if not normalized_class:
            return self._decision(
                disposition=PolicyDisposition.CLARIFY,
                task_class=None,
                risk_tier=RiskTier.PROHIBITED,
                reason_codes=("TASK_CLASS_REQUIRED",),
            )

        if normalized_class not in self.supported_task_classes:
            return self._decision(
                disposition=PolicyDisposition.REJECT,
                task_class=normalized_class,
                risk_tier=RiskTier.PROHIBITED,
                reason_codes=("UNSUPPORTED_TASK_CLASS",),
            )

        forbidden = tuple(sorted(set(effects) & _FORBIDDEN_EFFECTS))
        if forbidden:
            return self._decision(
                disposition=PolicyDisposition.REJECT,
                task_class=normalized_class,
                risk_tier=RiskTier.PROHIBITED,
                reason_codes=tuple(f"FORBIDDEN_EFFECT:{effect}" for effect in forbidden),
            )

        allowed_for_class = _ALLOWED_EFFECTS_BY_TASK_CLASS.get(normalized_class, frozenset())
        unrecognized = tuple(sorted(set(effects) - allowed_for_class))
        if unrecognized:
            return self._decision(
                disposition=PolicyDisposition.REJECT,
                task_class=normalized_class,
                risk_tier=RiskTier.PROHIBITED,
                reason_codes=tuple(f"EFFECT_NOT_ALLOWED:{effect}" for effect in unrecognized),
            )

        if normalized_environment in {"prod", "production"} and normalized_class in _MUTATING_TASK_CLASSES:
            return self._decision(
                disposition=PolicyDisposition.REJECT,
                task_class=normalized_class,
                risk_tier=RiskTier.PROHIBITED,
                reason_codes=("PRODUCTION_MUTATION_NOT_AUTHORIZED",),
            )

        is_mutating = normalized_class in _MUTATING_TASK_CLASSES
        return self._decision(
            disposition=PolicyDisposition.ADMIT,
            task_class=normalized_class,
            risk_tier=RiskTier.MEDIUM if is_mutating else RiskTier.LOW,
            reason_codes=("SUPPORTED_RESTRICTED_TASK_CLASS",),
            requires_human_approval=is_mutating,
            allowed_effects=effects,
        )

    def _decision(
        self,
        *,
        disposition: PolicyDisposition,
        task_class: str | None,
        risk_tier: RiskTier,
        reason_codes: tuple[str, ...],
        requires_human_approval: bool = False,
        allowed_effects: tuple[str, ...] = (),
    ) -> PolicyDecision:
        return PolicyDecision(
            policy_version=self.version,
            disposition=disposition,
            task_class=task_class,
            risk_tier=risk_tier,
            reason_codes=reason_codes,
            requires_human_approval=requires_human_approval,
            allowed_effects=allowed_effects,
        )
