"""Wave 4 Agent3: snapshot store + freshness proof tests (AIX lane)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from backend.codeintel_v2 import freshness as freshness_mod
from backend.codeintel_v2 import snapshots as snapshots_mod
from backend.codeintel_v2.freshness import Freshness, check_freshness, workspace_head
from backend.codeintel_v2.snapshots import (
    CodeIntelSnapshot,
    InMemorySnapshotStore,
    SnapshotStore,
    codeintel_posture,
)


def _init_repo(path: Path) -> str:
    (path / "note.txt").write_text("agent3\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(
        [
            "git", "-c", "user.name=agent3", "-c", "user.email=agent3@test",
            "commit", "-qm", "init",
        ],
        cwd=path,
        check=True,
    )
    head = workspace_head(path)
    assert head
    return head


def test_head_matches_snapshot_is_current(tmp_path: Path) -> None:
    head = _init_repo(tmp_path)
    snap = CodeIntelSnapshot(repo_id="r", commit_sha=head, snapshot_id="s1")
    result = check_freshness(snap, head)
    assert isinstance(result, Freshness)
    assert result.freshness == "current"
    assert result.reason.strip()
    assert result.workspace_head == head


def test_head_differs_is_stale_with_explicit_reason(tmp_path: Path) -> None:
    head = _init_repo(tmp_path)
    other = "0" * 40
    assert other != head
    snap = CodeIntelSnapshot(repo_id="r", commit_sha=other, snapshot_id="s1")
    result = check_freshness(snap, head)
    assert result.freshness == "stale"
    assert result.reason.strip()
    assert head in result.reason
    assert other in result.reason


def test_missing_head_is_unknown_classified_failure() -> None:
    snap = CodeIntelSnapshot(repo_id="r", commit_sha="a" * 40, snapshot_id="s1")
    for missing in (None, "", "   "):
        result = check_freshness(snap, missing)
        assert result.freshness == "unknown"
        assert result.freshness != "current"
        assert result.reason.strip()


def test_missing_snapshot_sha_is_unknown_classified_failure() -> None:
    for bad in (
        None,
        object(),
        {"repo_id": "r"},
        CodeIntelSnapshot(repo_id="r", commit_sha="", snapshot_id="s1"),
        CodeIntelSnapshot(repo_id="", commit_sha="a" * 40, snapshot_id="s1"),
    ):
        result = check_freshness(bad, "b" * 40)
        assert result.freshness == "unknown"
        assert result.freshness != "current"
        assert result.reason.strip()


def test_check_freshness_never_raises() -> None:
    for snap, head in (
        (None, None),
        (object(), object()),
        ({"nope": True}, ["x"]),
        (CodeIntelSnapshot(repo_id="r", commit_sha="a" * 40, snapshot_id="s"), None),
    ):
        result = check_freshness(snap, head)  # must not raise
        assert result.freshness in ("current", "stale", "unknown")
        assert result.reason.strip()


def test_duplicate_put_returns_existing_never_duplicates() -> None:
    store = InMemorySnapshotStore()
    assert isinstance(store, SnapshotStore)
    first = store.put(CodeIntelSnapshot(repo_id="r", commit_sha="c", snapshot_id="s1"))
    second = store.put(CodeIntelSnapshot(repo_id="r", commit_sha="c", snapshot_id="s2"))
    assert second.snapshot_id == "s1"
    assert first.snapshot_id == "s1"
    assert len(store) == 1
    assert store.get("r", "c") is not None
    assert store.get("r", "c").snapshot_id == "s1"  # type: ignore[union-attr]
    assert store.get("r", "other") is None


def test_get_or_create_dedups() -> None:
    store = InMemorySnapshotStore()
    one = store.get_or_create("r", "c")
    two = store.get_or_create("r", "c")
    assert one.snapshot_id == two.snapshot_id
    assert len(store) == 1


def test_workspace_head_helper(tmp_path: Path) -> None:
    assert workspace_head(tmp_path) is None  # not a repo
    head = _init_repo(tmp_path)
    assert workspace_head(tmp_path) == head
    assert workspace_head(tmp_path / "missing-dir") is None


def test_snapshot_fields_match_frozen_spec() -> None:
    assert set(CodeIntelSnapshot.model_fields) == {"repo_id", "commit_sha", "snapshot_id"}


def test_codeintel_flag_honored_not_redeclared() -> None:
    assert codeintel_posture() in ("off", "advisory")
    assert "AIX_CODEINTEL" not in snapshots_mod.__dict__
    assert "AIX_CODEINTEL" not in freshness_mod.__dict__
    source = Path(snapshots_mod.__file__).read_text(encoding="utf-8")
    assert "backend.hyeve_aix_service.config" in source


FORBIDDEN_EXPORT_SUBSTRINGS = ("target", "infer", "symbol")


def test_module_exports_no_target_or_inference_symbols() -> None:
    for mod in (snapshots_mod, freshness_mod):
        public = [name for name in dir(mod) if not name.startswith("_")]
        hits = [n for n in public if any(s in n.lower() for s in FORBIDDEN_EXPORT_SUBSTRINGS)]
        assert hits == [], f"{mod.__name__} leaks forbidden exports: {hits}"
        source = Path(mod.__file__).read_text(encoding="utf-8").lower()
        src_hits = [s for s in FORBIDDEN_EXPORT_SUBSTRINGS if s in source]
        assert src_hits == [], f"{mod.__name__} source mentions {src_hits}"
