from __future__ import annotations

import pytest

from backend.services.devexecutor_search_replace_blocks_service import (
    SearchReplaceBlockError,
    apply_search_replace_blocks,
    parse_search_replace_blocks,
)


def test_parse_search_replace_blocks_accepts_multiple_blocks() -> None:
    blocks = parse_search_replace_blocks(
        "<<<<<<< SEARCH\nold one\n=======\nnew one\n>>>>>>> REPLACE\n\n"
        "<<<<<<< SEARCH\nold two\n=======\nnew two\n>>>>>>> REPLACE\n"
    )

    assert len(blocks) == 2
    assert blocks[0].search == "old one\n"
    assert blocks[0].replace == "new one\n"
    assert blocks[1].search == "old two\n"
    assert blocks[1].replace == "new two\n"


def test_parse_search_replace_blocks_rejects_unexpected_text_outside_blocks() -> None:
    with pytest.raises(SearchReplaceBlockError, match="unexpected text outside blocks"):
        parse_search_replace_blocks("notes\n<<<<<<< SEARCH\nold\n=======\nnew\n>>>>>>> REPLACE\n")


def test_parse_search_replace_blocks_rejects_empty_search() -> None:
    with pytest.raises(SearchReplaceBlockError, match="empty SEARCH text"):
        parse_search_replace_blocks("<<<<<<< SEARCH\n=======\nnew\n>>>>>>> REPLACE\n")


def test_apply_search_replace_blocks_replaces_exact_single_match() -> None:
    blocks = parse_search_replace_blocks("<<<<<<< SEARCH\nbeta\n=======\ngamma\n>>>>>>> REPLACE\n")

    updated, outcomes = apply_search_replace_blocks(original_text="alpha\nbeta\nomega\n", blocks=blocks)

    assert updated == "alpha\ngamma\nomega\n"
    assert outcomes == [{"index": 1, "match_count": 1, "no_op": False}]


def test_apply_search_replace_blocks_rejects_missing_search_text() -> None:
    blocks = parse_search_replace_blocks("<<<<<<< SEARCH\nbeta\n=======\ngamma\n>>>>>>> REPLACE\n")

    with pytest.raises(SearchReplaceBlockError, match="was not matchable"):
        apply_search_replace_blocks(original_text="alpha\nomega\n", blocks=blocks)


def test_apply_search_replace_blocks_rejects_ambiguous_search_text() -> None:
    blocks = parse_search_replace_blocks("<<<<<<< SEARCH\nbeta\n=======\ngamma\n>>>>>>> REPLACE\n")

    with pytest.raises(SearchReplaceBlockError, match="matched multiple locations"):
        apply_search_replace_blocks(original_text="beta\nalpha\nbeta\n", blocks=blocks)


def test_apply_search_replace_blocks_allows_empty_replace() -> None:
    blocks = parse_search_replace_blocks("<<<<<<< SEARCH\nbeta\n=======\n>>>>>>> REPLACE\n")

    updated, outcomes = apply_search_replace_blocks(original_text="alpha\nbeta\nomega\n", blocks=blocks)

    assert updated == "alpha\nomega\n"
    assert outcomes == [{"index": 1, "match_count": 1, "no_op": False}]


def test_apply_search_replace_blocks_reports_no_op_block() -> None:
    blocks = parse_search_replace_blocks("<<<<<<< SEARCH\nbeta\n=======\nbeta\n>>>>>>> REPLACE\n")

    updated, outcomes = apply_search_replace_blocks(original_text="alpha\nbeta\nomega\n", blocks=blocks)

    assert updated == "alpha\nbeta\nomega\n"
    assert outcomes == [{"index": 1, "match_count": 1, "no_op": True}]


