"""F11-P1: WorkGraph persistence — in-memory adapter + durable SQLite projection.

Postgres DDL lives in ``schema.py`` (ledger dual-model). SQLite proves restart
recovery without requiring a live Postgres in unit/integration tests.
Objective Cycle remains SoR via plan/ledger; this store is a projection.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from backend.aix_core.framework.scale.schema import (
    TABLE_WORK_DEPS,
    TABLE_WORK_GRAPHS,
    TABLE_WORK_LOCKS,
    TABLE_WORK_UNITS,
    ensure_sqlite_work_schema,
)
from backend.aix_core.framework.work.models import (
    UnitStatus,
    WorkContract,
    WorkGraph,
    WorkUnit,
)


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class StoredWorkUnitRecord(BaseModel):
    """Internal indexed relational projection of a single WorkUnit."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    unit_id: str
    graph_id: str
    work_id: str
    unit_index: int
    purpose: str
    target_scope: tuple[str, ...]
    contract: WorkContract
    actions_data: tuple[dict[str, Any], ...] = ()
    status: UnitStatus = UnitStatus.PENDING
    assigned_role: str = "executor"
    assigned_lane_id: str | None = None
    attempt_count: int = 0
    created_at: str = Field(default_factory=_utc_now_iso)
    updated_at: str = Field(default_factory=_utc_now_iso)


