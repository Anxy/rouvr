"""Production composition for canonical selection -> Temporal -> continuation.

The inbox is the durable AIX side of the WorkHub/OpsBus accepted-selection
contract. Producers insert a closed event envelope with a stable event id;
this consumer owns acknowledgement only after the coordinator effect returns.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from backend.session_factory.activation import (
    ActivationResultEvent,
    MuseActivationCoordinator,
    SelectionEvent,
    VerdictEvent,
)
from backend.workflows.temporal.objective_cycle_contracts import (
    OBJECTIVE_CYCLE_WORKFLOW_CONTRACT_VERSION,
    objective_cycle_workflow_id,
)

_EVENT_KINDS = {"accepted_selection", "attempt_result", "independent_verdict"}
_SCHEMA = """
CREATE TABLE IF NOT EXISTS aix_activation_inbox (
 event_id text PRIMARY KEY, event_kind text NOT NULL, payload jsonb NOT NULL,
 state text NOT NULL CHECK (state IN ('pending','claimed','acked')) DEFAULT 'pending',
 lease_owner text, lease_token text, lease_expires_at timestamptz, attempts integer NOT NULL DEFAULT 0,
 result jsonb, updated_at timestamptz NOT NULL DEFAULT now());
ALTER TABLE aix_activation_inbox ADD COLUMN IF NOT EXISTS lease_token text;
CREATE TABLE IF NOT EXISTS aix_activation_continuation_outbox (
 release_id text PRIMARY KEY, payload jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now());
