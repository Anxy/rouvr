import asyncio
import copy
import json
from dataclasses import replace
from pathlib import Path
import pytest
from backend.session_factory.sdk_execution_worker import (
    AixSdkExecutionWorker,
    NodeSdkRunner,
    WorkerBinding,
    SDK_PACKAGE_SHA256,
)

ROOT = Path(__file__).parents[2]
PROFILE = json.loads(
    (Path(__file__).parent / "fixtures/sdk_worker/profile.v1.json").read_text()
)


class Store:
    def __init__(self):
        self.rows = {}

    async def claim(self, key, digest):
        r = self.rows.get(key)
        if not r:
            self.rows[key] = {"digest": digest, "stage": "new", "value": None}
            return "new", None
        if r["digest"] != digest:
            return "conflict", None
        return ("complete" if r["stage"] == "complete" else "pending"), r["value"]

    async def checkpoint(self, key, expected_stage, stage, value):
        if self.rows[key]["stage"] != expected_stage:
            raise RuntimeError("SDK_WORKER_CHECKPOINT_CONFLICT")
        self.rows[key].update(stage=stage, value=value)


class Lifecycle:
    def __init__(self, fail=False, bad=False):
        self.calls = []
        self.fail = fail
        self.bad = bad

    async def publish(self, event, headers):
        self.calls.append((event, headers))
        if self.fail:
            self.fail = False
            raise RuntimeError("crash")
        ack = {
            "contractVersion": "hyeve.aix/sdk-lifecycle/v1",
            "status": "accepted",
            "eventId": event["eventId"],
            "tenantId": event["tenantId"],
            "projectId": event["projectId"],
            "sessionId": event["sessionId"],
            "idempotencyKey": event["eventId"],
        }
        if self.bad:
            ack["sessionId"] = "other"
        return ack


class WorkHub:
    def __init__(self, bad=None, enrichment=None):
        self.attach_calls = []
        self.read_calls = []
        self.bad = bad
        self.enrichment = enrichment or {}

    async def attach(self, task, evidence, headers):
        self.attach_calls.append((task, evidence, headers))
        return {"id": "evidence-1"}

    async def read(self, task, eid, headers):
        self.read_calls.append((task, eid, headers))
        evidence = self.attach_calls[-1][1]
        result = {
            "id": eid,
            "task_id": task,
            "project_id": "project-1",
            "evidence_type": evidence["evidence_type"],
            "evidence_ref": evidence["evidence_ref"],
            "evidence_json": {**evidence["evidence_json"], **self.enrichment},
        }
        if self.bad == "missing_event_field":
            result["evidence_json"].pop("taskId")
        elif self.bad == "evidence_json":
            result[self.bad] = {**result[self.bad], "kind": "substituted"}
        elif self.bad:
            result[self.bad] = "wrong"
        return result


class FakeRunner:
    def __init__(self):
        self.calls = 0

    def run(self, p):
        self.calls += 1
        return {
            "contractVersion": "hyeve.aix/sdk-lifecycle/v1",
            "eventId": p["eventId"],
            "tenantId": p["tenantId"],
            "projectId": p["projectId"],
            "sessionId": p["sessionId"],
            "taskId": "fixture-task",
            "attemptId": "attempt-1",
            "sequence": p["sequence"],
            "kind": "attempt.result",
            "payload": {
                "status": "SUCCEEDED",
                "providerRequest": False,
                "credentialUsed": False,
                "sourceRevision": "0123456789abcdef",
                "sdkPackageVersion": "0.9.0-pilot.2",
                "runtimeVersion": "0.154.0",
            },
        }


def binding():
    return WorkerBinding(
        "tenant-1",
        "project-1",
        "session-1",
        "fixture-task",
        "attempt-1",
        7,
        "0123456789abcdef",
        "0.9.0-pilot.2",
        "0.154.0",
        "opaque",
    )


def request(**change):
    x = {
        "eventId": "event-1",
        "tenantId": "tenant-1",
        "projectId": "project-1",
        "sessionId": "session-1",
        "taskId": "fixture-task",
        "taskVersion": 7,
        "sourceRevision": "0123456789abcdef",
        "sequence": 1,
        "profile": PROFILE,
    }
    x.update(change)
    return x


