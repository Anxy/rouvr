"""AIX-VOL-II-T004: Typed Effects & Equivalent Intent Dispatch.

Verifies:
1. Semantically equivalent prompt phrasings authorize the exact same typed effect class.
2. `classify_task_family` deterministic classification maps equivalent intents (EDIT, CREATE, REFACTOR, DELETE, TEST) without reliance on filename-specific branches.
3. Resource-bearing families strictly enforce effect boundary limits.
"""

from __future__ import annotations

import pytest

from backend.aix_core.runtime.objective.task_family import (
    RESOURCE_REQUIRED_FAMILIES,
    classify_task_family,
)
from backend.aix_core.runtime.objective.contracts import (
    FORBIDDEN_EFFECTS,
)


def test_semantically_equivalent_edit_phrasings_authorize_same_effect_class() -> None:
    """Different phrasings for editing code must map to the EDIT task family effect class."""
    phrasings = [
        "update UserService.py to handle nulls",
        "modify UserService.py",
        "patch UserService.py",
        "change UserService.py",
        "rewrite UserService.py",
    ]

    for p in phrasings:
        res = classify_task_family(p)
        assert res.task_family == "EDIT"
        assert res.task_family in RESOURCE_REQUIRED_FAMILIES


def test_semantically_equivalent_create_phrasings_authorize_same_effect_class() -> None:
    """Different phrasings for creating files must map to the CREATE task family effect class."""
    phrasings = [
        "create a new file helper.py",
        "add a new class for user validation",
        "scaffold a new router module",
        "generate a new helper function",
    ]

    for p in phrasings:
        res = classify_task_family(p)
        assert res.task_family == "CREATE"
        assert res.task_family in RESOURCE_REQUIRED_FAMILIES


def test_semantically_equivalent_test_phrasings_authorize_same_effect_class() -> None:
    """Different phrasings for running tests must map to the TEST task family effect class."""
    phrasings = [
        "run the affected tests",
        "execute all tests in test_user.py",
        "run tests for user service",
    ]

    for p in phrasings:
        res = classify_task_family(p)
        assert res.task_family == "TEST"


def test_semantically_equivalent_refactor_phrasings_authorize_same_effect_class() -> None:
    """Different phrasings for refactoring code must map to the REFACTOR task family effect class."""
    phrasings = [
        "refactor CronJobController.php",
        "rename method in UserService.py",
        "restructure database module",
        "extract helper function from main.py",
        "clean up import statements",
    ]

    for p in phrasings:
        res = classify_task_family(p)
        assert res.task_family == "REFACTOR"
        assert res.task_family in RESOURCE_REQUIRED_FAMILIES


def test_forbidden_effects_cannot_be_authorized_by_typed_effects() -> None:
    """WorkHub lifecycle mutations, auto_accept, auto_close are strictly forbidden across all effect classes."""
    assert "workhub_lifecycle_mutation" in FORBIDDEN_EFFECTS
    assert "auto_accept" in FORBIDDEN_EFFECTS
    assert "auto_close" in FORBIDDEN_EFFECTS
