"""FL-A4: outbox + SSE events. Catalog delivery (c1) and the kill-worker
guarantee (c2): a dead worker yields silence — the UI derives stuck/idle
from heartbeat absence — never fake progress."""
import hashlib
import json
import os
import threading
import time

import pytest

from fastapi.testclient import TestClient


_ABSENT = object()


def _apply_env(**values):
    saved = {k: os.environ.get(k, _ABSENT) for k in values}
    for k, v in values.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    return saved


def _restore_env(saved):
    for k, v in saved.items():
        if v is _ABSENT:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


@pytest.fixture(autouse=True)
def _clean_env():
    saved = _apply_env(ROUVR_INSECURE_DEV="1", ROUVR_LEDGER_TOKEN=None)
    try:
        yield
    finally:
        _restore_env(saved)


def _app(tmp_path, name="e.db", token=None):
    if token:
        _apply_env(ROUVR_LEDGER_TOKEN=token, ROUVR_INSECURE_DEV=None)
    from ledger.app import make_app
    return make_app(f"sqlite:///{tmp_path}/{name}")


def _seed(c, tenant="acme", project="site", headers=None):
    assert c.post("/v1/tenants", json={"tenant": tenant}, headers=headers).status_code == 200
    r = c.post("/v1/projects", json={"tenant": tenant, "project": project}, headers=headers)
    assert r.status_code == 200, r.text


def _frame(block):
    eid, etype, data = None, None, []
    for line in block.splitlines():
        if line.startswith("id:"):
            eid = line[3:].strip()
        elif line.startswith("event:"):
            etype = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].strip())
        elif line.startswith(":") or line.startswith("retry:") or not line.strip():
            continue
        else:
            return None
    if eid is None or etype is None or not data:
        return None
    return {"id": int(eid), "type": etype, "data": json.loads("".join(data))}


def _parse(text):
    return [e for e in (_frame(b) for b in text.split("\n\n")) if e]


def _stream(c, path="/v1/events?live=0", headers=None):
    r = c.get(path, headers=headers)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/event-stream")
    return _parse(r.text)


def _mkuser(c, stack, user, tok, tenant):
    h = hashlib.sha256(tok.encode()).hexdigest()
    r = c.post("/v1/users", json={"username": user, "token_hash": h, "tenant": tenant},
               headers=stack)
    assert r.status_code == 200, r.text


def test_event_catalog_covers_plan_section_4():
    from ledger.app import EVENT_CATALOG
    plan = ["task.created", "task.transition", "task.step", "task.tests", "task.criteria",
            "attempt.started", "attempt.finished", "review.note", "receipt.signed",
            "escalation.opened", "escalation.resolved", "knowledge.versioned",
            "knowledge.confirmed", "worker.heartbeat"]
    for t in plan:
        assert t in EVENT_CATALOG, t
    assert "task.evidence" in EVENT_CATALOG  # documented generic fallback