@pytest.mark.asyncio
async def test_real_pinned_sdk_selection_to_result_ack_and_workhub_readback():
    store = Store()
    life = Lifecycle()
    wh = WorkHub()
    runner = NodeSdkRunner(ROOT)
    out = await AixSdkExecutionWorker(binding(), runner, store, life, wh).execute(
        request()
    )
    assert out["evidence_id"] == "evidence-1"
    assert out["event"]["payload"]["providerRequest"] is False
    assert out["event"]["payload"]["credentialUsed"] is False
    assert life.calls[0][1]["X-Hyeve-Project-Id"] == "project-1"
    assert wh.attach_calls[0][2]["X-Project-ID"] == "project-1"
    assert len(wh.read_calls) == 1


@pytest.mark.asyncio
async def test_crash_restart_reuses_sdk_result_and_idempotency():
    store = Store()
    runner = FakeRunner()
    life = Lifecycle(fail=True)
    wh = WorkHub()
    first = AixSdkExecutionWorker(binding(), runner, store, life, wh)
    with pytest.raises(RuntimeError, match="crash"):
        await first.execute(request())
    second = AixSdkExecutionWorker(binding(), runner, store, life, wh)
    out = await second.execute(request())
    assert out["evidence_id"] == "evidence-1"
    assert runner.calls == 1
    assert [c[1]["Idempotency-Key"] for c in life.calls] == ["event-1", "event-1"]
    assert (await second.execute(request())) == out
    assert len(wh.attach_calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change,code",
    [
        ({"tenantId": "x"}, "TENANTID"),
        ({"projectId": "x"}, "PROJECTID"),
        ({"sessionId": "x"}, "SESSIONID"),
        ({"taskVersion": 8}, "TASKVERSION"),
        ({"sourceRevision": "x"}, "SOURCEREVISION"),
    ],
)
async def test_wrong_authority_revision_version_and_session_refuse_before_sdk(
    change, code
):
    runner = FakeRunner()
    worker = AixSdkExecutionWorker(binding(), runner, Store(), Lifecycle(), WorkHub())
    with pytest.raises(RuntimeError, match=code):
        await worker.execute(request(**change))
    assert runner.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,value",
    [
        (("assignment", "taskId"), "other-task"),
        (("assignment", "attemptId"), "other-attempt"),
        (("assignment", "repository", "sourceRevision"), "other-revision"),
        (("contractVersion",), "hyeve.aix/sdk-compilation-profile/v999"),
        (("aixSourceRevision",), "other-aix-revision"),
        (("sdkApiVersion",), "hyeve.agent-sdk/public-v999"),
        (("policy", "adapter", "version"), "other-runtime"),
        (("assignment", "integrationRefs", "aix.tenant"), "other-tenant"),
        (("assignment", "integrationRefs", "aix.project"), "other-project"),
        (("assignment", "integrationRefs", "aix.session"), "other-session"),
        (("assignment", "integrationRefs", "aix.taskVersion"), "8"),
    ],
)
async def test_real_pinned_node_profile_substitution_fails_before_effect(path, value):
    profile = copy.deepcopy(PROFILE)
    target = profile
    for component in path[:-1]:
        target = target[component]
    target[path[-1]] = value
    life, wh = Lifecycle(), WorkHub()
    worker = AixSdkExecutionWorker(binding(), NodeSdkRunner(ROOT), Store(), life, wh)
    with pytest.raises(RuntimeError, match="PROFILE_BINDING_MISMATCH"):
        await worker.execute(request(profile=profile))
    assert life.calls == []
    assert wh.attach_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("taskId", "other-task"),
        ("attemptId", "other-attempt"),
        ("sourceRevision", "other-revision"),
        ("sdkPackageVersion", "0.9.0-substituted"),
        ("runtimeVersion", "0.155.0"),
        ("status", "FAILED"),
        ("providerRequest", True),
        ("credentialUsed", True),
    ],
)
async def test_sdk_output_substitution_fails_before_effect(field, value):
    class SubstitutingRunner(FakeRunner):
        def run(self, payload):
            event = super().run(payload)
            if field in event["payload"]:
                event["payload"][field] = value
            else:
                event[field] = value
            return event

    life, wh = Lifecycle(), WorkHub()
    with pytest.raises(RuntimeError, match="EVENT_BINDING_MISMATCH"):
        await AixSdkExecutionWorker(
            binding(), SubstitutingRunner(), Store(), life, wh
        ).execute(request())
    assert life.calls == []
    assert wh.attach_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changed_binding",
    [
        {"sdk_package_version": "0.9.0-substituted"},
        {"runtime_version": "0.155.0"},
    ],
)
async def test_embedded_version_binding_substitution_fails_before_effect(
    changed_binding,
):
    life, wh = Lifecycle(), WorkHub()
    with pytest.raises(RuntimeError, match="PROFILE_BINDING_MISMATCH"):
        await AixSdkExecutionWorker(
            replace(binding(), **changed_binding),
            NodeSdkRunner(ROOT),
            Store(),
            life,
            wh,
        ).execute(request())
    assert life.calls == []
    assert wh.attach_calls == []


