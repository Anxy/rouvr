"""PostgreSQL ActivationStore using the canonical short-connection asyncpg pattern."""

from __future__ import annotations
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Awaitable
import asyncpg  # type: ignore[import-untyped]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS aix_session_activations (
 activation_id text PRIMARY KEY, binding jsonb NOT NULL, status text NOT NULL,
 result jsonb, max_repairs integer NOT NULL CHECK (max_repairs BETWEEN 0 AND 10),
 repair_count integer NOT NULL DEFAULT 0 CHECK (repair_count >= 0), updated_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS aix_session_verdicts (
 verdict_id text PRIMARY KEY, activation_id text NOT NULL REFERENCES aix_session_activations(activation_id), record jsonb NOT NULL, repair_number integer, created_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS aix_session_results (
 result_id text PRIMARY KEY, activation_id text NOT NULL REFERENCES aix_session_activations(activation_id), record jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS aix_session_releases (
 release_id text PRIMARY KEY, record jsonb NOT NULL, state text NOT NULL CHECK (state IN ('claimed','retryable','completed')),
 lease_owner text, lease_token text, lease_expires_at timestamptz, result jsonb, updated_at timestamptz NOT NULL DEFAULT now());
ALTER TABLE aix_session_releases ADD COLUMN IF NOT EXISTS lease_token text;
CREATE INDEX IF NOT EXISTS aix_session_activations_attempt_idx ON aix_session_activations (((binding->'workhub_binding'->>'workhub_attempt_id'))) WHERE (binding ? 'workhub_binding');
CREATE TABLE IF NOT EXISTS aix_attempt_owners (
 tenant_id text NOT NULL, project_id text NOT NULL, workhub_attempt_id text NOT NULL,
 activation_id text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY (tenant_id, project_id, workhub_attempt_id));
"""


class ActivationStoreCorrupt(RuntimeError):
    pass


class ActivationStoreUnavailable(RuntimeError):
    pass


def _obj(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, dict):
        raise ActivationStoreCorrupt("ACTIVATION_ROW_CORRUPT")
    return dict(value)


_RESULT_CORE_KEYS = (
    "tenant_id", "activation_id", "result_id", "worker_id", "status",
    "evidence_ref",
)


def _result_core(record: dict[str, Any]) -> dict[str, Any]:
    return {key: record.get(key) for key in _RESULT_CORE_KEYS}


def _result_round(record: dict[str, Any]) -> int | None:
    value = record.get("repair_round")
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _result_binding(record: dict[str, Any]) -> tuple[int | None, str]:
    number = record.get("repair_number")
    if isinstance(number, bool) or not isinstance(number, int) or number < 1:
        number = None
    verdict = record.get("repair_verdict_id")
    if not isinstance(verdict, str):
        verdict = ""
    return (number, verdict)


def _repair_release_id(activation_id: str, repair_number: int) -> str:
    return f"{activation_id}:repair:{int(repair_number)}"


class PostgresActivationStore:
    def __init__(
        self,
        database_url: str,
        *,
        lease_owner: str,
        lease_seconds: int = 30,
        connect: Callable[[], Awaitable[Any]] | None = None,
    ) -> None:
        if not database_url or not lease_owner or lease_seconds < 1:
            raise ValueError("ACTIVATION_STORE_CONFIG_INVALID")
        self._url = database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
        self._owner = lease_owner
        self._lease = lease_seconds
        self._incarnation = uuid.uuid4().hex
        self._connect_override = connect

    async def _connect(self):
        try:
            return await (
                self._connect_override()
                if self._connect_override
                else asyncpg.connect(self._url, timeout=5, command_timeout=5)
            )
        except Exception as exc:
            raise ActivationStoreUnavailable("ACTIVATION_STORE_UNAVAILABLE") from exc

    async def initialize(self) -> None:
        conn = await self._connect()
        try:
            await conn.execute(_SCHEMA)
        finally:
            await conn.close()

    async def claim_activation(self, activation_id: str, record: dict[str, Any]):
        conn = await self._connect()
        try:
            async with conn.transaction():
                inserted = await conn.fetchval(
                    "INSERT INTO aix_session_activations(activation_id,binding,status,max_repairs) VALUES($1,$2::jsonb,'STARTING',$3) ON CONFLICT DO NOTHING RETURNING true",
                    activation_id,
                    json.dumps(record, sort_keys=True),
                    record["max_repairs"],
                )
                row = await conn.fetchrow(
                    "SELECT binding,status,result FROM aix_session_activations WHERE activation_id=$1",
                    activation_id,
                )
            if row is None:
                raise ActivationStoreCorrupt("ACTIVATION_ROW_MISSING")
            current = _obj(row["binding"])
            current["status"] = row["status"]
            if row["result"] is not None:
                current["temporal_result"] = _obj(row["result"])
            return bool(inserted), current
        finally:
            await conn.close()

    async def get_activation(self, activation_id: str):
        conn = await self._connect()
        try:
            row = await conn.fetchrow(
                "SELECT binding,status,result FROM aix_session_activations WHERE activation_id=$1",
                activation_id,
            )
        finally:
            await conn.close()
        if row is None:
            return None
        current = _obj(row["binding"])
        current["status"] = row["status"]
        if row["result"] is not None:
            current["temporal_result"] = _obj(row["result"])
        return current

    async def claim_attempt_owner(
        self,
        tenant_id: str,
        project_id: str,
        workhub_attempt_id: str,
        activation_id: str,
    ):
        """First-wins per-attempt activation ownership (lane-ops #23 R4).

        Returns (owned, owner_activation_id): True when this caller won
        or already owns the attempt. A loser learns the owner without
        inserting anything, so refused conflicts never poison recovery.
        """
        conn = await self._connect()
        try:
            async with conn.transaction():
                await conn.execute(
                    "INSERT INTO aix_attempt_owners(tenant_id,project_id,workhub_attempt_id,activation_id) VALUES($1,$2,$3,$4) ON CONFLICT DO NOTHING",
                    tenant_id,
                    project_id,
                    workhub_attempt_id,
                    activation_id,
                )
                row = await conn.fetchrow(
                    "SELECT activation_id FROM aix_attempt_owners WHERE tenant_id=$1 AND project_id=$2 AND workhub_attempt_id=$3",
                    tenant_id,
                    project_id,
                    workhub_attempt_id,
                )
            if row is None:
                raise ActivationStoreCorrupt("ATTEMPT_OWNER_MISSING")
            owner = str(row["activation_id"])
            return (owner == activation_id, owner)
        finally:
            await conn.close()

    async def find_attempt_activation(
        self, tenant_id: str, project_id: str, workhub_attempt_id: str
    ):
        conn = await self._connect()
        try:
            rows = await conn.fetch(
                "SELECT activation_id FROM aix_session_activations WHERE binding->'workhub_binding'->>'tenant_id'=$1 AND binding->'workhub_binding'->>'project_id'=$2 AND binding->'workhub_binding'->>'workhub_attempt_id'=$3",
                tenant_id,
                project_id,
                workhub_attempt_id,
            )
        finally:
            await conn.close()
        return [str(row["activation_id"]) for row in rows or []]

    async def mark_activation_started(self, activation_id: str, result: dict[str, Any]):
        conn = await self._connect()
        try:
            changed = await conn.fetchval(
                "UPDATE aix_session_activations SET status='STARTED',result=$2::jsonb,updated_at=now() WHERE activation_id=$1 AND status='STARTING' RETURNING true",
                activation_id,
                json.dumps(result, sort_keys=True),
            )
            if not changed:
                raise ActivationStoreCorrupt("ACTIVATION_START_TRANSITION_REFUSED")
        finally:
            await conn.close()

    async def get_repair_count(self, activation_id: str) -> int:
        conn = await self._connect()
        try:
            row = await conn.fetchrow(
                "SELECT repair_count FROM aix_session_activations WHERE activation_id=$1",
                activation_id,
            )
        finally:
            await conn.close()
        if row is None:
            return 0
        try:
            return int(row["repair_count"])
        except (TypeError, ValueError):
            return 0

    async def mark_activation_result(
        self, activation_id: str, status: str,
        repair_round: int | None = None,
    ):
        """Transition one STARTED activation to its terminal outcome.

        Exactly-once terminality: the conditional UPDATE wins only from
        STARTED; a replay carrying the already-recorded terminal status
        is a no-op, while a conflicting terminal status raises
        ValueError (ACTIVATION_RESULT_REPLAY_CONFLICT).

        With repair_round (current authorized round) a delayed stale
        mark never overwrites a newly authorized round, and a
        current-round terminal-to-terminal move for a released repair
        is allowed.
        """
        if status not in ("SUCCEEDED", "FAILED"):
            raise ValueError("ACTIVATION_RESULT_STATUS_INVALID")
        conn = await self._connect()
        try:
            if repair_round is None:
                changed = await conn.fetchval(
                    "UPDATE aix_session_activations SET status=$2,updated_at=now() WHERE activation_id=$1 AND status='STARTED' RETURNING true",
                    activation_id,
                    status,
                )
                if changed:
                    return
                row = await conn.fetchrow(
                    "SELECT status FROM aix_session_activations WHERE activation_id=$1",
                    activation_id,
                )
                if row is None:
                    raise ActivationStoreCorrupt("ACTIVATION_ROW_MISSING")
                if row["status"] != status:
                    raise ValueError("ACTIVATION_RESULT_REPLAY_CONFLICT")
                return
            async with conn.transaction():
                row = await conn.fetchrow(
                    "SELECT status,repair_count FROM aix_session_activations WHERE activation_id=$1 FOR UPDATE",
                    activation_id,
                )
                if row is None:
                    raise ActivationStoreCorrupt("ACTIVATION_ROW_MISSING")
                try:
                    current_round = int(row["repair_count"])
                except (TypeError, ValueError):
                    current_round = 0
                if int(repair_round) != current_round:
                    raise ValueError("ACTIVATION_RESULT_REPLAY_CONFLICT")
                if row["status"] == "STARTED" or row["status"] != status:
                    await conn.execute(
                        "UPDATE aix_session_activations SET status=$2,updated_at=now() WHERE activation_id=$1",
                        activation_id,
                        status,
                    )
        finally:
            await conn.close()

    async def claim_verdict(self, verdict_id: str, record: dict[str, Any]):
        conn = await self._connect()
        try:
            async with conn.transaction():
                inserted = await conn.fetchval(
                    "INSERT INTO aix_session_verdicts(verdict_id,activation_id,record) VALUES($1,$2,$3::jsonb) ON CONFLICT DO NOTHING RETURNING true",
                    verdict_id,
                    record["activation_id"],
                    json.dumps(record, sort_keys=True),
                )
                row = await conn.fetchrow(
                    "SELECT record FROM aix_session_verdicts WHERE verdict_id=$1",
                    verdict_id,
                )
            if row is None:
                raise ActivationStoreCorrupt("VERDICT_ROW_MISSING")
            return bool(inserted), _obj(row["record"])
        finally:
            await conn.close()

    async def claim_result(self, result_id: str, record: dict[str, Any]):
        conn = await self._connect()
        try:
            async with conn.transaction():
                # Serialize result ownership on the activation AND its
                # current authorized round: lock the activation row first
                # so concurrent same-round contenders cannot both insert.
                # A distinct already-claimed row for the CURRENT round
                # refuses here — before this insert — so the loser can
                # never poison the winner's by-attempt readback. Exact
                # retries (identical cores by result_id) deduplicate
                # through the idempotent insert. A genuinely released
                # repair advances repair_count, so its next round finds
                # no current-round row and publishes. Legacy rows without
                # a round owner fail closed (single legacy is round 0).
                owner = await conn.fetchrow(
                    "SELECT activation_id,repair_count FROM aix_session_activations WHERE activation_id=$1 FOR UPDATE",
                    record["activation_id"],
                )
                current_round = 0
                if owner is not None:
                    try:
                        current_round = int(owner["repair_count"])
                    except (TypeError, ValueError):
                        current_round = 0
                incoming_round = _result_round(record)
                if incoming_round is None:
                    incoming_round = current_round
                    record = {**record, "repair_round": incoming_round}
                if int(incoming_round) != int(current_round):
                    raise ValueError("ACTIVATION_RESULT_REPLAY_CONFLICT")
                if owner is not None:
                    rows = await conn.fetch(
                        "SELECT record FROM aix_session_results WHERE activation_id=$1",
                        record["activation_id"],
                    )
                    legacy: list[dict[str, Any]] = []
                    same_round = False
                    for row in rows or []:
                        existing = _obj(row["record"])
                        existing_round = _result_round(existing)
                        if existing_round is None:
                            legacy.append(existing)
                            continue
                        if int(existing_round) != int(incoming_round):
                            continue
                        same_round = True
                        if _result_core(existing) != _result_core(record):
                            raise ValueError(
                                "ACTIVATION_RESULT_REPLAY_CONFLICT")
                    if legacy:
                        if len(legacy) > 1 or any(
                            _result_core(row) != _result_core(legacy[0])
                            for row in legacy
                        ):
                            raise ValueError(
                                "ACTIVATION_RESULT_REPLAY_CONFLICT")
                        if int(incoming_round) == 0 and not same_round:
                            raise ValueError(
                                "ACTIVATION_RESULT_REPLAY_CONFLICT")
                inserted = await conn.fetchval(
                    "INSERT INTO aix_session_results(result_id,activation_id,record) VALUES($1,$2,$3::jsonb) ON CONFLICT DO NOTHING RETURNING true",
                    result_id,
                    record["activation_id"],
                    json.dumps(record, sort_keys=True),
                )
                row = await conn.fetchrow(
                    "SELECT record FROM aix_session_results WHERE result_id=$1",
                    result_id,
                )
            if row is None:
                raise ActivationStoreCorrupt("ACTIVATION_RESULT_ROW_MISSING")
            stored = _obj(row["record"])
            if _result_core(stored) != _result_core(record):
                raise ValueError("ACTIVATION_RESULT_REPLAY_CONFLICT")
            return bool(inserted), stored
        finally:
            await conn.close()

    async def claim_result_with_mark(self, result_id: str, record: dict[str, Any]):
        """Atomically claim result ownership and apply the terminal mark.

        Lane-ops #23 crash-hole owner: one transaction locks the
        activation, verifies the repair binding against the existing
        allocation/verdict/release records, refuses a distinct
        same-round contender before inserting, inserts the result row,
        and moves the activation to its fenced terminal outcome. A
        result without a repair binding is the initial round-0 claim
        (accepted only while no repair is authorized); a bound result
        must name the current authorized round with the allocating
        fail verdict and its completed repair release. Missing, stale,
        future, pending, or failed bindings refuse before insertion.
        """
        if record.get("status") not in ("SUCCEEDED", "FAILED"):
            raise ValueError("ACTIVATION_RESULT_STATUS_INVALID")
        conn = await self._connect()
        try:
            async with conn.transaction():
                owner = await conn.fetchrow(
                    "SELECT activation_id,status,repair_count FROM aix_session_activations WHERE activation_id=$1 FOR UPDATE",
                    record["activation_id"],
                )
                current_round = 0
                activation_status = "STARTING"
                if owner is not None:
                    try:
                        current_round = int(owner["repair_count"])
                    except (TypeError, ValueError):
                        current_round = 0
                    activation_status = str(owner["status"])
                incoming_round = _result_round(record)
                if incoming_round is None:
                    incoming_round = current_round
                    record = {**record, "repair_round": incoming_round}
                if int(incoming_round) != int(current_round):
                    raise ValueError(
                        "ACTIVATION_RESULT_REPLAY_CONFLICT:"
                        "REPAIR_BINDING_STALE"
                    )
                number, verdict_id = _result_binding(record)
                if int(incoming_round) == 0:
                    if number is not None or verdict_id:
                        raise ValueError(
                            "ACTIVATION_RESULT_REPLAY_CONFLICT:"
                            "REPAIR_BINDING_INVALID"
                        )
                else:
                    if number is None or int(number) != int(incoming_round):
                        raise ValueError(
                            "ACTIVATION_RESULT_REPLAY_CONFLICT:"
                            "REPAIR_BINDING_INVALID"
                        )
                    if not verdict_id:
                        raise ValueError(
                            "ACTIVATION_RESULT_REPLAY_CONFLICT:"
                            "REPAIR_BINDING_MISSING"
                        )
                    verdict = await conn.fetchrow(
                        "SELECT repair_number,record FROM aix_session_verdicts WHERE verdict_id=$1",
                        verdict_id,
                    )
                    if verdict is None:
                        raise ValueError(
                            "ACTIVATION_RESULT_REPLAY_CONFLICT:"
                            "REPAIR_BINDING_MISSING"
                        )
                    try:
                        allocated = (
                            None if verdict["repair_number"] is None
                            else int(verdict["repair_number"])
                        )
                    except (TypeError, ValueError):
                        allocated = None
                    verdict_record = _obj(verdict["record"])
                    if (
                        allocated is None
                        or int(allocated) != int(incoming_round)
                        or verdict_record.get("activation_id")
                        != record["activation_id"]
                        or verdict_record.get("outcome") != "fail"
                    ):
                        raise ValueError(
                            "ACTIVATION_RESULT_REPLAY_CONFLICT:"
                            "REPAIR_BINDING_STALE"
                        )
                    release = await conn.fetchrow(
                        "SELECT state FROM aix_session_releases WHERE release_id=$1",
                        _repair_release_id(
                            record["activation_id"], int(incoming_round)),
                    )
                    if release is None:
                        raise ValueError(
                            "ACTIVATION_RESULT_REPLAY_CONFLICT:"
                            "REPAIR_BINDING_MISSING"
                        )
                    if str(release["state"]) != "completed":
                        raise ValueError(
                            "ACTIVATION_RESULT_REPLAY_CONFLICT:"
                            "REPAIR_BINDING_RELEASE_PENDING"
                        )
                if owner is not None:
                    rows = await conn.fetch(
                        "SELECT record FROM aix_session_results WHERE activation_id=$1",
                        record["activation_id"],
                    )
                    legacy: list[dict[str, Any]] = []
                    same_round = False
                    for row in rows or []:
                        existing = _obj(row["record"])
                        existing_round = _result_round(existing)
                        if existing_round is None:
                            legacy.append(existing)
                            continue
                        if int(existing_round) != int(incoming_round):
                            continue
                        same_round = True
                        if _result_core(existing) != _result_core(record):
                            raise ValueError(
                                "ACTIVATION_RESULT_REPLAY_CONFLICT")
                    if legacy:
                        if len(legacy) > 1 or any(
                            _result_core(row) != _result_core(legacy[0])
                            for row in legacy
                        ):
                            raise ValueError(
                                "ACTIVATION_RESULT_REPLAY_CONFLICT")
                        if int(incoming_round) == 0 and not same_round:
                            raise ValueError(
                                "ACTIVATION_RESULT_REPLAY_CONFLICT")
                inserted = await conn.fetchval(
                    "INSERT INTO aix_session_results(result_id,activation_id,record) VALUES($1,$2,$3::jsonb) ON CONFLICT DO NOTHING RETURNING true",
                    result_id,
                    record["activation_id"],
                    json.dumps(record, sort_keys=True),
                )
                row = await conn.fetchrow(
                    "SELECT record FROM aix_session_results WHERE result_id=$1",
                    result_id,
                )
                if row is None:
                    raise ActivationStoreCorrupt("ACTIVATION_RESULT_ROW_MISSING")
                stored = _obj(row["record"])
                if _result_core(stored) != _result_core(record):
                    raise ValueError("ACTIVATION_RESULT_REPLAY_CONFLICT")
                if (
                    activation_status == "STARTED"
                    or str(record.get("status")) != activation_status
                ):
                    await conn.execute(
                        "UPDATE aix_session_activations SET status=$2,updated_at=now() WHERE activation_id=$1",
                        record["activation_id"],
                        record["status"],
                    )
                return bool(inserted), stored
        finally:
            await conn.close()

    async def get_result(self, result_id: str):
        conn = await self._connect()
        try:
            row = await conn.fetchrow(
                "SELECT record FROM aix_session_results WHERE result_id=$1", result_id
            )
        finally:
            await conn.close()
        return None if row is None else _obj(row["record"])

    async def get_result_by_activation(self, activation_id: str):
        """Surface the current-round terminal result row for one activation.

        Lane-ops #23: supervision carries this row as the genuine
        Temporal/result proof, scoped to the current authorized round
        (repair_count). No row for the current round means no proof yet
        (hold; a superseded prior round is never surfaced). Distinct
        rows for the same round — including legacy rows without a round
        owner — refuse ambiguous (hold, never pick arbitrary/latest).
        """
        conn = await self._connect()
        try:
            # One statement, one snapshot: the round owner and its rows
            # are read coherently, so a repair allocated concurrently
            # can never mix a previous round's status/result with a
            # newly released round. Never picks a latest row.
            rows = await conn.fetch(
                "SELECT a.repair_count AS repair_count, r.record AS record"
                " FROM aix_session_activations a"
                " LEFT JOIN aix_session_results r"
                " ON r.activation_id = a.activation_id"
                " WHERE a.activation_id=$1"
                " ORDER BY r.created_at, r.result_id",
                activation_id,
            )
        finally:
            await conn.close()
        if not rows:
            return None
        current_round = 0
        try:
            current_round = int(rows[0]["repair_count"])
        except (TypeError, ValueError):
            current_round = 0
        records = [
            _obj(row["record"]) for row in rows or [] if row["record"] is not None
        ]
        if not records:
            return None
        legacy = [record for record in records if _result_round(record) is None]
        tagged = [record for record in records if _result_round(record) is not None]
        if legacy:
            if tagged or len(legacy) > 1 or any(
                _result_core(row) != _result_core(legacy[0]) for row in legacy
            ):
                raise ValueError("ACTIVATION_RESULT_AMBIGUOUS")
            if int(current_round) != 0:
                return None
            return legacy[0]
        by_round: dict[int, list[dict[str, Any]]] = {}
        for record in tagged:
            by_round.setdefault(int(_result_round(record)), []).append(record)
        for members in by_round.values():
            if any(
                _result_core(row) != _result_core(members[0]) for row in members[1:]
            ):
                raise ValueError("ACTIVATION_RESULT_AMBIGUOUS")
        current = by_round.get(int(current_round))
        if not current:
            return None
        return current[0]

    async def claim_next_repair(self, activation_id: str, verdict_id: str):
        conn = await self._connect()
        try:
            async with conn.transaction():
                existing = await conn.fetchval(
                    "SELECT repair_number FROM aix_session_verdicts WHERE verdict_id=$1 FOR UPDATE",
                    verdict_id,
                )
                if existing is not None:
                    return "allocated", int(existing)
                row = await conn.fetchrow(
                    "SELECT repair_count,max_repairs FROM aix_session_activations WHERE activation_id=$1 FOR UPDATE",
                    activation_id,
                )
                if row is None:
                    raise ActivationStoreCorrupt("ACTIVATION_ROW_MISSING")
                if row["repair_count"] >= row["max_repairs"]:
                    return "exhausted", None
                number = int(row["repair_count"]) + 1
                await conn.execute(
                    "UPDATE aix_session_activations SET repair_count=$2,updated_at=now() WHERE activation_id=$1",
                    activation_id,
                    number,
                )
                changed = await conn.execute(
                    "UPDATE aix_session_verdicts SET repair_number=$2 WHERE verdict_id=$1 AND repair_number IS NULL",
                    verdict_id,
                    number,
                )
                if changed != "UPDATE 1":
                    raise ActivationStoreCorrupt("REPAIR_ALLOCATION_CONFLICT")
                return "allocated", number
        finally:
            await conn.close()

    async def claim_release(self, release_id: str, record: dict[str, Any]):
        conn = await self._connect()
        try:
            async with conn.transaction():
                await conn.execute(
                    "INSERT INTO aix_session_releases(release_id,record,state) VALUES($1,$2::jsonb,'retryable') ON CONFLICT DO NOTHING",
                    release_id,
                    json.dumps(record, sort_keys=True),
                )
                row = await conn.fetchrow(
                    "SELECT record,state,lease_owner,lease_token,lease_expires_at,result FROM aix_session_releases WHERE release_id=$1 FOR UPDATE",
                    release_id,
                )
                if row is None:
                    raise ActivationStoreCorrupt("RELEASE_ROW_MISSING")
                stored = _obj(row["record"])
                if stored != record:
                    raise ActivationStoreCorrupt("RELEASE_REPLAY_CONFLICT")
                if row["state"] == "completed":
                    if row["result"] is not None:
                        stored["release_result"] = _obj(row["result"])
                    return "completed", stored, None
                now = datetime.now(timezone.utc)
                expiry = row["lease_expires_at"]
                if row["state"] == "claimed" and expiry is not None and expiry > now:
                    return "busy", stored, None
                token = f"{self._incarnation}:{uuid.uuid4().hex}"
                await conn.execute(
                    "UPDATE aix_session_releases SET state='claimed',lease_owner=$2,lease_token=$3,lease_expires_at=now()+($4 * interval '1 second'),updated_at=now() WHERE release_id=$1",
                    release_id,
                    self._owner,
                    token,
                    self._lease,
                )
                return "acquired", stored, token
        finally:
            await conn.close()

    async def abandon_release(self, release_id: str, acquisition_token: str):
        conn = await self._connect()
        try:
            await conn.execute(
                "UPDATE aix_session_releases SET state='retryable',lease_owner=NULL,lease_token=NULL,lease_expires_at=NULL,updated_at=now() WHERE release_id=$1 AND state='claimed' AND lease_owner=$2 AND lease_token=$3",
                release_id,
                self._owner,
                acquisition_token,
            )
        finally:
            await conn.close()

    async def mark_release_completed(
        self, release_id: str, acquisition_token: str, result: dict[str, Any]
    ):
        conn = await self._connect()
        try:
            changed = await conn.fetchval(
                "UPDATE aix_session_releases SET state='completed',result=$4::jsonb,lease_owner=NULL,lease_token=NULL,lease_expires_at=NULL,updated_at=now() WHERE release_id=$1 AND state='claimed' AND lease_owner=$2 AND lease_token=$3 RETURNING true",
                release_id,
                self._owner,
                acquisition_token,
                json.dumps(result, sort_keys=True),
            )
            if not changed:
                raise ActivationStoreCorrupt("RELEASE_LEASE_LOST")
        finally:
            await conn.close()
