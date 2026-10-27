"""F1-P2: WorkType taxonomy covers SUPPORTED_TASK_CLASSES (F1-AC-02)."""

from __future__ import annotations

import pytest

from backend.aix_core.framework.work.worktype import WorkTypeCatalog, WorkTypeTaxonomy
from backend.aix_core.runtime.governance.policy import SUPPORTED_TASK_CLASSES


def test_taxonomy_covers_all_supported_task_classes() -> None:
    assert WorkTypeTaxonomy.covers_all_supported_classes()
    catalog = {wt.task_class for wt in WorkTypeCatalog.all_work_types()}
    assert catalog == set(SUPPORTED_TASK_CLASSES)


@pytest.mark.parametrize("task_class", sorted(SUPPORTED_TASK_CLASSES))
def test_round_trip_preserves_governance_flags(task_class: str) -> None:
    assert WorkTypeTaxonomy.round_trip_preserves_governance(task_class)


def test_from_product_class_maps_known_labels() -> None:
    wt = WorkTypeTaxonomy.from_product_class("verification")
    assert wt.task_class == "verification_only"
    assert wt.product_class == "verification"


def test_worktype_static_factories() -> None:
    from backend.aix_core.framework.work.models import WorkType

    assert WorkType.from_task_class("docs_only_change").task_class == "docs_only_change"
    assert WorkType.from_product_class("docs").task_class == "docs_only_change"


def test_unsupported_task_class_fails_closed() -> None:
    with pytest.raises(ValueError, match="unsupported"):
        WorkTypeTaxonomy.from_task_class("not_a_real_class")