@pytest.mark.asyncio
async def test_changed_replay_and_bad_ack_or_readback_fail_closed():
    store = Store()
    runner = FakeRunner()
    worker = AixSdkExecutionWorker(binding(), runner, store, Lifecycle(), WorkHub())
    await worker.execute(request())
    with pytest.raises(RuntimeError, match="REPLAY_CONFLICT"):
        await worker.execute(request(sequence=2))
    for life, wh, code in [(Lifecycle(bad=True), WorkHub(), "ACK_MISMATCH")]:
        with pytest.raises(RuntimeError, match=code):
            await AixSdkExecutionWorker(
                binding(), FakeRunner(), Store(), life, wh
            ).execute(request())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "corrupt_field",
    [
        "task_id",
        "project_id",
        "evidence_type",
        "evidence_ref",
        "evidence_json",
        "missing_event_field",
    ],
)
async def test_workhub_corrupt_readback_fails_closed(corrupt_field):
    with pytest.raises(RuntimeError, match="READBACK_MISMATCH"):
        await AixSdkExecutionWorker(
            binding(), FakeRunner(), Store(), Lifecycle(), WorkHub(corrupt_field)
        ).execute(request())


@pytest.mark.asyncio
async def test_live_workhub_server_enrichment_is_allowlisted():
    enrichment = {
        "captured_at": "2026-09-22T10:26:33.669018+00:00",
        "correlation_id": "01M34AEAGA4YAXF4WKZMWWECJY",
        "created_at": "2026-09-22T10:26:33.669018+00:00",
        "created_by": "principal-1",
        "evidence_ref": "aix-event:event-1",
        "evidence_type": "result",
        "schema_version": 1,
        "submitted_by": "principal-1",
        "summary": "result",
    }
    out = await AixSdkExecutionWorker(
        binding(), FakeRunner(), Store(), Lifecycle(), WorkHub(enrichment=enrichment)
    ).execute(request())
    assert out["evidence_id"] == "evidence-1"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation",
    [
        {"unknown_server_field": "rejected"},
        {"taskId": "substituted-task"},
        {"payload": {"status": "SUCCEEDED"}},
    ],
)
async def test_workhub_enrichment_rejects_unknown_or_submitted_mutation(mutation):
    with pytest.raises(RuntimeError, match="READBACK_MISMATCH"):
        await AixSdkExecutionWorker(
            binding(), FakeRunner(), Store(), Lifecycle(), WorkHub(enrichment=mutation)
        ).execute(request())


@pytest.mark.asyncio
async def test_concurrent_stale_checkpoint_cannot_replace_final_receipt():
    store = Store()
    await store.claim("event-1", "digest")
    await store.checkpoint("event-1", "new", "sdk_complete", {"event": "sdk"})

    final, stale = await asyncio.gather(
        store.checkpoint(
            "event-1", "sdk_complete", "complete", {"evidence_id": "final"}
        ),
        store.checkpoint("event-1", "sdk_complete", "sdk_complete", {"event": "stale"}),
        return_exceptions=True,
    )

    assert final is None
    assert isinstance(stale, RuntimeError)
    assert str(stale) == "SDK_WORKER_CHECKPOINT_CONFLICT"
    assert store.rows["event-1"]["stage"] == "complete"
    assert store.rows["event-1"]["value"] == {"evidence_id": "final"}


def test_pinned_package_bytes_are_present():
    import hashlib

    p = ROOT / "vendor/hyeve-agent-sdk-0.9.0-pilot.2.tgz"
    assert hashlib.sha256(p.read_bytes()).hexdigest() == SDK_PACKAGE_SHA256