def test_task_lifecycle_emits_catalog_events(tmp_path, monkeypatch):
    c = TestClient(_app(tmp_path))
    _seed(c)
    t = c.post("/v1/tasks", json={"title": "t", "intent": "i", "tenant": "acme",
                                  "project": "site"}).json()
    tid = t["id"]
    c.post(f"/v1/tasks/{tid}/contracts", json={"acceptance_criteria": ["a"]})
    c.post(f"/v1/tasks/{tid}/evidence", json={"kind": "check", "body": {}})
    assert c.post(f"/v1/tasks/{tid}/transition",
                  json={"to": "claimed", "idempotency_key": "k1"}).status_code == 200
    w = c.post(f"/v1/tasks/{tid}/assignments/claim",
               json={"key": "c1", "principal": "w1", "attempt_no": 1}).json()
    a = c.post(f"/v1/tasks/{tid}/attempts",
               json={"key": "a1", "attempt_no": 1,
                     "lease_epoch": w["lease_epoch"]}).json()["attempt"]
    assert c.post(f"/v1/tasks/{tid}/results",
                  json={"key": "r1", "attempt_id": a["id"],
                        "lease_epoch": w["lease_epoch"], "verdict": "pass",
                        "result": {}}).status_code == 200
    assert c.post(f"/v1/tasks/{tid}/leases/renew",
                  json={"key": "rn1", "holder": "w1",
                        "expected_lease_epoch": w["lease_epoch"]}).status_code == 200
    evs = _stream(c)
    by_type = {}
    for e in evs:
        by_type.setdefault(e["type"], []).append(e["data"])
    assert by_type["task.created"][0]["payload"] == {
        "state": "open", "task_id": tid, "title": "t"}
    # transition endpoint fired; the later claim found the task already
    # claimed, so exactly one task.transition exists
    assert [p["payload"] for p in by_type["task.transition"]] == [
        {"from": "open", "owner": None, "task_id": tid, "to": "claimed"}]
    assert by_type["attempt.started"][0]["payload"]["attempt_id"] == a["id"]
    assert by_type["attempt.finished"][0]["payload"]["verdict"] == "pass"
    hb = by_type["worker.heartbeat"][0]["payload"]
    assert hb["holder"] == "w1" and hb["lease_expires_at"]
    # envelope shape on every event
    for e in evs:
        assert set(e["data"]) == {"created_at", "id", "payload", "project",
                                  "task_id", "tenant", "type"}
        assert e["data"]["tenant"] == "acme" and e["id"] == e["data"]["id"]


def test_claim_from_open_emits_transition(tmp_path, monkeypatch):
    c = TestClient(_app(tmp_path))
    _seed(c)
    tid = c.post("/v1/tasks", json={"title": "t", "intent": "i", "tenant": "acme",
                                    "project": "site"}).json()["id"]
    c.post(f"/v1/tasks/{tid}/assignments/claim",
           json={"key": "c1", "principal": "w1", "attempt_no": 1})
    evs = _stream(c)
    tr = [e for e in evs if e["type"] == "task.transition"]
    assert len(tr) == 1
    assert tr[0]["data"]["payload"] == {"from": "open", "owner": "w1",
                                        "task_id": tid, "to": "claimed"}


def test_evidence_kinds_map_to_catalog_events(tmp_path, monkeypatch):
    c = TestClient(_app(tmp_path))
    _seed(c)
    tid = c.post("/v1/tasks", json={"title": "t", "intent": "i", "tenant": "acme",
                                    "project": "site"}).json()["id"]
    cases = [("step", "task.step", {"step": 2, "total": 5}),
             ("tests", "task.tests", {"passed": 41, "total": 44}),
             ("test_output", "task.tests", {"passed": 1, "total": 1}),
             ("criteria", "task.criteria", {"done": 3, "total": 5, "flipped": ["c3"]}),
             ("reviewer", "review.note", {"verdict": "request_changes"}),
             ("receipt", "receipt.signed", {"sha": "9f2c"}),
             ("escalation", "escalation.opened", {"reason": "stuck"}),
             ("escalation_resolved", "escalation.resolved", {"by": "owner"}),
             ("ops_receipt", "task.step", {"digest": "d"}),
             ("note", "task.step", {"x": 1})]
    for kind, _want, body in cases:
        assert c.post(f"/v1/tasks/{tid}/evidence",
                      json={"kind": kind, "body": body}).status_code == 200
    evs = _stream(c)
    got = [(e["type"], e["data"]["payload"]) for e in evs if e["type"] != "task.created"]
    assert len(got) == len(cases)
    for (kind, want, body), (etype, payload) in zip(cases, got):
        assert etype == want, kind
        assert payload["evidence_id"] > 0
        for k, v in body.items():
            assert payload[k] == v, (kind, k)



