from __future__ import annotations

from backend.services import task_runner_planner_validation as validation_module


def test_step_relative_path_reads_args_and_step_keys() -> None:
    assert validation_module._step_relative_path(None) == ""
    assert (
        validation_module._step_relative_path({"tool": "edit_file", "args": {"relative_path": "a/b.py"}})
        == "a/b.py"
    )
    assert validation_module._step_relative_path({"tool": "edit_file", "path": "c/d.py"}) == "c/d.py"


def test_decode_validation_error_parses_code_and_detail() -> None:
    code, detail = validation_module._decode_validation_error('E1: {"reason": "x"}')
    assert code == "E1"
    assert detail == {"reason": "x"}
    code, detail = validation_module._decode_validation_error("E2: plain text")
    assert code == "E2"
    assert detail == {"detail": "plain text"}
    assert validation_module._decode_validation_error(None) == ("", {})
    assert validation_module._decode_validation_error("") == ("", {})


def test_is_v2_structured_edit_file_args_predicate() -> None:
    assert validation_module._is_v2_structured_edit_file_args(
        {
            "grounding_source": "read_range",
            "search_range": {"start_line": 1},
            "replacement_text": "new",
        }
    )
    assert not validation_module._is_v2_structured_edit_file_args({"grounding_source": "other"})
    assert not validation_module._is_v2_structured_edit_file_args({})


def test_normalize_planner_step_dialects_passthrough() -> None:
    steps = [{"tool": "read_file", "args": {}}, "legacy"]
    normalized = validation_module._normalize_planner_step_dialects(steps)
    assert normalized == steps
    assert normalized[0] is not steps[0]


def test_edit_file_helpers_delegate_to_bridge() -> None:
    from backend.services import devexecutor_planner_bridge_service as bridge_module

    canonical = "<<<<<<< SEARCH\nold\n=======\nnew\n>>>>>>> REPLACE"
    assert validation_module._extract_edit_file_search_replace_pairs(
        canonical
    ) == bridge_module._extract_canonical_search_replace_pairs(canonical)
    payload = [{"search": "old", "replace": "new"}]
    assert validation_module._structured_search_replace_blocks_to_canonical(
        payload
    ) == bridge_module._canonicalize_edit_file_blocks(payload)
    assert validation_module._normalize_edit_file_search_replace_blocks(
        payload
    ) == bridge_module._canonicalize_edit_file_blocks(payload)
    assert validation_module._extract_structured_search_replace_pair_from_item(
        {"search": "old", "replace": "new"}
    ) == bridge_module._extract_semantic_search_replace_pair({"search": "old", "replace": "new"})


def test_wired_registries_resolve_from_validation_module() -> None:
    assert callable(validation_module.normalize_edit_file_args)
    assert callable(validation_module.is_code_mutation_tool)
    assert callable(validation_module.resolve_python_symbol_range)
    assert callable(validation_module.get_code_change_allowed_mutation_tools)
    assert "edit_file" in set(validation_module.get_code_change_allowed_mutation_tools())
