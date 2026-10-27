"""Wave 4 CI3-CI5 acceptance tests: file candidates + symbol lookup + graph.

Run from ``hyeve-aix-service/``::

    pytest backend/codeintel_v2/tests/ -q
"""

from __future__ import annotations

from pathlib import Path

import pytest

import backend.codeintel_v2.dependency_graph as graph_module
import backend.codeintel_v2.file_candidates as candidates_module
import backend.codeintel_v2.symbol_lookup as lookup_module
from backend.codeintel_v2.dependency_graph import adjacent_of
from backend.codeintel_v2.file_candidates import (
    FileCandidate,
    candidate_to_dict,
    search_files,
)
from backend.codeintel_v2.symbol_lookup import (
    SymbolCandidate,
    lookup_symbol,
    symbol_to_dict,
)

BANNED_STEMS = ("target", "grant", "apply", "mutate")


def _workspace(tmp_path: Path) -> Path:
    (tmp_path / "svc").mkdir()
    (tmp_path / "svc" / "orders.py").write_text(
        "def place_order():\n    return 1\n", encoding="utf-8"
    )
    (tmp_path / "svc" / "orders_service.py").write_text(
        "def place_order():\n    return 2\n", encoding="utf-8"
    )
    (tmp_path / "svc" / "unrelated.py").write_text("x = 1\n", encoding="utf-8")
    return tmp_path


# ------------------------------------------------------- ambiguity rejection


def test_ambiguity_rejection_flags_all_selects_none(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    results = search_files("orders", root)
    assert len(results) > 1
    assert all(item["ambiguous"] is True for item in results)
    assert all(item["selected"] is False for item in results)
    assert all(item["confidence"] >= 0.5 for item in results)


def test_single_candidate_still_selects_none(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    results = search_files("orders_service.py", root)
    assert len(results) == 1
    assert results[0]["ambiguous"] is False
    assert results[0]["selected"] is False
    assert results[0]["path"] == "svc/orders_service.py"


def test_non_files_dropped(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    # A directory whose name matches the query must not become a candidate.
    (tmp_path / "orders_backup").mkdir()
    results = search_files("orders", root)
    assert results, "expected file candidates"
    assert all(item["path"] != "orders_backup" for item in results)
    for item in results:
        assert (root / item["path"]).is_file()


def test_no_query_match_returns_empty(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    assert search_files("zzz_no_such_file", root) == []
    assert search_files("", root) == []
    assert search_files(None, root) == []


# ------------------------------------------------------------ symbol lookup


def test_lookup_symbol_found_in_explicit_file(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    candidates, outcome = lookup_symbol("place_order", "svc/orders.py", root)
    assert outcome == "found"
    assert len(candidates) == 1
    assert candidates[0]["symbol"] == "place_order"
    assert candidates[0]["file"] == "svc/orders.py"
    assert candidates[0]["line"] == 1
    assert 0.0 <= candidates[0]["confidence"] <= 1.0


def test_lookup_symbol_no_match_classified(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    candidates, outcome = lookup_symbol("no_such_symbol", "svc/orders.py", root)
    assert candidates == []
    assert outcome.startswith("not-found:")
    assert outcome == "not-found:no-match"


def test_lookup_symbol_never_guesses_across_files(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    # place_order exists in orders_service.py but NOT in unrelated.py.
    candidates, outcome = lookup_symbol("place_order", "svc/unrelated.py", root)
    assert candidates == []
    assert outcome == "not-found:no-match"


def test_lookup_symbol_missing_file_classified(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    candidates, outcome = lookup_symbol("place_order", "svc/gone.py", root)
    assert candidates == []
    assert outcome.startswith("not-found:")


# -------------------------------------------------------- dependency graph


def test_adjacent_of_depth1_missing_dropped(tmp_path: Path) -> None:
    root = tmp_path
    (root / "a.py").write_text(
        "import b\nimport missing_module\n", encoding="utf-8"
    )
    (root / "b.py").write_text("import c\n", encoding="utf-8")
    (root / "c.py").write_text("x = 1\n", encoding="utf-8")
    info = adjacent_of("a.py", root)
    assert info["freshness"] == "unknown"
    assert info["advisory_only"] is True
    assert info["adjacent"] == ["b.py"]
    assert "c.py" not in info["adjacent"]  # no transitive walk beyond depth 1


def test_adjacent_of_never_scope_documented() -> None:
    assert "never-scope" in (adjacent_of.__doc__ or "")
    assert "unknown" in (adjacent_of.__doc__ or "")


# ------------------------------------------------------------ advisory-only


@pytest.mark.parametrize(
    "module", [candidates_module, lookup_module, graph_module]
)
def test_modules_expose_no_authority_symbols(module: object) -> None:
    for name in dir(module):
        lowered = name.lower()
        for stem in BANNED_STEMS:
            assert stem not in lowered, f"{module.__name__} exposes {name!r}"


def test_search_files_exposes_no_chosen_file(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    results = search_files("orders_service.py", root)
    assert len(results) == 1
    assert "chosen" not in results[0]
    assert results[0]["selected"] is False


# ------------------------------------------------------- confidence bounds


@pytest.mark.parametrize("bad", [-0.1, 1.5, 100, float("nan"), float("inf"), "0.9", None, True])
def test_file_candidate_rejects_bad_confidence(bad: object) -> None:
    with pytest.raises(ValueError):
        FileCandidate(path="svc/orders.py", confidence=bad, reason="r")  # type: ignore[arg-type]


@pytest.mark.parametrize("bad", [-0.1, 1.5, 100, float("nan"), float("inf"), "0.9", None, True])
def test_symbol_candidate_rejects_bad_confidence(bad: object) -> None:
    with pytest.raises(ValueError):
        SymbolCandidate(symbol="f", file="svc/orders.py", line=1, confidence=bad)  # type: ignore[arg-type]


@pytest.mark.parametrize("good", [0.0, 1.0, 1, 0])
def test_confidence_bounds_accepted(good: object) -> None:
    file_candidate = FileCandidate(path="svc/orders.py", confidence=good, reason="r")  # type: ignore[arg-type]
    symbol_candidate = SymbolCandidate(symbol="f", file="svc/orders.py", line=1, confidence=good)  # type: ignore[arg-type]
    assert 0.0 <= file_candidate.confidence <= 1.0
    assert 0.0 <= symbol_candidate.confidence <= 1.0
    assert candidate_to_dict(file_candidate)["confidence"] == file_candidate.confidence
    assert symbol_to_dict(symbol_candidate)["confidence"] == symbol_candidate.confidence