def test_knowledge_emits_versioned_confirmed_leak_safe(tmp_path, monkeypatch):
    c = TestClient(_app(tmp_path))
    _seed(c)
    k = c.post("/v1/knowledge", json={"tenant": "acme", "project": "site", "kind": "note",
                                      "title": "SECRET TITLE", "body": "secret body",
                                      "visibility": "restricted"}).json()
    c.post(f"/v1/knowledge/{k['id']}/versions",
           json={"title": "SECRET TITLE v2", "body": "secret body v2"})
    c.post(f"/v1/knowledge/{k['id'] + 1}/confirm")
    evs = _stream(c)
    kinds = [e["type"] for e in evs]
    assert kinds.count("knowledge.versioned") == 2
    assert kinds.count("knowledge.confirmed") == 1
    raw = json.dumps(evs)
    assert "SECRET" not in raw and "secret body" not in raw
    v1 = [e for e in evs if e["type"] == "knowledge.versioned"][0]["data"]
    assert v1["payload"]["citation"] == f"knowledge:{k['id']}@v1"
    assert v1["task_id"] is None


def test_heartbeat_endpoint_validates_and_enriches(tmp_path, monkeypatch):
    c = TestClient(_app(tmp_path))
    _seed(c)
    assert c.post("/v1/workers/heartbeat", json={"holder": "w"}).status_code == 422
    assert c.post("/v1/workers/heartbeat", json={"task_id": 1}).status_code == 422
    assert c.post("/v1/workers/heartbeat",
                  json={"task_id": 999, "holder": "w"}).status_code == 404
    tid = c.post("/v1/tasks", json={"title": "t", "intent": "i", "tenant": "acme",
                                    "project": "site"}).json()["id"]
    r = c.post("/v1/workers/heartbeat", json={"task_id": tid, "holder": "w1"})
    assert r.status_code == 200 and r.json()["type"] == "worker.heartbeat"
    evs = _stream(c)
    hb = [e for e in evs if e["type"] == "worker.heartbeat"][0]["data"]["payload"]
    assert hb["holder"] == "w1" and hb["lease_expires_at"] is None  # no lease yet
    w = c.post(f"/v1/tasks/{tid}/assignments/claim",
               json={"key": "c1", "principal": "w1", "attempt_no": 1}).json()
    c.post("/v1/workers/heartbeat", json={"task_id": tid, "holder": "w1"})
    evs = _stream(c)
    hb2 = [e for e in evs if e["type"] == "worker.heartbeat"][-1]["data"]["payload"]
    assert hb2["lease_expires_at"] == w["lease"]["expires_at"]
    assert hb2["lease_holder"] == "w1"


def test_cursor_replay_and_last_event_id(tmp_path, monkeypatch):
    c = TestClient(_app(tmp_path))
    _seed(c)
    for i in range(3):
        c.post("/v1/tasks", json={"title": f"t{i}", "intent": "i", "tenant": "acme",
                                  "project": "site"})
    full = _stream(c)
    assert len(full) == 3 and [e["id"] for e in full] == sorted(e["id"] for e in full)
    mid = full[1]["id"]
    tail = _stream(c, f"/v1/events?live=0&cursor={mid}")
    assert [e["id"] for e in tail] == [full[2]["id"]]
    assert _stream(c, f"/v1/events?live=0&cursor={full[2]['id']}") == []
    hdr = _stream(c, "/v1/events?live=0", headers={"Last-Event-ID": str(mid)})
    assert [e["id"] for e in hdr] == [full[2]["id"]]
    # explicit cursor wins over the header
    both = _stream(c, "/v1/events?live=0&cursor=0", headers={"Last-Event-ID": str(mid)})
    assert len(both) == 3
    for bad in ("abc", "-1", "1.5", "1;DROP"):
        assert c.get(f"/v1/events?live=0&cursor={bad}").status_code == 422, bad
    assert c.get("/v1/events?live=0", headers={"Last-Event-ID": "nope"}).status_code == 422