def test_apply_search_replace_blocks_matches_file_without_trailing_newline() -> None:
    blocks = parse_search_replace_blocks("<<<<<<< SEARCH\nvalue = 1\n=======\nvalue = 2\n>>>>>>> REPLACE\n")

    updated, outcomes = apply_search_replace_blocks(original_text="value = 1", blocks=blocks)

    assert updated == "value = 2\n"
    assert outcomes == [{"index": 1, "match_count": 1, "no_op": False}]


def test_apply_search_replace_blocks_rejects_mid_line_substring_match() -> None:
    blocks = parse_search_replace_blocks("<<<<<<< SEARCH\n  return 1\n=======\n  return 2\n>>>>>>> REPLACE\n")

    with pytest.raises(SearchReplaceBlockError, match="was not matchable"):
        apply_search_replace_blocks(original_text="def f():\n    return 1\n", blocks=blocks)


def test_apply_search_replace_blocks_accepts_whitespace_drift_with_fuzzy_match() -> None:
    blocks = parse_search_replace_blocks(
        "<<<<<<< SEARCH\nif ready:\n    return value\n=======\nif ready:\n    return updated\n>>>>>>> REPLACE\n"
    )

    updated, outcomes = apply_search_replace_blocks(
        original_text="if ready:\n        return value\n",
        blocks=blocks,
    )

    assert updated == "if ready:\n    return updated\n"
    assert outcomes[0]["match_mode"] == "fuzzy_whitespace"
    assert float(outcomes[0]["similarity"]) >= 0.92


def test_apply_search_replace_blocks_accepts_single_line_indentation_normalized_match() -> None:
    blocks = parse_search_replace_blocks(
        "<<<<<<< SEARCH\n"
        "async def mutate_task(self, task_id: str) -> dict[str, str]:\n"
        "=======\n"
        "async def mutate_task(self, task_id: str, target_project_id: str | None = None) -> dict[str, str]:\n"
        ">>>>>>> REPLACE\n"
    )

    updated, outcomes = apply_search_replace_blocks(
        original_text=(
            "class ExampleService:\n"
            "    async def mutate_task(self, task_id: str) -> dict[str, str]:\n"
            "        return {'task_id': task_id}\n"
        ),
        blocks=blocks,
    )

    assert "    async def mutate_task(self, task_id: str, target_project_id: str | None = None)" in updated
    assert outcomes[0]["match_mode"] == "normalized_indentation"


def test_apply_search_replace_blocks_accepts_multiline_indentation_normalized_match() -> None:
    blocks = parse_search_replace_blocks(
        "<<<<<<< SEARCH\n"
        "async def mutate_task(self, task_id: str) -> dict[str, str]:\n"
        "    payload = {'task_id': task_id}\n"
        "    return payload\n"
        "=======\n"
        "async def mutate_task(self, task_id: str, target_project_id: str | None = None) -> dict[str, str]:\n"
        "    payload = {'task_id': task_id, 'target_project_id': target_project_id}\n"
        "    return payload\n"
        ">>>>>>> REPLACE\n"
    )

    updated, outcomes = apply_search_replace_blocks(
        original_text=(
            "class ExampleService:\n"
            "    async def mutate_task(self, task_id: str) -> dict[str, str]:\n"
            "        payload = {'task_id': task_id}\n"
            "        return payload\n"
        ),
        blocks=blocks,
    )

    assert "    async def mutate_task(self, task_id: str, target_project_id: str | None = None)" in updated
    assert "        payload = {'task_id': task_id, 'target_project_id': target_project_id}" in updated
    assert outcomes[0]["match_mode"] == "normalized_indentation"


def test_apply_search_replace_blocks_rejects_ambiguous_fuzzy_match() -> None:
    blocks = parse_search_replace_blocks(
        "<<<<<<< SEARCH\nif ready:\n    return value\n=======\nif ready:\n    return updated\n>>>>>>> REPLACE\n"
    )

    with pytest.raises(SearchReplaceBlockError, match="matched multiple locations"):
        apply_search_replace_blocks(
            original_text="if ready:\n        return value\n\nif ready:\n      return value\n",
            blocks=blocks,
        )
