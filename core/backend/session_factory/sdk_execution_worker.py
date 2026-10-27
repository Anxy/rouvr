"""Private AIX adapter for one pinned Agent SDK package; no lifecycle ownership."""

from __future__ import annotations
import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

SDK_COMMIT = "950149934d1be83cfd6b87543639e7f8d872fc6f"
SDK_PACKAGE_VERSION = "0.9.0-pilot.2"
SDK_PACKAGE_SHA256 = "88b904721cde9b0a9136eb80b62a078b5b63bc2d8c6b5630a10dd340dfacbdf8"
SDK_API_VERSION = "hyeve.agent-sdk/public-v2alpha1"
AIX_PROFILE_VERSION = "hyeve.aix/sdk-compilation-profile/v1"
AIX_PUBLIC_CONTRACT_REVISION = "98720c9fb09aff51757bee139d0bed03d96523be"
LIFECYCLE_VERSION = "hyeve.aix/sdk-lifecycle/v1"
WORKHUB_EVIDENCE_ENRICHMENT_KEYS = frozenset(
    {
        "captured_at",
        "correlation_id",
        "created_at",
        "created_by",
        "evidence_ref",
        "evidence_type",
        "schema_version",
        "submitted_by",
        "summary",
    }
)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _matches_workhub_evidence_json(submitted: dict[str, Any], observed: Any) -> bool:
    if not isinstance(observed, dict):
        return False
    extras = set(observed) - set(submitted)
    if not extras <= WORKHUB_EVIDENCE_ENRICHMENT_KEYS:
        return False
    return all(
        key in observed and _canonical_json(observed[key]) == _canonical_json(value)
        for key, value in submitted.items()
    )


class WorkerReplayStore(Protocol):
    async def claim(
        self, key: str, digest: str
    ) -> tuple[str, dict[str, Any] | None]: ...
    async def checkpoint(
        self, key: str, expected_stage: str, stage: str, value: dict[str, Any]
    ) -> None: ...


class LifecycleTransport(Protocol):
    async def publish(
        self, event: dict[str, Any], headers: dict[str, str]
    ) -> dict[str, Any]: ...


class WorkHubTransport(Protocol):
    async def attach(
        self, task_id: str, evidence: dict[str, Any], headers: dict[str, str]
    ) -> dict[str, Any]: ...
    async def read(
        self, task_id: str, evidence_id: str, headers: dict[str, str]
    ) -> dict[str, Any]: ...


@dataclass(frozen=True)
class WorkerBinding:
    tenant_id: str
    project_id: str
    session_id: str
    task_id: str
    attempt_id: str
    task_version: int
    source_revision: str
    sdk_package_version: str
    runtime_version: str
    token: str


class NodeSdkRunner:
    def __init__(self, root: Path):
        package = root / "vendor" / "hyeve-agent-sdk-0.9.0-pilot.2.tgz"
        if hashlib.sha256(package.read_bytes()).hexdigest() != SDK_PACKAGE_SHA256:
            raise RuntimeError("SDK_PACKAGE_DIGEST_MISMATCH")
        self._script = root / "sdk-worker" / "worker.mjs"
        self._cwd = root / "sdk-worker"

    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        proc = subprocess.run(
            ["node", str(self._script)],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            cwd=self._cwd,
            timeout=30,
        )
        if proc.returncode:
            raise RuntimeError("SDK_WORKER_FAILED")
        value = json.loads(proc.stdout)
        if not isinstance(value, dict):
            raise RuntimeError("SDK_WORKER_RESULT_INVALID")
        return value


