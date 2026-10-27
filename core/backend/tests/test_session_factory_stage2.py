"""Hermetic Stage-2 tests: fencing + canonical paths + retry bound."""

import os

import pytest

from backend.session_factory.guards import (
    OwnershipLost,
    ResourceEscape,
    assert_ownership,
    canonical_resource,
    guarded_write,
    should_retry,
)
from backend.session_factory.locks import acquire_lock


class FakeCursor:
    def __init__(self, row=None, rowcount=0):
        self._row = row
        self.rowcount = rowcount

    def fetchone(self):
        return self._row


class FakeLocksTable:
    def __init__(self):
        self.owner: dict[str, str] = {}

    def execute(self, sql, params):
        first = sql.strip().split("\n")[0]
        if first.startswith("INSERT INTO aix_work_locks"):
            path, _, unit, _, _ = params
            if path in self.owner:
                return FakeCursor(row=None)
            self.owner[path] = unit
            return FakeCursor(row=(path,))
        if first.startswith("SELECT locked_by_unit_id"):
            (path,) = params
            unit = self.owner.get(path)
            return FakeCursor(row=(unit, "lane") if unit else None)
        raise AssertionError(first)


def test_takeover_then_stale_write_refused(tmp_path) -> None:
    t = FakeLocksTable()
    assert acquire_lock(
        t, file_path="a.py", graph_id="g", unit_id="u1",
        lane_id="l", ttl_seconds=60,
    ) is True
    t.owner["a.py"] = "u2"  # expiry takeover by another unit
    with pytest.raises(OwnershipLost):
        guarded_write(
            t, workspace_root=str(tmp_path), file_path="a.py",
            unit_id="u1", content="stale",
        )
    assert not os.path.exists(tmp_path / "a.py")


def test_owner_write_lands_on_canonical_path(tmp_path) -> None:
    t = FakeLocksTable()
    acquire_lock(
        t, file_path="sub/a.py", graph_id="g", unit_id="u1",
        lane_id="l", ttl_seconds=60,
    )
    os.makedirs(tmp_path / "sub")
    target = guarded_write(
        t, workspace_root=str(tmp_path), file_path="sub/a.py",
        unit_id="u1", content="fresh",
    )
    assert open(target).read() == "fresh"
    assert_ownership(t, file_path="sub/a.py", unit_id="u1")


def test_symlink_and_dotdot_escape_rejected(tmp_path) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    link = tmp_path / "ws" / "link.txt"
    link.parent.mkdir()
    os.symlink(outside, link)
    with pytest.raises(ResourceEscape):
        canonical_resource(str(tmp_path / "ws"), "link.txt")
    with pytest.raises(ResourceEscape):
        canonical_resource(str(tmp_path / "ws"), "../outside.txt")


def test_retry_bound() -> None:
    assert should_retry(attempt_count=0, max_attempts=3) is True
    assert should_retry(attempt_count=3, max_attempts=3) is False
