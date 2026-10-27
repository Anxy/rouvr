"""Intent: refactor objectives mentioning 'create or change' stay EDIT, not CREATE."""

from backend.aix_core.runtime.executor.real_execute import classify_intent


def test_s6_refactor_with_create_or_change_is_edit_not_create() -> None:
    objective = (
        "Refactor CronJobController into a layered architecture: extract at least "
        "three domain-focused service classes, and run php -l on every PHP file you "
        "create or change."
    )
    assert classify_intent(objective, target_path=None, symbol_name=None) == "EDIT_FILE"


def test_pure_create_still_create_file() -> None:
    objective = "Create a new helper module mutation_helpers.py with bump_version."
    assert classify_intent(objective, target_path=None, symbol_name=None) == "CREATE_FILE"


def test_empty_directory_is_create_file_not_edit() -> None:
    objective = (
        "Create an empty directory named my-web-app at my-web-app. "
        "Track it with my-web-app/.gitkeep. Do not modify any existing files."
    )
    assert classify_intent(objective, target_path=None, symbol_name=None) == "CREATE_FILE"
