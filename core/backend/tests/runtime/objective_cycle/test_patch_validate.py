import pytest
import shutil
import tempfile
from pathlib import Path

from backend.aix_core.runtime.executor.patch_validate import (
    apply_patch_atomic,
    compute_content_hash,
    validate_patch,
    validate_write_file_policy,
)


@pytest.fixture
def tmp_path() -> Path:
    """Overrides pytest's built-in `tmp_path` fixture with a private
    `tempfile.mkdtemp()` directory. The original override here used a *fixed*,
    repo-relative, git-adjacent directory (`.tmp_patch_validate`) shared across every
    test run to work around this environment's shared pytest `%TEMP%\\pytest-of-*`
    basetemp having intermittent Windows permission contention -- but a fixed shared
    path has its own collision surface (confirmed: intermittent `os.replace`
    `PermissionError` under concurrent/back-to-back runs, the same class of failure
    `test_cursor_41_50_d_g.py`'s budget-ledger test had with its own fixed path). A
    private `tempfile.mkdtemp()` per test avoids both collision surfaces.
    """
    test_dir = Path(tempfile.mkdtemp(prefix="aix-patch-validate-test-"))
    yield test_dir
    shutil.rmtree(test_dir, ignore_errors=True)


def test_positive_add_logging_patch_validation_and_apply(tmp_path: Path) -> None:
    """Request: Update function calculate_total in math_utils.py to add logging."""
    file_path = tmp_path / "math_utils.py"
    initial_content = "def calculate_total(items):\n    return sum(items)\n"
    file_path.write_text(initial_content, encoding="utf-8")

    before_hash = compute_content_hash(initial_content)

    diff = (
        "--- a/math_utils.py\n"
        "+++ b/math_utils.py\n"
        "@@ -1,2 +1,4 @@\n"
        "+import logging\n"
        " def calculate_total(items):\n"
        "+    logging.info('calculating total')\n"
        "     return sum(items)\n"
    )

    res = validate_patch(
        file_path=file_path,
        existing_content=initial_content,
        unified_diff=diff,
        expected_file_hash=before_hash,
    )

    assert res.valid is True
    assert res.reason_code is None
    assert res.lines_added == 2
    assert res.lines_removed == 0
    assert res.before_hash == before_hash
    assert res.new_content is not None
    assert "import logging" in res.new_content
    assert "logging.info('calculating total')" in res.new_content

    # Test atomic apply
    after_hash = apply_patch_atomic(file_path, res)
    assert file_path.read_text(encoding="utf-8") == res.new_content
    assert after_hash == compute_content_hash(res.new_content)


def test_wrong_context_returns_patch_context_mismatch_and_file_unchanged(tmp_path: Path) -> None:
    """Wrong hunk context lines return PATCH_CONTEXT_MISMATCH and file remains untouched."""
    file_path = tmp_path / "math_utils.py"
    initial_content = "def calculate_total(items):\n    return sum(items)\n"
    file_path.write_text(initial_content, encoding="utf-8")

    # Hunk expects 'def subtract_total(items):' which does not match initial_content
    wrong_diff = (
        "--- a/math_utils.py\n"
        "+++ b/math_utils.py\n"
        "@@ -1,2 +1,3 @@\n"
        " def subtract_total(items):\n"
        "+    logging.info('subtracting')\n"
        "     return sum(items)\n"
    )

    res = validate_patch(
        file_path=file_path,
        existing_content=initial_content,
        unified_diff=wrong_diff,
    )

    assert res.valid is False
    assert res.reason_code == "PATCH_CONTEXT_MISMATCH"
    assert res.new_content is None
    assert file_path.read_text(encoding="utf-8") == initial_content  # unchanged!


def test_stale_precondition_hash_returns_stale_file_version() -> None:
    """Expected file hash mismatch rejects with STALE_FILE_VERSION."""
    initial_content = "def foo(): pass\n"
    diff = (
        "--- a/foo.py\n"
        "+++ b/foo.py\n"
        "@@ -1,1 +1,2 @@\n"
        " def foo(): pass\n"
        "+# comment\n"
    )

    res = validate_patch(
        file_path="foo.py",
        existing_content=initial_content,
        unified_diff=diff,
        expected_file_hash="0000000000000000000000000000000000000000000000000000000000000000",
    )

    assert res.valid is False
    assert res.reason_code == "STALE_FILE_VERSION"


def test_no_op_patch_rejection() -> None:
    """Empty or +0/-0 diff returns NO_OP_PATCH."""
    initial_content = "def foo(): pass\n"

    res_empty = validate_patch(
        file_path="foo.py",
        existing_content=initial_content,
        unified_diff="",
    )
    assert res_empty.valid is False
    assert res_empty.reason_code == "NO_OP_PATCH"

    diff_no_change = (
        "--- a/foo.py\n"
        "+++ b/foo.py\n"
        "@@ -1,1 +1,1 @@\n"
        " def foo(): pass\n"
    )
    res_no_change = validate_patch(
        file_path="foo.py",
        existing_content=initial_content,
        unified_diff=diff_no_change,
    )
    assert res_no_change.valid is False
    assert res_no_change.reason_code == "NO_OP_PATCH"


def test_write_file_create_only_policy(tmp_path: Path) -> None:
    """write_file must reject overwriting an existing file (create_only=True)."""
    existing_file = tmp_path / "existing.py"
    existing_file.write_text("# existing content\n", encoding="utf-8")

    # Trying to write to existing file with create_only=True
    policy_res = validate_write_file_policy(existing_file, "new whole file content", create_only=True)
    assert policy_res.allowed is False
    assert policy_res.reason_code == "CREATE_ONLY_VIOLATION"

    # Creating a new file that does not exist yet
    new_file = tmp_path / "brand_new.py"
    new_policy_res = validate_write_file_policy(new_file, "new file content", create_only=True)
    assert new_policy_res.allowed is True
    assert new_policy_res.reason_code is None