def test_events_are_per_tenant_server_side(tmp_path, monkeypatch):
    app = _app(tmp_path, token="stack-secret")
    stack = {"X-Rouvr-Token": "stack-secret"}
    with TestClient(app) as c:
        _seed(c, "a", "p", stack)
        _seed(c, "b", "p", stack)
        _mkuser(c, stack, "alice", "a-tok", "a")
        _mkuser(c, stack, "bob", "b-tok", "b")
        ta = c.post("/v1/tasks", json={"title": "ta", "intent": "i", "tenant": "a",
                                       "project": "p"}, headers=stack).json()["id"]
        tb = c.post("/v1/tasks", json={"title": "tb", "intent": "i", "tenant": "b",
                                       "project": "p"}, headers=stack).json()["id"]
        ha, hb = {"X-Rouvr-Token": "a-tok"}, {"X-Rouvr-Token": "b-tok"}
        ea = _stream(c, headers=ha)
        assert {e["data"]["tenant"] for e in ea} == {"a"}
        assert {e["data"]["payload"]["task_id"] for e in ea} == {ta}
        eb = _stream(c, headers=hb)
        assert {e["data"]["tenant"] for e in eb} == {"b"}
        assert {e["data"]["payload"]["task_id"] for e in eb} == {tb}
        es = _stream(c, headers=stack)
        assert {e["data"]["tenant"] for e in es} == {"a", "b"}
        # scope never comes from args: a forged tenant param changes nothing
        forged = _stream(c, "/v1/events?live=0&tenant=b", headers=ha)
        assert {e["data"]["tenant"] for e in forged} == {"a"}
        assert c.get("/v1/events?live=0").status_code == 401
        assert c.get("/api/v1/events?live=0").status_code == 401


def test_replays_emit_no_duplicate_events(tmp_path, monkeypatch):
    c = TestClient(_app(tmp_path))
    _seed(c)
    tid = c.post("/v1/tasks", json={"title": "t", "intent": "i", "tenant": "acme",
                                    "project": "site"}).json()["id"]
    for _ in range(2):
        c.post(f"/v1/tasks/{tid}/transition",
               json={"to": "claimed", "idempotency_key": "k1"})
    w = c.post(f"/v1/tasks/{tid}/assignments/claim",
               json={"key": "c1", "principal": "w1", "attempt_no": 1}).json()
    c.post(f"/v1/tasks/{tid}/assignments/claim",
           json={"key": "c1", "principal": "w1", "attempt_no": 1})
    a = c.post(f"/v1/tasks/{tid}/attempts",
               json={"key": "a1", "attempt_no": 1,
                     "lease_epoch": w["lease_epoch"]}).json()["attempt"]
    c.post(f"/v1/tasks/{tid}/attempts",
           json={"key": "a1", "attempt_no": 1, "lease_epoch": w["lease_epoch"]})
    res = {"key": "r1", "attempt_id": a["id"], "lease_epoch": w["lease_epoch"],
           "verdict": "pass", "result": {}}
    c.post(f"/v1/tasks/{tid}/results", json=res)
    c.post(f"/v1/tasks/{tid}/results", json=res)
    rn = {"key": "rn1", "holder": "w1", "expected_lease_epoch": w["lease_epoch"]}
    c.post(f"/v1/tasks/{tid}/leases/renew", json=rn)
    c.post(f"/v1/tasks/{tid}/leases/renew", json=rn)  # replay: stored lease unexpired
    evs = _stream(c)
    kinds = [e["type"] for e in evs]
    assert kinds.count("task.transition") == 1
    assert kinds.count("attempt.started") == 1
    assert kinds.count("attempt.finished") == 1
    assert kinds.count("worker.heartbeat") == 1  # fresh renew only


