"""AIX Feature 12 (Packet 04) — Durable Gap Framework (GapV1).

Verifier creates gaps; executor cannot close gaps (only verifier can close).
GapStore persists to Postgres when a connection is provided, otherwise to a
JSON file (AC-25 restart survival for local/tests).
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from inspect import isawaitable
from pathlib import Path
from typing import Any, AsyncIterator, Literal

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

GapCategory = Literal[
    "OBJECTIVE_DEVIATION",
    "RUNTIME_FAILURE",
    "CONTRACT_VIOLATION",
    "SYNTAX_ERROR",
    "TEST_FAILURE",
    "REGRESSION",
]

GapSeverity = Literal["BLOCKER", "HIGH", "MEDIUM", "LOW"]

GapStatus = Literal[
    "OPEN",
    "IN_REPAIR",
    "REVERIFIED",
    "CLOSED",
    "ESCALATED",
]

GAPS_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS gaps (
  id TEXT PRIMARY KEY,
  objective_id TEXT NOT NULL,
  verification_id TEXT,
  status TEXT NOT NULL,
  category TEXT,
  expected TEXT,
  observed TEXT,
  evidence JSONB,
  severity TEXT,
  ov_id TEXT,
  repair_attempt_count INT DEFAULT 0,
  payload JSONB,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_gaps_objective_id ON gaps(objective_id);
CREATE INDEX IF NOT EXISTS idx_gaps_status ON gaps(status);
"""


class GapV1(BaseModel):
    gap_id: str
    objective_id: str
    verification_id: str
    ov_id: str = ""
    category: GapCategory
    expected: str
    observed: str
    evidence: list[str] = Field(default_factory=list)
    severity: GapSeverity = "BLOCKER"
    status: GapStatus = "OPEN"
    provenance: dict[str, Any] = Field(default_factory=dict)
    repair_attempt_count: int = 0
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    updated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def is_blocking(self) -> bool:
        return self.severity == "BLOCKER" and self.status in ("OPEN", "IN_REPAIR", "ESCALATED")

    def transition_to_in_repair(self) -> None:
        if self.status != "OPEN":
            return
        self.status = "IN_REPAIR"
        self.repair_attempt_count += 1
        self.updated_at = datetime.now(timezone.utc).isoformat()

    def close_by_verifier(self, verification_id: str) -> None:
        """Only verifier authority can close a gap."""
        self.status = "CLOSED"
        self.updated_at = datetime.now(timezone.utc).isoformat()
        self.provenance["closed_by_verification_id"] = verification_id

    def escalate(self, reason: str = "") -> None:
        self.status = "ESCALATED"
        self.updated_at = datetime.now(timezone.utc).isoformat()
        if reason:
            self.provenance["escalation_reason"] = reason


async def ensure_gaps_schema(conn: Any) -> None:
    """Create gaps table on a Postgres asyncpg connection."""
    for stmt in GAPS_TABLE_DDL.strip().split(";"):
        s = stmt.strip()
        if s:
            await conn.execute(s)


