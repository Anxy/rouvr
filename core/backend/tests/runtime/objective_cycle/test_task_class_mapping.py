"""AIX Closed-Loop Execution Plan, Sprint W1: task_family.py <-> policy.SUPPORTED_TASK_CLASSES
mapping tests. Verifies the mapping stays conservative (only onto already-supported classes)
and that deliberately unmapped families resolve to None, not a guess."""

from __future__ import annotations

import pytest

from backend.aix_core.runtime.governance.policy import SUPPORTED_TASK_CLASSES, TaskClassPolicy
from backend.aix_core.runtime.objective.task_class_mapping import (
    UNMAPPED_FAMILIES,
    task_family_to_task_class,
)
from backend.aix_core.runtime.objective.task_family import TASK_FAMILIES


def test_every_task_family_is_either_mapped_or_explicitly_unmapped() -> None:
    for family in TASK_FAMILIES:
        mapped = task_family_to_task_class(family)
        assert (mapped is not None) != (family in UNMAPPED_FAMILIES)


@pytest.mark.parametrize(
    "family,expected_class",
    [
        ("EXPLORE", "verification_only"),
        ("READ", "verification_only"),
        ("SEARCH", "verification_only"),
        ("ANALYZE", "verification_only"),
        ("EXPLAIN", "verification_only"),
        ("REVIEW", "verification_only"),
        ("EDIT", "single_file_helper"),
        ("REFACTOR", "single_file_helper"),
        ("TEST", "test_generation"),
    ],
)
def test_mapped_families_resolve_to_expected_class(family: str, expected_class: str) -> None:
    assert task_family_to_task_class(family) == expected_class


@pytest.mark.parametrize("family", ["CREATE", "DELETE", "COMMAND", "DEBUG", "UNKNOWN"])
def test_deliberately_unmapped_families_return_none_not_a_guess(family: str) -> None:
    assert task_family_to_task_class(family) is None
    assert family in UNMAPPED_FAMILIES


def test_none_and_unrecognized_input_return_none() -> None:
    assert task_family_to_task_class(None) is None
    assert task_family_to_task_class("NOT_A_REAL_FAMILY") is None


def test_mapping_is_case_insensitive() -> None:
    assert task_family_to_task_class("edit") == task_family_to_task_class("EDIT")


def test_every_mapped_class_is_policy_supported() -> None:
    for family in TASK_FAMILIES:
        mapped = task_family_to_task_class(family)
        if mapped is not None:
            assert mapped in SUPPORTED_TASK_CLASSES


def test_mapped_output_round_trips_through_task_class_policy() -> None:
    """The whole point of the mapping: policy.evaluate() must not CLARIFY/REJECT on
    UNSUPPORTED_TASK_CLASS for any family this module claims to map."""
    policy = TaskClassPolicy()
    for family in TASK_FAMILIES:
        mapped = task_family_to_task_class(family)
        if mapped is None:
            continue
        decision = policy.evaluate(task_class=mapped, requested_effects=())
        assert "UNSUPPORTED_TASK_CLASS" not in decision.reason_codes
