"""F1-P2: WorkType taxonomy over governance SUPPORTED_TASK_CLASSES.

Does not mutate `policy.py` enforcement — read-only catalog + factories.
"""

from __future__ import annotations

from typing import Mapping

from backend.aix_core.framework.work.models import WorkType
from backend.aix_core.runtime.governance.policy import (
    SUPPORTED_TASK_CLASSES,
    TaskClassPolicy,
    _ALLOWED_EFFECTS_BY_TASK_CLASS,
    _FORBIDDEN_EFFECTS,
)

# Product-class labels used by OutcomeContract / admission product routing.
_PRODUCT_CLASS_TO_TASK_CLASS: Mapping[str, str] = {
    "software": "source_edit",
    "docs": "docs_only_change",
    "tests": "test_generation",
    "verification": "verification_only",
    "fixture": "test_fixture_change",
    "helper": "single_file_helper",
}

_TASK_FAMILY_BY_CLASS: Mapping[str, str] = {
    "docs_only_change": "docs",
    "test_fixture_change": "test",
    "single_file_helper": "repair",
    "source_edit": "mutation",
    "test_generation": "test",
    "verification_only": "verification",
}


class WorkTypeCatalog:
    """Frozen catalog covering 100% of SUPPORTED_TASK_CLASSES."""

    @staticmethod
    def supported_task_classes() -> frozenset[str]:
        return frozenset(SUPPORTED_TASK_CLASSES)

    @staticmethod
    def allowed_effects_for(task_class: str) -> tuple[str, ...]:
        key = str(task_class or "").strip().lower()
        effects = _ALLOWED_EFFECTS_BY_TASK_CLASS.get(key, frozenset())
        return tuple(sorted(effects))

    @staticmethod
    def forbidden_effects() -> tuple[str, ...]:
        return tuple(sorted(_FORBIDDEN_EFFECTS))

    @classmethod
    def all_work_types(cls) -> tuple[WorkType, ...]:
        return tuple(WorkTypeTaxonomy.from_task_class(tc) for tc in sorted(SUPPORTED_TASK_CLASSES))


class WorkTypeTaxonomy:
    """Factories + round-trip helpers for governance task classes."""

    @staticmethod
    def from_task_class(
        task_class: str,
        *,
        product_class: str = "software",
        environment_scope: str = "development",
        approval_policy: str = "INITIAL_PLAN_ONLY",
    ) -> WorkType:
        normalized = str(task_class or "").strip().lower()
        if normalized not in SUPPORTED_TASK_CLASSES:
            raise ValueError(f"unsupported task_class: {task_class!r}")
        return WorkType(
            task_class=normalized,
            task_family=_TASK_FAMILY_BY_CLASS.get(normalized, "general"),
            product_class=str(product_class or "software").strip().lower() or "software",
            allowed_effects=WorkTypeCatalog.allowed_effects_for(normalized),
            forbidden_effects=WorkTypeCatalog.forbidden_effects(),
            environment_scope=environment_scope,  # type: ignore[arg-type]
            approval_policy=approval_policy,
        )

    @staticmethod
    def from_product_class(
        product_class: str,
        *,
        environment_scope: str = "development",
        approval_policy: str = "INITIAL_PLAN_ONLY",
    ) -> WorkType:
        key = str(product_class or "").strip().lower() or "software"
        task_class = _PRODUCT_CLASS_TO_TASK_CLASS.get(key, "source_edit")
        return WorkTypeTaxonomy.from_task_class(
            task_class,
            product_class=key,
            environment_scope=environment_scope,
            approval_policy=approval_policy,
        )

    @staticmethod
    def covers_all_supported_classes() -> bool:
        catalog = {wt.task_class for wt in WorkTypeCatalog.all_work_types()}
        return catalog == set(SUPPORTED_TASK_CLASSES)

    @staticmethod
    def round_trip_preserves_governance(task_class: str) -> bool:
        """F1-AC-02: taxonomy WorkType effects match TaskClassPolicy defaults."""
        wt = WorkTypeTaxonomy.from_task_class(task_class)
        decision = TaskClassPolicy().evaluate(
            task_class=wt.task_class,
            requested_effects=wt.allowed_effects,
            environment=wt.environment_scope,
        )
        return (
            decision.task_class == wt.task_class
            and set(decision.allowed_effects) == set(wt.allowed_effects)
            and decision.disposition.value == "ADMIT"
        )


# PACKET-CODE-MAP aliases expected on WorkType namespace.
def _worktype_from_task_class(task_class: str, **kwargs: object) -> WorkType:
    return WorkTypeTaxonomy.from_task_class(task_class, **kwargs)  # type: ignore[arg-type]


def _worktype_from_product_class(product_class: str, **kwargs: object) -> WorkType:
    return WorkTypeTaxonomy.from_product_class(product_class, **kwargs)  # type: ignore[arg-type]


WorkType.from_task_class = staticmethod(_worktype_from_task_class)  # type: ignore[attr-defined]
WorkType.from_product_class = staticmethod(_worktype_from_product_class)  # type: ignore[attr-defined]


__all__ = [
    "WorkTypeCatalog",
    "WorkTypeTaxonomy",
]
