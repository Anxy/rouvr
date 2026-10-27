"""AIX-REAL-EXECUTION-002: universal task-family root router."""

from __future__ import annotations

from backend.aix_core.runtime.objective.task_family import (
    RESOURCE_REQUIRED_FAMILIES,
    classify_task_family,
)


def test_empty_text_is_unknown_with_zero_confidence() -> None:
    result = classify_task_family("")
    assert result.task_family == "UNKNOWN"
    assert result.classification_confidence == 0.0


def test_env_read_objective_classifies_as_read() -> None:
    result = classify_task_family(
        r"D:\v2.1\hyeve\services\hyeve-integration-service\.env read this file"
    )
    assert result.task_family == "READ"
    assert result.classification_method == "deterministic"
    assert result.classification_confidence > 0


def test_debug_phrasing_wins_over_generic_edit_verbs() -> None:
    result = classify_task_family("Why is login failing?")
    assert result.task_family == "DEBUG"


def test_ambiguous_fix_the_client_classifies_as_edit() -> None:
    result = classify_task_family("fix the client")
    assert result.task_family == "EDIT"
    assert result.task_family in RESOURCE_REQUIRED_FAMILIES


def test_run_the_tests_classifies_as_test_not_command() -> None:
    result = classify_task_family("Run the affected tests")
    assert result.task_family == "TEST"


def test_find_where_implemented_classifies_as_search() -> None:
    result = classify_task_family("Find where retries are implemented.")
    assert result.task_family == "SEARCH"


def test_review_this_service_classifies_as_review() -> None:
    result = classify_task_family("Review this service.")
    assert result.task_family == "REVIEW"


def test_explain_this_folder_classifies_as_explain() -> None:
    result = classify_task_family("Explain this folder.")
    assert result.task_family == "EXPLAIN"


def test_continue_refactoring_classifies_as_refactor() -> None:
    result = classify_task_family(
        r"Continue refactoring D:\v2.1\Visions\CronJobController.php only: insert section comments"
    )
    assert result.task_family == "REFACTOR"