"""


def _object(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, dict):
        raise RuntimeError("ACTIVATION_EVENT_CORRUPT")
    return dict(value)


class PostgresActivationEventBridge:
    """Lease-backed canonical inbox plus deterministic continuation outbox."""

    def __init__(
        self, database_url: str, *, owner: str, lease_seconds: int = 30, connect=None
    ):
        if not database_url or not owner or lease_seconds < 1:
            raise ValueError("ACTIVATION_EVENT_BRIDGE_CONFIG_INVALID")
        self.url = database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
        self.owner = owner
        self.lease_seconds = lease_seconds
        self.incarnation = uuid.uuid4().hex
        self.connect_override = connect

    async def _connect(self):
        if self.connect_override:
            return await self.connect_override()
        import asyncpg

        return await asyncpg.connect(self.url, timeout=5, command_timeout=5)

    async def initialize(self) -> None:
        conn = await self._connect()
        try:
            await conn.execute(_SCHEMA)
        finally:
            await conn.close()

    async def enqueue(
        self, event_id: str, event_kind: str, payload: dict[str, Any]
    ) -> None:
        if event_kind not in _EVENT_KINDS or payload.get("event_id") != event_id:
            raise ValueError("ACTIVATION_EVENT_ENVELOPE_INVALID")
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        conn = await self._connect()
        try:
            async with conn.transaction():
                await conn.execute(
                    "INSERT INTO aix_activation_inbox(event_id,event_kind,payload) VALUES($1,$2,$3::jsonb) ON CONFLICT DO NOTHING",
                    event_id,
                    event_kind,
                    encoded,
                )
                row = await conn.fetchrow(
                    "SELECT event_kind,payload FROM aix_activation_inbox WHERE event_id=$1 FOR UPDATE",
                    event_id,
                )
                if (
                    row is None
                    or row["event_kind"] != event_kind
                    or _object(row["payload"]) != payload
                ):
                    raise RuntimeError("ACTIVATION_EVENT_REPLAY_CONFLICT")
        finally:
            await conn.close()

    async def claim(self) -> dict[str, Any] | None:
        conn = await self._connect()
        try:
            async with conn.transaction():
                row = await conn.fetchrow(
                    "SELECT event_id,event_kind,payload FROM aix_activation_inbox WHERE state='pending' OR (state='claimed' AND lease_expires_at<=now()) ORDER BY updated_at,event_id FOR UPDATE SKIP LOCKED LIMIT 1"
                )
                if row is None:
                    return None
                lease_token = f"{self.incarnation}:{uuid.uuid4().hex}"
                await conn.execute(
                    "UPDATE aix_activation_inbox SET state='claimed',lease_owner=$2,lease_token=$3,lease_expires_at=now()+($4*interval '1 second'),attempts=attempts+1,updated_at=now() WHERE event_id=$1",
                    row["event_id"],
                    self.owner,
                    lease_token,
                    self.lease_seconds,
                )
                return {
                    "event_id": row["event_id"],
                    "event_kind": row["event_kind"],
                    "payload": _object(row["payload"]),
                    "lease_token": lease_token,
                }
        finally:
            await conn.close()

    async def acknowledge(
        self, event_id: str, lease_token: str, result: dict[str, Any]
    ) -> None:
        conn = await self._connect()
        try:
            changed = await conn.fetchval(
                "UPDATE aix_activation_inbox SET state='acked',result=$4::jsonb,lease_owner=NULL,lease_token=NULL,lease_expires_at=NULL,updated_at=now() WHERE event_id=$1 AND state='claimed' AND lease_owner=$2 AND lease_token=$3 RETURNING true",
                event_id,
                self.owner,
                lease_token,
                json.dumps(result, sort_keys=True),
            )
            if not changed:
                raise RuntimeError("ACTIVATION_EVENT_ACK_LEASE_LOST")
        finally:
            await conn.close()

    async def retry(self, event_id: str, lease_token: str) -> None:
        conn = await self._connect()
        try:
            changed = await conn.fetchval(
                "UPDATE aix_activation_inbox SET state='pending',lease_owner=NULL,lease_token=NULL,lease_expires_at=NULL,updated_at=now() WHERE event_id=$1 AND state='claimed' AND lease_owner=$2 AND lease_token=$3 RETURNING true",
                event_id,
                self.owner,
                lease_token,
            )
            if not changed:
                raise RuntimeError("ACTIVATION_EVENT_RETRY_LEASE_LOST")
        finally:
            await conn.close()

    async def release(self, payload: dict[str, Any]) -> dict[str, Any]:
        release_id = str(payload.get("release_id") or "")
        if not release_id or payload.get("idempotency_key") != release_id:
            raise ValueError("CONTINUATION_RELEASE_INVALID")
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        conn = await self._connect()
        try:
            async with conn.transaction():
                await conn.execute(
                    "INSERT INTO aix_activation_continuation_outbox(release_id,payload) VALUES($1,$2::jsonb) ON CONFLICT DO NOTHING",
                    release_id,
                    encoded,
                )
                row = await conn.fetchrow(
                    "SELECT payload FROM aix_activation_continuation_outbox WHERE release_id=$1 FOR UPDATE",
                    release_id,
                )
                if row is None or _object(row["payload"]) != payload:
                    raise RuntimeError("CONTINUATION_RELEASE_REPLAY_CONFLICT")
            return {"release_id": release_id, "receipt": f"continuation:{release_id}"}
        finally:
            await conn.close()


class CanonicalTemporalMuseStarter:
    """Only adapter allowed to map activation binding to the registered workflow."""

    def __init__(self, canonical_start: Callable[..., Awaitable[dict[str, Any]]]):
        self._start = canonical_start

    async def __call__(self, record: dict[str, Any]) -> dict[str, Any]:
        expected = objective_cycle_workflow_id(
            tenant_id=record["tenant_id"],
            objective_id=record["objective_id"],
            cycle_id=record["cycle_id"],
        )
        if (
            record.get("workflow_id") != expected
            or record.get("idempotency_key") != expected
        ):
            raise RuntimeError("ACTIVATION_TEMPORAL_IDENTITY_MISMATCH")
        # Lane-ops #23 M1: thread the already-owned opaque activation/
        # selection refs unchanged from the canonical record into the
        # workflow input. Copied, never defaulted or re-derived; a missing
        # value travels as "" and the Muse adapter refuses it before spawn.
        stored_binding = record.get("workhub_binding")
        binding = stored_binding if isinstance(stored_binding, dict) else {}
        result = await self._start(
            raw={
                "contract_version": OBJECTIVE_CYCLE_WORKFLOW_CONTRACT_VERSION,
                "workflow_version": record["workflow_version"],
                "tenant_id": record["tenant_id"],
                "objective_id": record["objective_id"],
                "cycle_id": record["cycle_id"],
                "candidate_sha": record["candidate_sha"],
                "plan_sha256": record["plan_sha256"],
                "start_request_id": record["selection_id"],
                "correlation_id": record["correlation_id"],
                "activation_id": str(record.get("activation_id") or ""),
                "selection_id": str(record.get("selection_id") or ""),
                "workhub_attempt_id": str(binding.get("workhub_attempt_id") or ""),
                "accepted_model_id": str(record.get("model") or ""),
                "worker_id": str(record.get("worker_id") or ""),
            }
        )
        if (
            result.get("workflow") != "AixObjectiveCycleWorkflow"
            or result.get("workflow_id") != expected
        ):
            raise RuntimeError("ACTIVATION_TEMPORAL_RECEIPT_MISMATCH")
        return {**result, "muse_activation_receipt": f"muse:{record['activation_id']}"}


class ConfiguredReviewerAuthority:
    def __init__(self, reviewers: frozenset[str]):
        self.reviewers = reviewers

    async def __call__(self, payload: dict[str, Any]) -> bool:
        return bool(self.reviewers) and payload.get("reviewer_id") in self.reviewers


@dataclass(frozen=True)
class AuthenticatedActivationPrincipal:
    tenant_id: str
    actor_id: str


_TENANT_HINT_HEADER = "X-Hyeve-Tenant-Id"


def _unverified_token_tenant(raw: str) -> str:
    """Best-effort tenant hint from the unverified JWT payload.

    Auth requires tenant context on validate; the signature stays the
    authority (Auth binds RLS to the verified JWT tenant, never this
    header). Undecodable values omit the hint and fail closed server-side.
    """
    try:
        import base64
        import json

        segment = raw.strip().split(".")[1]
        padded = segment + "=" * (-len(segment) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
    except Exception:
        return ""
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("tenant_id") or "").strip()


class CoreActivationAuthenticator:
    """Validate bearer credentials with Auth and return only asserted identity."""

    def __init__(self, core_api_url: str, *, timeout_seconds: float = 5.0):
        if not core_api_url or timeout_seconds <= 0:
            raise ValueError("ACTIVATION_AUTH_CONFIG_INVALID")
        self.url = core_api_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    async def __call__(self, authorization: str) -> AuthenticatedActivationPrincipal:
        scheme, separator, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not separator or not token.strip():
            raise ValueError("ACTIVATION_AUTH_BEARER_REQUIRED")
        import httpx

        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                hint_headers = {}
                hint = _unverified_token_tenant(token)
                if hint:
                    hint_headers[_TENANT_HINT_HEADER] = hint
                response = await client.post(
                    f"{self.url}/auth/validate", json={"token": token.strip()},
                    headers=hint_headers,
                )
                response.raise_for_status()
                value = response.json()
        except Exception as exc:
            raise ValueError("ACTIVATION_AUTH_INVALID") from exc
        if not isinstance(value, dict) or value.get("active") is False:
            raise ValueError("ACTIVATION_AUTH_INVALID")
        tenant_id = str(value.get("tenant_id") or "").strip()
        actor_id = str(value.get("sub") or value.get("actor_id") or "").strip()
        if not tenant_id or not actor_id:
            raise ValueError("ACTIVATION_AUTH_IDENTITY_MISSING")
        return AuthenticatedActivationPrincipal(tenant_id=tenant_id, actor_id=actor_id)


class ActivationEventConsumer:
    def __init__(
        self,
        bridge: PostgresActivationEventBridge,
        coordinator: MuseActivationCoordinator,
    ):
        self.bridge, self.coordinator = bridge, coordinator

    async def process_once(self) -> bool:
        envelope = await self.bridge.claim()
        if envelope is None:
            return False
        payload = dict(envelope["payload"])
        try:
            if envelope["event_kind"] == "accepted_selection":
                payload.pop("event_id", None)
                result = await self.coordinator.accept_selection(
                    SelectionEvent(**payload)
                )
            elif envelope["event_kind"] == "attempt_result":
                payload.pop("event_id", None)
                result = await self.coordinator.accept_result(
                    ActivationResultEvent(**payload)
                )
            elif envelope["event_kind"] == "independent_verdict":
                payload.pop("event_id", None)
                result = await self.coordinator.accept_verdict(VerdictEvent(**payload))
            else:
                raise RuntimeError("ACTIVATION_EVENT_KIND_INVALID")
            await self.bridge.acknowledge(
                envelope["event_id"], envelope["lease_token"], result
            )
            return True
        except Exception:
            await self.bridge.retry(envelope["event_id"], envelope["lease_token"])
            raise


class ActivationRuntime:
    def __init__(
        self, consumer: ActivationEventConsumer, *, poll_seconds: float = 0.25
    ):
        self.consumer, self.poll_seconds, self._task = consumer, poll_seconds, None

    async def _run(self):
        while True:
            try:
                worked = await self.consumer.process_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                worked = False
            if not worked:
                await asyncio.sleep(self.poll_seconds)

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(
                self._run(), name="aix-activation-consumer"
            )

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
