from backend.aix_core.runtime.executor.read_file_execute import extract_target_path
from backend.aix_core.runtime.executor.real_execute import classify_intent


def test_refactor_bound_file_classifies_as_edit_not_read() -> None:
    objective = r'hey can you refactor this file "D:\v2.1\Visions\web.php"'
    target_path = extract_target_path(objective)
    assert classify_intent(objective, target_path=target_path, symbol_name=None) == "EDIT_FILE"


def test_refactor_with_remove_duplicate_classifies_as_edit() -> None:
    objective = (
        "Refactor D:\\v2.1\\Visions\\web.php: group routes by module with section comments, "
        "normalize Route::Post/Route::Get to lowercase post/get everywhere, "
        "and remove duplicate route definitions"
    )
    target_path = extract_target_path(objective)
    assert classify_intent(objective, target_path=target_path, symbol_name=None) == "EDIT_FILE"


def test_edit_with_pytest_validation_is_edit_not_run_tests() -> None:
    """Mutation + focused pytest DoD must not route to full-repo RUN_TESTS discovery."""
    objective = (
        'set FEATURE["version"] to 3 (replace the exact text `"version": 2` with `"version": 3`) '
        "in backend/tests/aix_core/objective_cycle/live_fixtures/feature_flag.py, and update "
        "backend/tests/aix_core/objective_cycle/live_fixtures/test_feature_flag.py so it asserts "
        'FEATURE["version"] == 3. Run python -m pytest '
        "backend/tests/aix_core/objective_cycle/live_fixtures/test_feature_flag.py -q"
    )
    assert classify_intent(objective, target_path=None, symbol_name=None) == "EDIT_FILE"


def test_define_named_function_without_edit_verb_is_not_edit_symbol() -> None:
    objective = "Define double(x) in the confirmed utility module gaef001-scratch/util_g.py."
    assert (
        classify_intent(
            objective,
            target_path="gaef001-scratch/util_g.py",
            symbol_name="double",
        )
        != "EDIT_SYMBOL"
    )


def test_create_new_module_with_function_name_is_create_not_edit_symbol() -> None:
    objective = (
        "create a new Python module at backend/tests/foo/mutation_helpers.py that defines "
        "a function bump_version(feature: dict) -> dict which returns a shallow copy"
    )
    assert classify_intent(objective, target_path=None, symbol_name=None) == "CREATE_FILE"
    # Even when a symbol extractor would fire, create phrasing must win.
    assert (
        classify_intent(objective, target_path=None, symbol_name="bump_version") == "CREATE_FILE"
    )


def test_pure_run_tests_still_classifies_as_run_tests() -> None:
    assert (
        classify_intent("Run the tests in this repository", target_path=None, symbol_name=None)
        == "RUN_TESTS"
    )
    assert classify_intent("execute pytest", target_path=None, symbol_name=None) == "RUN_TESTS"
