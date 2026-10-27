"""Live-CLI regression: natural-language and planner-prose replace parsing."""

from __future__ import annotations

from backend.aix_core.runtime.executor.source_patch_codegen import (
    extract_text_replacements,
    local_fallback_text_replacements,
)


class _Discovery:
    def __init__(self, files: dict[str, str]) -> None:
        self.config_files = files
        self.source_files = {}
        self.focused_paths = tuple(files.keys())


def test_extract_replace_the_string_without_quotes() -> None:
    pairs = extract_text_replacements(
        "replace the string TailAdmin Next.js - Free Next.js Tailwind Admin Dashboard Template "
        "with TailAdmin Next.js (AIX smoke) in README.md only"
    )
    assert pairs == [
        (
            "TailAdmin Next.js - Free Next.js Tailwind Admin Dashboard Template",
            "TailAdmin Next.js (AIX smoke)",
        )
    ]


def test_extract_replace_the_exact_string_from_live_cli_objective() -> None:
    pairs = extract_text_replacements(
        "Replace the exact string TailAdmin Next.js - Free Next.js Tailwind Admin "
        "Dashboard Template with TailAdmin Next.js (AIX live repair proof) in "
        "README.md. Do not create HTML. Do not create CSS. Do not create proposal "
        "artifacts. Modify only README.md and verify the result."
    )
    assert pairs == [
        (
            "TailAdmin Next.js - Free Next.js Tailwind Admin Dashboard Template",
            "TailAdmin Next.js (AIX live repair proof)",
        )
    ]


def test_extract_replace_the_exact_text_alias() -> None:
    pairs = extract_text_replacements(
        'Replace the exact text "AIX live-repair-20260815-v3" with '
        '"AIX live-summary-test-20260815" in AIX-Frontend/README.md only.'
    )
    assert pairs == [
        ("AIX live-repair-20260815-v3", "AIX live-summary-test-20260815")
    ]


def test_extract_replace_the_exact_string_with_quotes_strips_delimiters() -> None:
    """Live CLI wraps OLD/NEW in quotes; those are delimiters."""
    objective = (
        'Replace the exact string "TailAdmin Next.js - Free Next.js Tailwind Admin '
        'Dashboard Template" with "TailAdmin Next.js (AIX live-repair-20260815-v3)" in '
        "D:/v2.1/AIX/hyeve-aix-service/AIX-Frontend/README.md only. Do not create HTML. "
        "Do not create CSS. Do not create proposal artifacts. Modify README.md and "
        "verify the result."
    )
    pairs = extract_text_replacements(objective)
    assert pairs == [
        (
            "TailAdmin Next.js - Free Next.js Tailwind Admin Dashboard Template",
            "TailAdmin Next.js (AIX live-repair-20260815-v3)",
        )
    ]
    content = "# TailAdmin Next.js - Free Next.js Tailwind Admin Dashboard Template\n\nBody\n"
    result = local_fallback_text_replacements(_Discovery({"README.md": content}), objective)
    assert result is not None
    assert len(result.edits) == 1
    assert "AIX live-repair-20260815-v3" in result.edits[0].new_content
    assert "Free Next.js Tailwind Admin Dashboard Template" not in result.edits[0].new_content


def test_extract_planner_is_replaced_with_prose() -> None:
    pairs = extract_text_replacements(
        'Acceptance: The exact string "OLD TITLE" is replaced with "NEW TITLE" in README.md.'
    )
    assert ("OLD TITLE", "NEW TITLE") in pairs


def test_local_fallback_applies_readme_title_replace() -> None:
    content = "# TailAdmin Next.js - Free Next.js Tailwind Admin Dashboard Template\n\nBody\n"
    discovery = _Discovery({"README.md": content})
    objective = (
        "Planner guidance:\n"
        'Acceptance: The exact string "TailAdmin Next.js - Free Next.js Tailwind Admin Dashboard Template" '
        'is replaced with "TailAdmin Next.js (AIX smoke)" in README.md.'
    )
    result = local_fallback_text_replacements(discovery, objective)
    assert result is not None
    assert len(result.edits) == 1
    assert "TailAdmin Next.js (AIX smoke)" in result.edits[0].new_content
    assert "Free Next.js Tailwind Admin Dashboard Template" not in result.edits[0].new_content


def test_already_satisfied_requires_new_text_present() -> None:
    content = "# TailAdmin Next.js (AIX live repair proof)\n\nBody\n"
    discovery = _Discovery({"README.md": content})
    objective = (
        'Exact requested replacement: replace "TailAdmin Next.js - Free Next.js Tailwind Admin '
        'Dashboard Template" with "TailAdmin Next.js (AIX live-repair-20260815-v3)"'
    )
    result = local_fallback_text_replacements(discovery, objective)
    assert result is not None
    assert result.edits == []
    assert result.already_satisfied is False
    assert result.remaining_asked_work is True


