"""Work-lock arbitration + dependency-ready gate (Wave 15 concurrency proof).

Locks serialize same-file work across workers (see reviewer concurrency
point). Live ``aix_work_locks`` shape: PK ``(file_path)``,
``graph_id``, ``locked_by_unit_id``, ``locked_by_lane_id``,
``acquired_at``, ``expires_at`` — no RLS policies.

Acquire is one atomic statement: INSERT, else UPDATE only when the
existing row is expired (takeover). A live holder can never be stolen;
an expired holder loses the lock — writers must re-check ownership
(``check_owner``) before mutating, which answers "worker alive after
its lock expires".
"""

from __future__ import annotations

from typing import Any, Protocol


class LockConn(Protocol):
    """Minimal DBAPI surface this module needs (fakes in tests)."""

    def execute(self, sql: str, params: tuple[Any, ...]) -> Any: ...


ACQUIRE_SQL = """
INSERT INTO aix_work_locks
  (file_path, graph_id, locked_by_unit_id, locked_by_lane_id, expires_at)
VALUES (%s, %s, %s, %s, now() + make_interval(secs => %s))
ON CONFLICT (file_path) DO UPDATE SET
  graph_id = EXCLUDED.graph_id,
  locked_by_unit_id = EXCLUDED.locked_by_unit_id,
  locked_by_lane_id = EXCLUDED.locked_by_lane_id,
  acquired_at = now(),
  expires_at = EXCLUDED.expires_at
WHERE aix_work_locks.expires_at <= now()
RETURNING file_path
"""

RELEASE_SQL = """
DELETE FROM aix_work_locks
WHERE file_path = %s AND locked_by_unit_id = %s
"""

OWNER_SQL = """
SELECT locked_by_unit_id, locked_by_lane_id FROM aix_work_locks
WHERE file_path = %s
"""


def acquire_lock(
    conn: LockConn,
    *,
    file_path: str,
    graph_id: str,
    unit_id: str,
    lane_id: str,
    ttl_seconds: int,
) -> bool:
    """True when acquired or took over an expired lock; else False."""
    cur = conn.execute(
        ACQUIRE_SQL, (file_path, graph_id, unit_id, lane_id, ttl_seconds)
    )
    return cur.fetchone() is not None


def release_lock(conn: LockConn, *, file_path: str, unit_id: str) -> bool:
    """Release only our own lock (never another worker's)."""
    cur = conn.execute(RELEASE_SQL, (file_path, unit_id))
    return cur.rowcount == 1


def check_owner(
    conn: LockConn, *, file_path: str, unit_id: str
) -> bool:
    """True iff ``unit_id`` still owns the lock (fencing check)."""
    cur = conn.execute(OWNER_SQL, (file_path,))
    row = cur.fetchone()
    return row is not None and row[0] == unit_id


def ready_units(
    units: dict[str, str],
    dependencies: list[tuple[str, str]],
    *,
    ok_status: str = "SUCCEEDED",
) -> list[str]:
    """Units whose parents are all terminal-OK (dependency gate).

    ``units`` maps unit_id → status; ``dependencies`` holds
    (child, parent) pairs mirroring ``aix_work_unit_dependencies``.
    Pure: no I/O, unit-tested.
    """
    parents: dict[str, list[str]] = {}
    for child, parent in dependencies:
        parents.setdefault(child, []).append(parent)
    return sorted(
        uid
        for uid, status in units.items()
        if all(units.get(p) == ok_status for p in parents.get(uid, []))
    )
