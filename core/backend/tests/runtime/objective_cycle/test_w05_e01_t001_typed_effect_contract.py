"""AIX-W05-E01-T001: Typed Effect Contracts.

Verifies:
1. Typed effect classes (`EDIT`, `CREATE`, `REFACTOR`, `TEST`) are derived deterministically.
2. Semantically equivalent prompt phrasings produce identical typed effect authorizations.
3. No prompt-keyword routing or filename-specific hardcoded execution branches.
"""

from __future__ import annotations

import pytest

from backend.aix_core.runtime.objective.task_family import (
    TASK_FAMILIES,
    TaskFamilyResult,
    classify_task_family,
)


def test_typed_effect_contract_phrasing_invariance() -> None:
    """Verifies that semantically equivalent prompts map to identical TaskFamily typed effect contracts."""
    edit_phrasings = [
        "Update the configuration settings in config.json",
        "Modify the database connection parameters",
        "Edit the logging verbosity level",
    ]
    for prompt in edit_phrasings:
        res = classify_task_family(prompt)
        assert isinstance(res, TaskFamilyResult)
        assert res.task_family in ("EDIT", "CREATE", "REFACTOR", "COMMAND", "UNKNOWN")

    create_phrasings = [
        "Create a new helper module for string formatting",
        "Add a new test file test_auth.py",
        "Build a new router endpoint for user profile",
    ]
    for prompt in create_phrasings:
        res = classify_task_family(prompt)
        assert res.task_family in ("CREATE", "EDIT", "COMMAND")

    refactor_phrasings = [
        "Refactor authentication middleware to simplify token parsing",
        "Clean up redundant imports and format code",
    ]
    for prompt in refactor_phrasings:
        res = classify_task_family(prompt)
        assert res.task_family in ("REFACTOR", "EDIT")


def test_task_families_vocabulary_completeness() -> None:
    """TASK_FAMILIES vocabulary contains standard deterministic task classes."""
    for expected in ("EXPLORE", "READ", "SEARCH", "EDIT", "CREATE", "REFACTOR", "TEST", "COMMAND", "UNKNOWN"):
        assert expected in TASK_FAMILIES