class WorkGraphSnapshot(BaseModel):
    """Lightweight metadata header for persisted WorkGraphs."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    graph_id: str
    work_id: str
    revision: int
    status: str = "ACTIVE"
    total_units: int
    completed_units: int = 0
    failed_units: int = 0
    created_at: str = Field(default_factory=_utc_now_iso)
    updated_at: str = Field(default_factory=_utc_now_iso)


class InMemoryWorkGraphStore:
    """Test/dev adapter — not durable across process restart."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._graphs: dict[str, WorkGraphSnapshot] = {}
        self._units: dict[str, dict[str, StoredWorkUnitRecord]] = {}
        self._dependencies: dict[str, dict[str, set[str]]] = {}
        self._dependents: dict[str, dict[str, set[str]]] = {}

    def save_work_graph(self, graph: WorkGraph) -> WorkGraphSnapshot:
        with self._lock:
            return self._save_unlocked(graph)

    def _save_unlocked(self, graph: WorkGraph) -> WorkGraphSnapshot:
        graph_id = graph.graph_id
        now_str = _utc_now_iso()
        unit_records: dict[str, StoredWorkUnitRecord] = {}
        deps_map: dict[str, set[str]] = {}
        dependents_map: dict[str, set[str]] = {}

        for unit in graph.units:
            rec = StoredWorkUnitRecord(
                unit_id=unit.unit_id,
                graph_id=graph_id,
                work_id=graph.work_id,
                unit_index=unit.unit_index,
                purpose=unit.purpose,
                target_scope=unit.target_scope,
                contract=unit.contract,
                actions_data=tuple(dict(a) for a in unit.actions) if unit.actions else (),
                status=unit.status,
                assigned_role=unit.contract.assigned_role,
                created_at=unit.created_at,
                updated_at=now_str,
            )
            unit_records[unit.unit_id] = rec
            deps_map[unit.unit_id] = set(unit.depends_on)
            for parent in unit.depends_on:
                dependents_map.setdefault(parent, set()).add(unit.unit_id)

        snapshot = WorkGraphSnapshot(
            graph_id=graph_id,
            work_id=graph.work_id,
            revision=graph.revision,
            status="ACTIVE",
            total_units=len(graph.units),
            completed_units=sum(1 for u in graph.units if u.status == UnitStatus.SUCCEEDED),
            failed_units=sum(1 for u in graph.units if u.status == UnitStatus.FAILED),
            created_at=graph.created_at,
            updated_at=now_str,
        )
        self._graphs[graph_id] = snapshot
        self._units[graph_id] = unit_records
        self._dependencies[graph_id] = deps_map
        self._dependents[graph_id] = dependents_map
        return snapshot

    def get_snapshot(self, graph_id: str) -> WorkGraphSnapshot | None:
        with self._lock:
            return self._graphs.get(graph_id)

    def get_unit(self, graph_id: str, unit_id: str) -> StoredWorkUnitRecord | None:
        with self._lock:
            return self._units.get(graph_id, {}).get(unit_id)

    def list_units(self, graph_id: str) -> tuple[StoredWorkUnitRecord, ...]:
        with self._lock:
            return tuple(
                sorted(self._units.get(graph_id, {}).values(), key=lambda u: u.unit_index)
            )

    def dependency_edges(self, graph_id: str) -> tuple[tuple[str, str], ...]:
        with self._lock:
            edges: list[tuple[str, str]] = []
            for child, parents in self._dependencies.get(graph_id, {}).items():
                for parent in parents:
                    edges.append((child, parent))
            return tuple(sorted(edges))

    def load_work_graph(self, graph_id: str) -> WorkGraph | None:
        with self._lock:
            snap = self._graphs.get(graph_id)
            unit_map = self._units.get(graph_id)
            deps = self._dependencies.get(graph_id, {})
            if snap is None or unit_map is None:
                return None
            units: list[WorkUnit] = []
            for rec in sorted(unit_map.values(), key=lambda u: u.unit_index):
                units.append(
                    WorkUnit(
                        unit_id=rec.unit_id,
                        work_id=rec.work_id,
                        unit_index=rec.unit_index,
                        purpose=rec.purpose,
                        depends_on=tuple(sorted(deps.get(rec.unit_id, set()))),
                        target_scope=rec.target_scope,
                        contract=rec.contract,
                        actions=rec.actions_data,
                        status=rec.status,
                        created_at=rec.created_at,
                    )
                )
            return WorkGraph(
                graph_id=snap.graph_id,
                work_id=snap.work_id,
                revision=snap.revision,
                units=tuple(units),
                created_at=snap.created_at,
            )

    def update_unit_status(
        self,
        graph_id: str,
        unit_id: str,
        status: UnitStatus,
        *,
        assigned_lane_id: str | None = None,
    ) -> StoredWorkUnitRecord:
        with self._lock:
            unit_map = self._units.get(graph_id)
            if not unit_map or unit_id not in unit_map:
                raise KeyError(f"Unit {unit_id} not found in graph {graph_id}")
            current = unit_map[unit_id]
            updated = current.model_copy(
                update={
                    "status": status,
                    "assigned_lane_id": (
                        assigned_lane_id if assigned_lane_id is not None else current.assigned_lane_id
                    ),
                    "attempt_count": current.attempt_count
                    + (1 if status == UnitStatus.RUNNING else 0),
                    "updated_at": _utc_now_iso(),
                }
            )
            unit_map[unit_id] = updated
            snap = self._graphs[graph_id]
            self._graphs[graph_id] = snap.model_copy(
                update={
                    "completed_units": sum(
                        1 for u in unit_map.values() if u.status == UnitStatus.SUCCEEDED
                    ),
                    "failed_units": sum(
                        1 for u in unit_map.values() if u.status == UnitStatus.FAILED
                    ),
                    "updated_at": _utc_now_iso(),
                }
            )
            return updated

    def query_ready_wavefront(
        self,
        graph_id: str,
        *,
        limit: int = 100,
        exclude_locked_paths: set[str] | None = None,
    ) -> tuple[StoredWorkUnitRecord, ...]:
        with self._lock:
            unit_map = self._units.get(graph_id, {})
            deps_map = self._dependencies.get(graph_id, {})
            exclude_paths = exclude_locked_paths or set()
            ready: list[StoredWorkUnitRecord] = []
            for unit in sorted(unit_map.values(), key=lambda u: u.unit_index):
                if unit.status != UnitStatus.PENDING:
                    continue
                if any(p in exclude_paths for p in unit.target_scope):
                    continue
                parents = deps_map.get(unit.unit_id, set())
                if all(
                    unit_map.get(p_id) is not None and unit_map[p_id].status == UnitStatus.SUCCEEDED
                    for p_id in parents
                ):
                    ready.append(unit)
                    if len(ready) >= limit:
                        break
            return tuple(ready)

    def count_units_by_status(self, graph_id: str) -> dict[str, int]:
        with self._lock:
            unit_map = self._units.get(graph_id, {})
            counts: dict[str, int] = {}
            for u in unit_map.values():
                counts[u.status.value] = counts.get(u.status.value, 0) + 1
            return counts

    def dependents_of(self, graph_id: str, unit_id: str) -> frozenset[str]:
        with self._lock:
            return frozenset(self._dependents.get(graph_id, {}).get(unit_id, set()))

    def all_dependents_map(self, graph_id: str) -> dict[str, set[str]]:
        with self._lock:
            return {k: set(v) for k, v in self._dependents.get(graph_id, {}).items()}