def test_kill_worker_stream_goes_quiet_never_fake_progress(tmp_path, monkeypatch):
    """c2: after the worker dies the stream carries nothing new — no
    synthesized step/tests/criteria — and the heartbeat series freezes, so
    the UI derives stuck (claimed + silent) instead of showing motion."""
    c = TestClient(_app(tmp_path))
    _seed(c)
    tid = c.post("/v1/tasks_with_contract",
                 json={"title": "t", "intent": "i", "tenant": "acme", "project": "site",
                       "contract": {"acceptance_criteria": ["a"]}}).json()["id"]
    w = c.post(f"/v1/tasks/{tid}/assignments/claim",
               json={"key": "c1", "principal": "w1", "attempt_no": 1,
                     "ttl_s": 3600}).json()
    c.post("/v1/workers/heartbeat", json={"task_id": tid, "holder": "w1"})
    c.post(f"/v1/tasks/{tid}/evidence",
           json={"kind": "step", "body": {"step": 1, "total": 3}})
    before = _stream(c)
    types = [e["type"] for e in before]
    assert "task.transition" in types and "worker.heartbeat" in types
    assert "task.step" in types
    cursor = before[-1]["id"]
    last_hb = [e for e in before if e["type"] == "worker.heartbeat"][-1]
    assert last_hb["data"]["payload"]["lease_expires_at"] == w["lease"]["expires_at"]
    # the worker dies here: no more heartbeats, evidence, or renewals.
    # expiry + stack reclaim still synthesize nothing worker-like.
    from sqlalchemy import create_engine, text
    eng = create_engine(f"sqlite:///{tmp_path}/e.db")
    with eng.begin() as cx:
        cx.execute(text("UPDATE leases SET expires_at='2000-01-01T00:00:00+00:00'"))
    assert c.post(f"/v1/tasks/{tid}/assignments/reclaim",
                  json={"key": "rc1"}).status_code == 200
    after = _stream(c, f"/v1/events?live=0&cursor={cursor}")
    assert after == []
    progress = {"task.step", "task.tests", "task.criteria"}
    assert not (progress & {e["type"] for e in after})


def test_api_v1_alias_matches_v1(tmp_path, monkeypatch):
    c = TestClient(_app(tmp_path))
    _seed(c)
    c.post("/v1/tasks", json={"title": "t", "intent": "i", "tenant": "acme",
                              "project": "site"})
    a = c.get("/v1/events?live=0")
    b = c.get("/api/v1/events?live=0")
    assert a.status_code == b.status_code == 200
    assert _parse(a.text) == _parse(b.text)
    assert b.headers["content-type"].startswith("text/event-stream")
    assert b.text.startswith("retry: 3000")
    tid = _parse(a.text)[0]["data"]["payload"]["task_id"]
    r = c.post("/api/v1/workers/heartbeat", json={"task_id": tid, "holder": "w"})
    assert r.status_code == 200


def test_live_tail_delivers_new_event(tmp_path, monkeypatch):
    from ledger.app import make_app
    app = make_app(f"sqlite:///{tmp_path}/live.db")
    c = TestClient(app)
    _seed(c)
    tid = c.post("/v1/tasks", json={"title": "t", "intent": "i", "tenant": "acme",
                                    "project": "site"}).json()["id"]
    seen: list = []

    def reader():
        try:
            client2 = TestClient(app)
            with client2.stream("GET", "/v1/events?live=1") as r:
                buf = ""
                for chunk in r.iter_text():
                    buf += chunk
                    while "\n\n" in buf:
                        block, buf = buf.split("\n\n", 1)
                        e = _frame(block)
                        if e:
                            seen.append(e)
                            if e["type"] == "task.tests":
                                return
        except Exception as exc:  # noqa: BLE001 — surface in the assertion
            seen.append({"type": "__error__", "data": {"error": repr(exc)}})

    th = threading.Thread(target=reader, daemon=True)
    th.start()
    deadline = time.monotonic() + 10
    while not [e for e in seen if e["type"] == "task.created"] \
            and time.monotonic() < deadline:
        time.sleep(0.05)
    assert [e for e in seen if e["type"] == "task.created"], seen
    c.post(f"/v1/tasks/{tid}/evidence",
           json={"kind": "tests", "body": {"passed": 41, "total": 44}})
    th.join(timeout=15)
    assert not th.is_alive(), "live tail never delivered the new event"
    tails = [e for e in seen if e["type"] == "task.tests"]
    assert len(tails) == 1
    assert tails[0]["data"]["payload"]["passed"] == 41