class GapStore:
    """Durable gap storage (Postgres preferred; JSON file fallback for AC-25 tests)."""

    def __init__(
        self,
        *,
        persist_path: Path | str | None = None,
        pg_conn: Any | None = None,
        pg_pool: Any | None = None,
        database_url: str | None = None,
    ) -> None:
        self._gaps: dict[str, GapV1] = {}
        self._persist_path = Path(persist_path) if persist_path else None
        self._persist_dir: Path | None = None
        self._single_file: Path | None = None
        if self._persist_path is not None:
            if self._persist_path.suffix == ".json":
                self._single_file = self._persist_path
            else:
                self._persist_dir = self._persist_path
                self._persist_dir.mkdir(parents=True, exist_ok=True)
        self._pg_conn = pg_conn or pg_pool
        self._database_url = database_url
        self._pg_lock = asyncio.Lock()
        if self._single_file and self._single_file.exists():
            self.load_from_disk()
        elif self._persist_dir and self._persist_dir.exists():
            self._load_all_objective_files()
        if self._pg_conn is None and self._persist_path is None:
            logger.warning(
                "GapStore running in-memory only; gaps will not survive restart"
            )

    @staticmethod
    def build_report(
        objective_id: str,
        blocking_gaps: list[GapV1],
    ) -> list[Any]:
        """Build planner-facing gap reports for bounded replanning."""
        from backend.aix_core.runtime.planner.incremental_replan import GapReport

        reports: list[GapReport] = []
        for gap in blocking_gaps:
            if gap.objective_id != objective_id:
                continue
            reports.append(
                GapReport(
                    gap_id=gap.gap_id,
                    objective_id=gap.objective_id,
                    category=gap.category,
                    description=f"{gap.expected} vs {gap.observed}",
                    expected=gap.expected,
                    observed=gap.observed,
                    evidence_locations=tuple(gap.evidence),
                    target_scope=tuple(
                        str(p).strip().replace("\\", "/")
                        for p in (gap.evidence or ())
                        if str(p or "").strip() and not str(p).endswith("/")
                    ),
                    suggested_fix=f"Close {gap.ov_id or gap.category} defect",
                )
            )
        return reports

    def put_gap(self, gap: GapV1) -> None:
        self._gaps[gap.gap_id] = gap
        self._flush()

    def get_gap(self, gap_id: str) -> GapV1 | None:
        return self._gaps.get(gap_id)

    def get_gaps_for_objective(self, objective_id: str) -> list[GapV1]:
        return [g for g in self._gaps.values() if g.objective_id == objective_id]

    def get_open_blocking_gaps(self, objective_id: str) -> list[GapV1]:
        return [g for g in self.get_gaps_for_objective(objective_id) if g.is_blocking()]

    def count_blocking_gaps(self, objective_id: str) -> int:
        return len(self.get_open_blocking_gaps(objective_id))

    def count_open(self, objective_id: str) -> int:
        return len(
            [
                g
                for g in self.get_gaps_for_objective(objective_id)
                if g.status in ("OPEN", "IN_REPAIR")
            ]
        )

    def save(self) -> None:
        """Explicit flush for callers."""
        self._flush()

    def load(self, objective_id: str | None = None) -> list[GapV1]:
        """Reload from durable backend; optionally filter by objective."""
        if (self._single_file and self._single_file.exists()) or (
            self._persist_dir and self._persist_dir.exists()
        ):
            self.load_from_disk()
        if objective_id is None:
            return list(self._gaps.values())
        return self.get_gaps_for_objective(objective_id)

    def load_from_disk(self) -> None:
        if self._single_file and self._single_file.exists():
            raw = json.loads(self._single_file.read_text(encoding="utf-8"))
            self._gaps = {gid: GapV1.model_validate(payload) for gid, payload in raw.items()}
            return
        self._load_all_objective_files()

    def _load_all_objective_files(self) -> None:
        if self._persist_dir is None:
            return
        gaps_dir = self._persist_dir / "gaps"
        if not gaps_dir.exists():
            return
        for path in gaps_dir.glob("*.json"):
            raw = json.loads(path.read_text(encoding="utf-8"))
            for gid, payload in raw.items():
                self._gaps[gid] = GapV1.model_validate(payload)

    def _flush(self) -> None:
        if self._single_file is not None:
            self._single_file.parent.mkdir(parents=True, exist_ok=True)
            payload = {gid: gap.model_dump() for gid, gap in self._gaps.items()}
            self._single_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        elif self._persist_dir is not None:
            by_objective: dict[str, dict[str, dict[str, Any]]] = {}
            for gid, gap in self._gaps.items():
                by_objective.setdefault(gap.objective_id, {})[gid] = gap.model_dump()
            gaps_dir = self._persist_dir / "gaps"
            gaps_dir.mkdir(parents=True, exist_ok=True)
            for objective_id, payload in by_objective.items():
                out = gaps_dir / f"{objective_id}.json"
                out.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    @asynccontextmanager
    async def _pg_connection(self) -> AsyncIterator[Any]:
        """Yield one asyncpg connection; pool acquire or locked single conn."""
        if self._pg_conn is not None:
            try:
                import asyncpg

                if isinstance(self._pg_conn, asyncpg.Pool):
                    async with self._pg_conn.acquire() as conn:
                        yield conn
                    return
            except ImportError:
                pass
            acquire_fn = getattr(self._pg_conn, "acquire", None)
            if callable(acquire_fn):
                ctx = acquire_fn()
                if not isawaitable(ctx) and hasattr(ctx, "__aenter__"):
                    async with ctx as conn:
                        yield conn
                    return
            async with self._pg_lock:
                yield self._pg_conn
            return
        if self._database_url:
            import asyncpg

            conn = await asyncpg.connect(self._database_url)
            try:
                yield conn
            finally:
                await conn.close()
            return
        raise RuntimeError("PG_CONN_REQUIRED")

    async def save_pg(self, gap: GapV1) -> None:
        async with self._pg_connection() as conn:
            await ensure_gaps_schema(conn)

            def _ts(value: str | datetime) -> datetime:
                if isinstance(value, datetime):
                    return value
                text = str(value).replace("Z", "+00:00")
                return datetime.fromisoformat(text)

            await conn.execute(
                """
                INSERT INTO gaps (
                  id, objective_id, verification_id, status, category, expected, observed,
                  evidence, severity, ov_id, repair_attempt_count, payload, created_at, updated_at
                ) VALUES (
                  $1,$2,$3,$4,$5,$6,$7,$8::jsonb,$9,$10,$11,$12::jsonb,$13::timestamptz,$14::timestamptz
                )
                ON CONFLICT (id) DO UPDATE SET
                  status = EXCLUDED.status,
                  expected = EXCLUDED.expected,
                  observed = EXCLUDED.observed,
                  evidence = EXCLUDED.evidence,
                  severity = EXCLUDED.severity,
                  repair_attempt_count = EXCLUDED.repair_attempt_count,
                  payload = EXCLUDED.payload,
                  updated_at = EXCLUDED.updated_at
                """,
                gap.gap_id,
                gap.objective_id,
                gap.verification_id,
                gap.status,
                gap.category,
                gap.expected,
                gap.observed,
                json.dumps(gap.evidence),
                gap.severity,
                gap.ov_id,
                gap.repair_attempt_count,
                json.dumps(gap.model_dump()),
                _ts(gap.created_at),
                _ts(gap.updated_at),
            )
        self._gaps[gap.gap_id] = gap

    async def load_pg(self, objective_id: str) -> list[GapV1]:
        async with self._pg_connection() as conn:
            await ensure_gaps_schema(conn)
            rows = await conn.fetch(
                "SELECT payload FROM gaps WHERE objective_id = $1",
                objective_id,
            )
        gaps: list[GapV1] = []
        for row in rows:
            payload = row["payload"]
            if isinstance(payload, str):
                payload = json.loads(payload)
            gap = GapV1.model_validate(payload)
            self._gaps[gap.gap_id] = gap
            gaps.append(gap)
        return gaps


__all__ = [
    "GapCategory",
    "GapSeverity",
    "GapStatus",
    "GapV1",
    "GapStore",
    "GAPS_TABLE_DDL",
    "ensure_gaps_schema",
]
