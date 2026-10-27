"""Wave 4 CI6 acceptance tests: context packs + evidence attach helper.

Run from ``hyeve-aix-service/``::

    pytest backend/codeintel_v2/tests/ -q
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from backend.codeintel_v2.citations import Citation
from backend.codeintel_v2.context_packs import (
    MAX_PACK_CITATIONS,
    ContextPack,
    build_pack,
)
from backend.codeintel_v2.evidence_attach import (
    ADDED_KEYS,
    attach_citations_to_evidence,
)
from backend.codeintel_v2.snapshots import CodeIntelSnapshot
from backend.hyeve_aix_service.config import settings

_THIS_DIR = Path(__file__).resolve().parent
_PACKAGE_DIR = _THIS_DIR.parent

_SNAP = CodeIntelSnapshot(
    repo_id="repo-a", commit_sha="abc123", snapshot_id="snap-1"
)


def _freshness(label: str) -> dict[str, Any]:
    return {
        "repo_id": "repo-a",
        "commit_sha": "abc123",
        "snapshot_id": "snap-1",
        "workspace_head": "abc123",
        "freshness": label,
        "reason": f"{label} for test",
    }


def _citation(label: str = "current", **overrides: Any) -> Citation:
    params: dict[str, Any] = {
        "path": "svc/orders.py",
        "line_range": (10, 24),
        "snapshot_id": "snap-1",
        "commit_sha": "abc123",
        "confidence": 0.82,
        "source": "codeintel_v2",
        "freshness": _freshness(label),
    }
    params.update(overrides)
    return Citation(**params)


@pytest.fixture
def advisory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "AIX_CODEINTEL", "advisory")


@pytest.fixture
def off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "AIX_CODEINTEL", "off")


# ---------------------------------------------------------------- build_pack


def test_all_current_pack_is_current_not_degraded(advisory: None) -> None:
    pack = build_pack([_citation("current"), _citation("current")], _SNAP, "abc123")
    assert isinstance(pack, ContextPack)
    assert pack.freshness.freshness == "current"
    assert pack.advisory_degraded is False
    assert len(pack.citations) == 2
    assert pack.truncated is False


def test_any_stale_makes_pack_stale(advisory: None) -> None:
    pack = build_pack([_citation("current"), _citation("stale")], _SNAP, "abc123")
    assert pack.freshness.freshness == "stale"
    assert pack.advisory_degraded is True


def test_stale_wins_over_unknown(advisory: None) -> None:
    pack = build_pack([_citation("unknown"), _citation("stale")], _SNAP, "abc123")
    assert pack.freshness.freshness == "stale"
    assert pack.advisory_degraded is True


def test_only_unknown_makes_pack_unknown(advisory: None) -> None:
    pack = build_pack([_citation("unknown")], _SNAP, "abc123")
    assert pack.freshness.freshness == "unknown"
    assert pack.advisory_degraded is True


def test_empty_citations_is_empty_pack_not_error(advisory: None) -> None:
    for empty in ([], (), None):
        pack = build_pack(empty, _SNAP, "abc123")
        assert pack.citations == []
        assert "empty" in pack.reason
        assert pack.truncated is False


def test_provenance_gap_raises_value_error(advisory: None) -> None:
    bad_items: list[Any] = [
        {"path": "svc/orders.py"},  # plain dict, not a Citation
        "svc/orders.py",  # wrong shape entirely
        None,
        42,
    ]
    for bad in bad_items:
        with pytest.raises(ValueError):
            build_pack([_citation("current"), bad], _SNAP, "abc123")


def test_truncation_caps_at_fifty_with_flag(advisory: None) -> None:
    many = [
        _citation("current", path=f"svc/mod_{i}.py", line_range=(i + 1, i + 2))
        for i in range(MAX_PACK_CITATIONS + 5)
    ]
    pack = build_pack(many, _SNAP, "abc123")
    assert len(pack.citations) == MAX_PACK_CITATIONS
    assert pack.truncated is True
    assert "truncated" in pack.reason
    assert pack.freshness.freshness == "current"


def test_exactly_fifty_is_not_truncated(advisory: None) -> None:
    many = [
        _citation("current", path=f"svc/mod_{i}.py", line_range=(i + 1, i + 2))
        for i in range(MAX_PACK_CITATIONS)
    ]
    pack = build_pack(many, _SNAP, "abc123")
    assert len(pack.citations) == MAX_PACK_CITATIONS
    assert pack.truncated is False


def test_posture_off_returns_empty_pack_no_fabrication(off: None) -> None:
    pack = build_pack([_citation("current"), _citation("stale")], _SNAP, "abc123")
    assert pack.citations == []
    assert "posture_off" in pack.reason
    assert pack.freshness.freshness == "unknown"


def test_posture_off_with_garbage_input_still_empty_no_error(off: None) -> None:
    pack = build_pack([{"not": "a citation"}, None], _SNAP, "abc123")
    assert pack.citations == []
    assert "posture_off" in pack.reason


@pytest.mark.parametrize("labels", [
    ["current"],
    ["current", "current"],
    ["stale"],
    ["current", "stale"],
    ["unknown"],
    ["unknown", "stale"],
    [],
])
def test_pack_freshness_declared_on_every_pack(
    advisory: None, labels: list[str]
) -> None:
    pack = build_pack([_citation(label) for label in labels], _SNAP, "abc123")
    assert pack.freshness.freshness in ("current", "stale", "unknown")
    assert pack.freshness.reason.strip() != ""
    assert pack.reason.strip() != ""
    assert pack.pack_id.strip() != ""
    assert pack.built_at.strip() != ""


def test_empty_and_posture_off_packs_declare_freshness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "AIX_CODEINTEL", "advisory")
    empty = build_pack([], _SNAP, "abc123")
    assert empty.freshness.freshness == "unknown"
    assert empty.reason.strip() != ""
    monkeypatch.setattr(settings, "AIX_CODEINTEL", "off")
    refused = build_pack([_citation("current")], _SNAP, "abc123")
    assert refused.freshness.freshness == "unknown"
    assert "posture_off" in refused.reason


# ------------------------------------------------------- evidence attach


def _recon_payload() -> dict[str, Any]:
    return {
        "target_file": "svc/orders.py",
        "target_files": ["svc/orders.py", "svc/refunds.py"],
        "anchor": {"symbol": "place_order", "line": 10},
        "summary": "recon summary text",
    }


def test_attach_returns_new_dict_with_three_keys(advisory: None) -> None:
    payload = _recon_payload()
    before = copy.deepcopy(payload)
    pack = build_pack([_citation("current")], _SNAP, "abc123")
    out = attach_citations_to_evidence(payload, pack)
    assert out is not payload
    assert set(out) - set(payload) == set(ADDED_KEYS)
    assert payload == before  # input untouched
    assert isinstance(out["codeintel_citations"], list)
    assert out["codeintel_citations"][0]["path"] == "svc/orders.py"
    assert out["pack_freshness"] == "current"
    assert out["advisory_degraded"] is False


@pytest.mark.parametrize(("labels", "degraded"), [
    (["current"], False),
    (["stale"], True),
    (["unknown"], True),
    (["current", "stale"], True),
    ([], True),  # empty pack is unknown => degraded
])
def test_attach_degraded_follows_pack(
    advisory: None, labels: list[str], degraded: bool
) -> None:
    pack = build_pack([_citation(label) for label in labels], _SNAP, "abc123")
    out = attach_citations_to_evidence(_recon_payload(), pack)
    assert out["pack_freshness"] == pack.freshness.freshness
    assert out["advisory_degraded"] is degraded


def test_attach_never_touches_anchor_keys(advisory: None) -> None:
    payload = _recon_payload()
    pack = build_pack([_citation("stale")], _SNAP, "abc123")
    out = attach_citations_to_evidence(payload, pack)
    for key in ("target_file", "target_files", "anchor"):
        assert out[key] == payload[key]
    assert payload == _recon_payload()  # caller payload fully unchanged


def test_attach_is_pure_no_io(advisory: None) -> None:
    source = (_PACKAGE_DIR / "evidence_attach.py").read_text(encoding="utf-8")
    for probe in ("open(", "socket", "subprocess", "os.", "httpx", "requests"):
        assert probe not in source
    pack = build_pack([_citation("current")], _SNAP, "abc123")
    first = attach_citations_to_evidence(_recon_payload(), pack)
    second = attach_citations_to_evidence(_recon_payload(), pack)
    assert first == second


def test_new_modules_have_no_banned_stems() -> None:
    for name in ("context_packs.py", "evidence_attach.py"):
        source = (_PACKAGE_DIR / name).read_text(encoding="utf-8").lower()
        for stem in ("infer", "target", "adjacent"):
            assert stem not in source, f"{name} contains {stem!r}"