class AixSdkExecutionWorker:
    def __init__(
        self,
        binding: WorkerBinding,
        runner: NodeSdkRunner,
        store: WorkerReplayStore,
        lifecycle: LifecycleTransport,
        workhub: WorkHubTransport,
    ):
        self.b = binding
        self.runner = runner
        self.store = store
        self.lifecycle = lifecycle
        self.workhub = workhub

    def _headers(self, key: str, *, workhub: bool = False):
        if not self.b.token:
            raise RuntimeError("SDK_WORKER_AUTH_REQUIRED")
        h = {
            "Authorization": f"Bearer {self.b.token}",
            "X-Hyeve-Tenant-Id": self.b.tenant_id,
            "X-Hyeve-Session-Id": self.b.session_id,
            "Idempotency-Key": key,
        }
        h["X-Project-ID" if workhub else "X-Hyeve-Project-Id"] = self.b.project_id
        return h

    async def execute(self, request: dict[str, Any]) -> dict[str, Any]:
        required = {
            "eventId",
            "tenantId",
            "projectId",
            "sessionId",
            "taskId",
            "taskVersion",
            "sourceRevision",
            "sequence",
            "profile",
        }
        if set(request) != required:
            raise RuntimeError("SDK_WORKER_REQUEST_INVALID")
        for key, expected in (
            ("tenantId", self.b.tenant_id),
            ("projectId", self.b.project_id),
            ("sessionId", self.b.session_id),
            ("taskId", self.b.task_id),
            ("taskVersion", self.b.task_version),
            ("sourceRevision", self.b.source_revision),
        ):
            if request[key] != expected:
                raise RuntimeError(f"SDK_WORKER_{key.upper()}_MISMATCH")
        profile = request["profile"]
        try:
            assignment = profile["assignment"]
            refs = assignment["integrationRefs"]
            bound = (
                assignment["taskId"],
                assignment["attemptId"],
                assignment["repository"]["sourceRevision"],
                profile["contractVersion"],
                profile["aixSourceRevision"],
                profile["sdkApiVersion"],
                profile["policy"]["adapter"]["version"],
                refs["aix.tenant"],
                refs["aix.project"],
                refs["aix.session"],
                int(refs["aix.taskVersion"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("SDK_WORKER_PROFILE_BINDING_INVALID") from exc
        expected_profile = (
            self.b.task_id,
            self.b.attempt_id,
            self.b.source_revision,
            AIX_PROFILE_VERSION,
            AIX_PUBLIC_CONTRACT_REVISION,
            SDK_API_VERSION,
            self.b.runtime_version,
            self.b.tenant_id,
            self.b.project_id,
            self.b.session_id,
            self.b.task_version,
        )
        if (
            bound != expected_profile
            or self.b.sdk_package_version != SDK_PACKAGE_VERSION
        ):
            raise RuntimeError("SDK_WORKER_PROFILE_BINDING_MISMATCH")
        digest = hashlib.sha256(
            json.dumps(request, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        state, saved = await self.store.claim(request["eventId"], digest)
        if state == "conflict":
            raise RuntimeError("SDK_WORKER_REPLAY_CONFLICT")
        if state == "complete":
            return saved or {}
        event = (saved or {}).get("event")
        if event is None:
            event = self.runner.run(
                {
                    "mode": "credential_free_fake",
                    **{
                        k: request[k]
                        for k in (
                            "eventId",
                            "tenantId",
                            "projectId",
                            "sessionId",
                            "sequence",
                        )
                    },
                    "profile": request["profile"],
                }
            )
            await self.store.checkpoint(
                request["eventId"], "new", "sdk_complete", {"event": event}
            )
        expected_event = (
            LIFECYCLE_VERSION,
            request["eventId"],
            self.b.tenant_id,
            self.b.project_id,
            self.b.session_id,
            self.b.task_id,
            self.b.attempt_id,
            self.b.source_revision,
            SDK_PACKAGE_VERSION,
            self.b.runtime_version,
            request["sequence"],
            "attempt.result",
            "SUCCEEDED",
            False,
            False,
        )
        try:
            observed_event = (
                event["contractVersion"],
                event["eventId"],
                event["tenantId"],
                event["projectId"],
                event["sessionId"],
                event["taskId"],
                event["attemptId"],
                event["payload"]["sourceRevision"],
                event["payload"]["sdkPackageVersion"],
                event["payload"]["runtimeVersion"],
                event["sequence"],
                event["kind"],
                event["payload"]["status"],
                event["payload"]["providerRequest"],
                event["payload"]["credentialUsed"],
            )
        except (KeyError, TypeError) as exc:
            raise RuntimeError("SDK_WORKER_EVENT_BINDING_INVALID") from exc
        if observed_event != expected_event:
            raise RuntimeError("SDK_WORKER_EVENT_BINDING_MISMATCH")
        ack = await self.lifecycle.publish(event, self._headers(request["eventId"]))
        expected = {
            "contractVersion": LIFECYCLE_VERSION,
            "status": "accepted",
            "eventId": request["eventId"],
            "tenantId": self.b.tenant_id,
            "projectId": self.b.project_id,
            "sessionId": self.b.session_id,
            "idempotencyKey": request["eventId"],
        }
        if ack != expected:
            raise RuntimeError("SDK_WORKER_LIFECYCLE_ACK_MISMATCH")
        evidence = {
            "project_id": self.b.project_id,
            "evidence_type": "result",
            "evidence_ref": f"aix-event:{request['eventId']}",
            "evidence_json": event,
        }
        created = await self.workhub.attach(
            self.b.task_id, evidence, self._headers(request["eventId"], workhub=True)
        )
        eid = created.get("id")
        if not isinstance(eid, str) or not eid:
            raise RuntimeError("SDK_WORKER_EVIDENCE_ID_MISSING")
        read = await self.workhub.read(
            self.b.task_id,
            eid,
            self._headers(request["eventId"] + ":read", workhub=True),
        )
        expected_read = {
            "task_id": self.b.task_id,
            "project_id": self.b.project_id,
            "evidence_type": evidence["evidence_type"],
            "evidence_ref": evidence["evidence_ref"],
            "evidence_json": evidence["evidence_json"],
        }
        observed_read = {key: read.get(key) for key in expected_read}
        if (
            read.get("id") != eid
            or any(
                observed_read[key] != expected_read[key]
                for key in ("task_id", "project_id", "evidence_type", "evidence_ref")
            )
            or not _matches_workhub_evidence_json(
                expected_read["evidence_json"], observed_read["evidence_json"]
            )
        ):
            raise RuntimeError("SDK_WORKER_EVIDENCE_READBACK_MISMATCH")
        result = {"event": event, "lifecycle_ack": ack, "evidence_id": eid}
        await self.store.checkpoint(
            request["eventId"], "sdk_complete", "complete", result
        )
        return result


class PostgresWorkerReplayStore:
    """Durable replay/checkpoint store; schema creation is explicit."""

    SCHEMA = """CREATE TABLE IF NOT EXISTS aix_sdk_worker_replays(event_id text PRIMARY KEY, request_digest text NOT NULL, stage text NOT NULL, value jsonb, updated_at timestamptz NOT NULL DEFAULT now());"""

    def __init__(self, database_url: str, connect=None):
        self.url = database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
        self.connect_override = connect

    async def _connect(self):
        if self.connect_override:
            return await self.connect_override()
        import asyncpg

        return await asyncpg.connect(self.url, timeout=5, command_timeout=5)

    async def initialize(self):
        c = await self._connect()
        try:
            await c.execute(self.SCHEMA)
        finally:
            await c.close()

    async def claim(self, key: str, digest: str):
        c = await self._connect()
        try:
            async with c.transaction():
                await c.execute(
                    "INSERT INTO aix_sdk_worker_replays(event_id,request_digest,stage) VALUES($1,$2,'new') ON CONFLICT DO NOTHING",
                    key,
                    digest,
                )
                row = await c.fetchrow(
                    "SELECT request_digest,stage,value FROM aix_sdk_worker_replays WHERE event_id=$1 FOR UPDATE",
                    key,
                )
            if row is None:
                raise RuntimeError("SDK_WORKER_REPLAY_ROW_MISSING")
            if row["request_digest"] != digest:
                return "conflict", None
            value = row["value"]
            if isinstance(value, str):
                value = json.loads(value)
            if value is not None and not isinstance(value, dict):
                raise RuntimeError("SDK_WORKER_REPLAY_ROW_CORRUPT")
            return ("complete" if row["stage"] == "complete" else "pending"), value
        finally:
            await c.close()

    async def checkpoint(
        self, key: str, expected_stage: str, stage: str, value: dict[str, Any]
    ):
        if stage not in {"sdk_complete", "complete"}:
            raise RuntimeError("SDK_WORKER_STAGE_INVALID")
        c = await self._connect()
        try:
            async with c.transaction():
                current = await c.fetchval(
                    "SELECT stage FROM aix_sdk_worker_replays WHERE event_id=$1 FOR UPDATE",
                    key,
                )
                if current != expected_stage:
                    raise RuntimeError("SDK_WORKER_CHECKPOINT_CONFLICT")
                changed = await c.fetchval(
                    "UPDATE aix_sdk_worker_replays SET stage=$3,value=$4::jsonb,updated_at=now() WHERE event_id=$1 AND stage=$2 RETURNING true",
                    key,
                    expected_stage,
                    stage,
                    json.dumps(value, sort_keys=True),
                )
                if not changed:
                    raise RuntimeError("SDK_WORKER_CHECKPOINT_CONFLICT")
        finally:
            await c.close()