def test_already_satisfied_when_new_present_and_old_absent() -> None:
    content = "# TailAdmin Next.js (AIX live-repair-20260815-v3)\n\nBody\n"
    discovery = _Discovery({"README.md": content})
    objective = (
        'Exact requested replacement: replace "TailAdmin Next.js - Free Next.js Tailwind Admin '
        'Dashboard Template" with "TailAdmin Next.js (AIX live-repair-20260815-v3)"'
    )
    result = local_fallback_text_replacements(discovery, objective)
    assert result is not None
    assert result.edits == []
    assert result.already_satisfied is True


def test_substring_new_does_not_cascade_replace() -> None:
    content = "# marker AIX live-summary-test-20260815 end\n"
    discovery = _Discovery({"README.md": content})
    objective = (
        'Replace the exact text "AIX live-summary-test-20260815" with '
        '"AIX live-summary-test-20260815-pass" in README.md only.'
    )
    result = local_fallback_text_replacements(discovery, objective)
    assert result is not None
    assert len(result.edits) == 1
    assert result.edits[0].new_content.count("pass") == 1
    # Second application must be a no-op (would cascade without the guard).
    result2 = local_fallback_text_replacements(
        _Discovery({"README.md": result.edits[0].new_content}), objective
    )
    assert result2 is not None
    assert result2.edits == [] or result2.already_satisfied or result2.remaining_asked_work
    if result2.edits:
        assert result2.edits[0].new_content.count("pass") == 1


def test_extract_backtick_delimited_operands_with_inner_quotes() -> None:
    """S2 live: backticks around JSON/assert snippets must not truncate at inner "."""
    pairs = extract_text_replacements(
        "replace the exact text `\"enabled\": False` with `\"enabled\": True` "
        "and replace the exact text `\"version\": 1` with `\"version\": 2` "
        "and replace the exact text `assert FEATURE[\"enabled\"] is False` with "
        "`assert FEATURE[\"enabled\"] is True`"
    )
    assert ('"enabled": False', '"enabled": True') in pairs
    assert ('"version": 1', '"version": 2') in pairs
    assert (
        'assert FEATURE["enabled"] is False',
        'assert FEATURE["enabled"] is True',
    ) in pairs


def test_bound_seed_does_not_repo_search_when_skip_set(monkeypatch) -> None:
    """Bound multi-file scope must not expand via repo-wide OLD-token search."""
    from backend.aix_core.runtime.objective.discovery import DiscoveryReport
    from backend.aix_core.runtime.executor.multi_step_execute import _seed_files_for_replacements

    calls: list[str] = []

    class _Toolbox:
        repository_root = None

        def search_files(self, query: str):
            calls.append(query)
            return {"hits": [{"path": "unrelated/other.py"}]}

        def read_file(self, path: str, max_bytes: int = 0):
            raise AssertionError("bound seed must not read outside discovery")

    report = DiscoveryReport()
    report.config_files = {
        "backend/tests/aix_core/objective_cycle/live_fixtures/feature_flag.py": (
            'FEATURE = {"enabled": False, "version": 1}\n'
        ),
    }
    out = _seed_files_for_replacements(
        toolbox=_Toolbox(),
        report=report,
        objective='replace the exact text `"enabled": False` with `"enabled": True`',
        skip_search_if_present=True,
    )
    assert calls == []
    assert list(out.config_files) == list(report.config_files)


def test_structured_pairs_preferred_over_mangled_json_dumps_prose() -> None:
    """S2 live: planner re-embed via json.dumps must not win over raw pairs."""
    discovery = _Discovery(
        {
            "feature_flag.py": 'FEATURE = {"enabled": False, "version": 1}\n',
        }
    )
    mangled = (
        'Exact requested replacement: replace "\\"enabled\\": False" with "\\"enabled\\": True"'
    )
    # Without structured pairs, mangled prose fails to match source.
    miss = local_fallback_text_replacements(discovery, mangled)
    assert miss is not None
    assert miss.remaining_asked_work is True
    # With structured pairs from the raw user objective, mutation succeeds.
    ok = local_fallback_text_replacements(
        discovery,
        mangled,
        pairs=[('"enabled": False', '"enabled": True'), ('"version": 1', '"version": 2')],
    )
    assert ok is not None
    assert ok.edits
    assert '"enabled": True' in ok.edits[0].new_content
    assert '"version": 2' in ok.edits[0].new_content