class DurableWorkGraphStore(InMemoryWorkGraphStore):
    """SQLite-backed durable projection — survives process restart."""

    def __init__(self, db_path: str | Path) -> None:
        super().__init__()
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        ensure_sqlite_work_schema(self._conn)
        self._hydrate_from_disk()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _hydrate_from_disk(self) -> None:
        rows = self._conn.execute(f"SELECT graph_id FROM {TABLE_WORK_GRAPHS}").fetchall()
        for row in rows:
            self._load_graph_into_memory(row["graph_id"])

    def _load_graph_into_memory(self, graph_id: str) -> None:
        g = self._conn.execute(
            f"SELECT * FROM {TABLE_WORK_GRAPHS} WHERE graph_id = ?", (graph_id,)
        ).fetchone()
        if g is None:
            return
        units = self._conn.execute(
            f"SELECT * FROM {TABLE_WORK_UNITS} WHERE graph_id = ? ORDER BY unit_index",
            (graph_id,),
        ).fetchall()
        deps = self._conn.execute(
            f"SELECT child_unit_id, parent_unit_id FROM {TABLE_WORK_DEPS} WHERE graph_id = ?",
            (graph_id,),
        ).fetchall()
        work_units: list[WorkUnit] = []
        dep_map: dict[str, list[str]] = {}
        for d in deps:
            dep_map.setdefault(d["child_unit_id"], []).append(d["parent_unit_id"])
        for u in units:
            contract = WorkContract.model_validate(json.loads(u["contract"]))
            work_units.append(
                WorkUnit(
                    unit_id=u["unit_id"],
                    work_id=u["work_id"],
                    unit_index=u["unit_index"],
                    purpose=u["purpose"],
                    depends_on=tuple(dep_map.get(u["unit_id"], ())),
                    target_scope=tuple(json.loads(u["target_scope"])),
                    contract=contract,
                    actions=tuple(json.loads(u["actions"] or "[]")),
                    status=UnitStatus(u["status"]),
                    created_at=u["created_at"],
                )
            )
        graph = WorkGraph(
            graph_id=g["graph_id"],
            work_id=g["work_id"],
            revision=g["revision"],
            units=tuple(work_units),
            created_at=g["created_at"],
        )
        super()._save_unlocked(graph)
        # Restore runtime statuses / lanes from disk (may differ from WorkUnit.status seed)
        for u in units:
            if u["status"] != UnitStatus.PENDING.value or u["assigned_lane_id"]:
                try:
                    super().update_unit_status(
                        graph_id,
                        u["unit_id"],
                        UnitStatus(u["status"]),
                        assigned_lane_id=u["assigned_lane_id"],
                    )
                except KeyError:
                    pass

    def save_work_graph(self, graph: WorkGraph) -> WorkGraphSnapshot:
        with self._lock:
            snap = super()._save_unlocked(graph)
            self._persist_graph(graph, snap)
            return snap

    def _persist_graph(self, graph: WorkGraph, snap: WorkGraphSnapshot) -> None:
        now = _utc_now_iso()
        self._conn.execute(
            f"""
            INSERT INTO {TABLE_WORK_GRAPHS}
              (graph_id, work_id, revision, status, total_units, completed_units,
               failed_units, payload, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(graph_id) DO UPDATE SET
              work_id=excluded.work_id,
              revision=excluded.revision,
              status=excluded.status,
              total_units=excluded.total_units,
              completed_units=excluded.completed_units,
              failed_units=excluded.failed_units,
              updated_at=excluded.updated_at
            """,
            (
                snap.graph_id,
                snap.work_id,
                snap.revision,
                snap.status,
                snap.total_units,
                snap.completed_units,
                snap.failed_units,
                "{}",
                snap.created_at,
                snap.updated_at,
            ),
        )
        self._conn.execute(f"DELETE FROM {TABLE_WORK_DEPS} WHERE graph_id = ?", (graph.graph_id,))
        self._conn.execute(f"DELETE FROM {TABLE_WORK_UNITS} WHERE graph_id = ?", (graph.graph_id,))
        for unit in graph.units:
            rec = self._units[graph.graph_id][unit.unit_id]
            self._conn.execute(
                f"""
                INSERT INTO {TABLE_WORK_UNITS}
                  (unit_id, graph_id, work_id, unit_index, purpose, target_scope, contract,
                   actions, status, assigned_role, assigned_lane_id, attempt_count,
                   created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    rec.unit_id,
                    rec.graph_id,
                    rec.work_id,
                    rec.unit_index,
                    rec.purpose,
                    json.dumps(list(rec.target_scope)),
                    rec.contract.model_dump_json(),
                    json.dumps(list(rec.actions_data)),
                    rec.status.value,
                    rec.assigned_role,
                    rec.assigned_lane_id,
                    rec.attempt_count,
                    rec.created_at,
                    rec.updated_at,
                ),
            )
            for parent in unit.depends_on:
                self._conn.execute(
                    f"""
                    INSERT INTO {TABLE_WORK_DEPS}
                      (graph_id, child_unit_id, parent_unit_id, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (graph.graph_id, unit.unit_id, parent, now),
                )
        self._conn.commit()

    def update_unit_status(
        self,
        graph_id: str,
        unit_id: str,
        status: UnitStatus,
        *,
        assigned_lane_id: str | None = None,
    ) -> StoredWorkUnitRecord:
        with self._lock:
            updated = super().update_unit_status(
                graph_id, unit_id, status, assigned_lane_id=assigned_lane_id
            )
            self._conn.execute(
                f"""
                UPDATE {TABLE_WORK_UNITS}
                SET status = ?, assigned_lane_id = ?, attempt_count = ?, updated_at = ?
                WHERE graph_id = ? AND unit_id = ?
                """,
                (
                    updated.status.value,
                    updated.assigned_lane_id,
                    updated.attempt_count,
                    updated.updated_at,
                    graph_id,
                    unit_id,
                ),
            )
            snap = self._graphs[graph_id]
            self._conn.execute(
                f"""
                UPDATE {TABLE_WORK_GRAPHS}
                SET completed_units = ?, failed_units = ?, updated_at = ?
                WHERE graph_id = ?
                """,
                (snap.completed_units, snap.failed_units, snap.updated_at, graph_id),
            )
            self._conn.commit()
            return updated

    def acquire_path_lock(
        self,
        *,
        file_path: str,
        graph_id: str,
        unit_id: str,
        lane_id: str,
        expires_at: str,
    ) -> None:
        with self._lock:
            existing = self._conn.execute(
                f"SELECT locked_by_unit_id FROM {TABLE_WORK_LOCKS} WHERE file_path = ?",
                (file_path,),
            ).fetchone()
            if existing and existing["locked_by_unit_id"] != unit_id:
                raise RuntimeError(f"path locked: {file_path}")
            self._conn.execute(
                f"""
                INSERT INTO {TABLE_WORK_LOCKS}
                  (file_path, graph_id, locked_by_unit_id, locked_by_lane_id, acquired_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(file_path) DO UPDATE SET
                  locked_by_unit_id=excluded.locked_by_unit_id,
                  locked_by_lane_id=excluded.locked_by_lane_id,
                  acquired_at=excluded.acquired_at,
                  expires_at=excluded.expires_at
                """,
                (file_path, graph_id, unit_id, lane_id, _utc_now_iso(), expires_at),
            )
            self._conn.commit()

    def release_path_locks_for_unit(self, unit_id: str) -> None:
        with self._lock:
            self._conn.execute(
                f"DELETE FROM {TABLE_WORK_LOCKS} WHERE locked_by_unit_id = ?", (unit_id,)
            )
            self._conn.commit()

    def save_hibernation_record(self, cycle_id: str, record_json: str, *, graph_id: str, work_id: str, status: str) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO aix_work_hibernation
                  (cycle_id, graph_id, work_id, status, record_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(cycle_id) DO UPDATE SET
                  status=excluded.status,
                  record_json=excluded.record_json,
                  updated_at=excluded.updated_at
                """,
                (cycle_id, graph_id, work_id, status, record_json, _utc_now_iso()),
            )
            self._conn.commit()

    def load_hibernation_record(self, cycle_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT record_json FROM aix_work_hibernation WHERE cycle_id = ?",
                (cycle_id,),
            ).fetchone()
            if row is None:
                return None
            return json.loads(row["record_json"])


class WorkGraphStore(DurableWorkGraphStore):
    """Canonical store: durable SQLite by default; pass db_path=':memory:' for ephemeral."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        if db_path is None:
            # Ephemeral in-memory adapter for tests / flag-on without a configured path.
            InMemoryWorkGraphStore.__init__(self)
            self.db_path = None
            self._conn = None
            self._durable = False
            self._hibernation_mem: dict[str, str] = {}
            return
        self._durable = True
        DurableWorkGraphStore.__init__(self, db_path)

    def save_work_graph(self, graph: WorkGraph) -> WorkGraphSnapshot:
        if not getattr(self, "_durable", False):
            return InMemoryWorkGraphStore.save_work_graph(self, graph)
        return DurableWorkGraphStore.save_work_graph(self, graph)

    def update_unit_status(
        self,
        graph_id: str,
        unit_id: str,
        status: UnitStatus,
        *,
        assigned_lane_id: str | None = None,
    ) -> StoredWorkUnitRecord:
        if not getattr(self, "_durable", False):
            return InMemoryWorkGraphStore.update_unit_status(
                self, graph_id, unit_id, status, assigned_lane_id=assigned_lane_id
            )
        return DurableWorkGraphStore.update_unit_status(
            self, graph_id, unit_id, status, assigned_lane_id=assigned_lane_id
        )

    def save_hibernation_record(
        self,
        cycle_id: str,
        record_json: str,
        *,
        graph_id: str,
        work_id: str,
        status: str,
    ) -> None:
        if not getattr(self, "_durable", False) or self._conn is None:
            self._hibernation_mem[cycle_id] = record_json
            return
        DurableWorkGraphStore.save_hibernation_record(
            self, cycle_id, record_json, graph_id=graph_id, work_id=work_id, status=status
        )

    def load_hibernation_record(self, cycle_id: str) -> dict[str, Any] | None:
        if not getattr(self, "_durable", False) or self._conn is None:
            raw = getattr(self, "_hibernation_mem", {}).get(cycle_id)
            return json.loads(raw) if raw else None
        return DurableWorkGraphStore.load_hibernation_record(self, cycle_id)


__all__ = [
    "DurableWorkGraphStore",
    "InMemoryWorkGraphStore",
    "StoredWorkUnitRecord",
    "WorkGraphSnapshot",
    "WorkGraphStore",
]
