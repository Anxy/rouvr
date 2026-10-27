"""Hermetic tests: lock arbitration over a fake PK table + ready gate."""

from datetime import datetime, timedelta, timezone

from backend.session_factory.locks import (
    acquire_lock,
    check_owner,
    ready_units,
    release_lock,
)


class FakeCursor:
    def __init__(self, row=None, rowcount=0):
        self._row = row
        self.rowcount = rowcount

    def fetchone(self):
        return self._row


class FakeLocksTable:
    """Emulates PK(file_path) + expires_at takeover semantics."""

    def __init__(self):
        self.rows: dict[str, dict] = {}
        self.now = datetime(2026, 9, 10, tzinfo=timezone.utc)

    def execute(self, sql, params):
        first = sql.strip().split("\n")[0]
        if first.startswith("INSERT INTO aix_work_locks"):
            path, graph, unit, lane, ttl = params
            cur = self.rows.get(path)
            if cur is None or cur["expires_at"] <= self.now:
                self.rows[path] = {
                    "graph_id": graph,
                    "unit": unit,
                    "lane": lane,
                    "expires_at": self.now + timedelta(seconds=ttl),
                }
                return FakeCursor(row=(path,))
            return FakeCursor(row=None)
        if first.startswith("DELETE FROM aix_work_locks"):
            path, unit = params
            cur = self.rows.get(path)
            if cur is not None and cur["unit"] == unit:
                del self.rows[path]
                return FakeCursor(rowcount=1)
            return FakeCursor(rowcount=0)
        if first.startswith("SELECT locked_by_unit_id"):
            (path,) = params
            cur = self.rows.get(path)
            if cur is None:
                return FakeCursor(row=None)
            return FakeCursor(row=(cur["unit"], cur["lane"]))
        raise AssertionError(f"unexpected SQL: {first}")


def acquire(table, path="f.py", unit="u1", lane="lane-a", ttl=60, graph="g"):
    return acquire_lock(
        table, file_path=path, graph_id=graph, unit_id=unit, lane_id=lane,
        ttl_seconds=ttl,
    )


def test_first_acquire_wins_second_fails() -> None:
    t = FakeLocksTable()
    assert acquire(t, unit="u1") is True
    assert acquire(t, unit="u2") is False
    assert check_owner(t, file_path="f.py", unit_id="u1") is True
    assert check_owner(t, file_path="f.py", unit_id="u2") is False


def test_expired_lock_takeover_and_stale_owner_fenced() -> None:
    t = FakeLocksTable()
    assert acquire(t, unit="u1", ttl=1) is True
    t.now += timedelta(seconds=5)
    assert acquire(t, unit="u2") is True
    assert check_owner(t, file_path="f.py", unit_id="u1") is False
    assert check_owner(t, file_path="f.py", unit_id="u2") is True


def test_release_only_own_lock() -> None:
    t = FakeLocksTable()
    assert acquire(t, unit="u1") is True
    assert release_lock(t, file_path="f.py", unit_id="u2") is False
    assert release_lock(t, file_path="f.py", unit_id="u1") is True
    assert acquire(t, unit="u2") is True


def test_ready_gate_waits_for_parents() -> None:
    units = {"a": "SUCCEEDED", "b": "RUNNING", "c": "PENDING"}
    deps = [("c", "a"), ("c", "b")]
    assert ready_units(units, deps) == ["a", "b"]
    units["b"] = "SUCCEEDED"
    assert ready_units(units, deps) == ["a", "b", "c"]


def test_ready_gate_unknown_parent_blocks() -> None:
    assert ready_units({"c": "PENDING"}, [("c", "missing")]) == []
