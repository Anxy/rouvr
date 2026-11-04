"""Step 2: supervisor chat turns a message into a slice with tasks (SSE)."""
import json
import os

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("ROUVR_AUTH_SECRET", "test-secret-for-chat-step-12345678901")
    monkeypatch.setenv("ROUVR_ADMIN_PASSWORD", "chat-admin-password-0123456789")
    monkeypatch.setenv("ROUVR_LEDGER_TOKEN", "stack-secret")
    monkeypatch.delenv("ROUVR_INSECURE_DEV", raising=False)
    monkeypatch.delenv("ROUVR_MODEL_PROVIDER", raising=False)
    monkeypatch.delenv("META_API_KEY", raising=False)
    from ledger.app import make_app

    app = make_app(f"sqlite:///{tmp_path}/chat.db")
    reg = TestClient(app)
    headers = {"x-rouvr-token": "stack-secret"}
    reg.post("/v1/tenants", json={"tenant": "demo"}, headers=headers).raise_for_status()
    reg.post("/v1/projects", json={"tenant": "demo", "project": "demo"},
             headers=headers).raise_for_status()
    return app


@pytest.fixture()
def session_client(app):
    from ledger.app import make_app  # noqa

    c = TestClient(app)
    login = c.post("/auth/login", data={"username": "admin",
                                        "password": "chat-admin-password-0123456789"})
    assert login.status_code == 200, login.text
    return TestClient(app, cookies=login.cookies)


def _frames(text):
    out = []
    for chunk in text.split("\n\n"):
        chunk = chunk.strip()
        if not chunk:
            continue
        event, data = None, None
        for line in chunk.splitlines():
            if line.startswith("event:"):
                event = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data = line[len("data:"):].strip()
        out.append((event, json.loads(data) if data else None))
    return out


def _chat(session_client, message, project="demo", message_id=None):
    body = {"message": message, "project": project, "tenant": "demo"}
    if message_id:
        body["message_id"] = message_id
    return session_client.post("/api/v1/chat", json=body, headers={"Origin": "http://testserver"})


PLAN = {"question": None,
        "slice": {"title": "Ship the demo slice", "intent": "demo end to end",
                  "acceptance_criteria": ["chat creates slice+tasks", "cards in reply"]},
        "tasks": [{"title": "First chore", "intent": "do first"},
                  {"title": "Second chore", "intent": "do second"}]}


def test_chat_anonymous_is_401(app):
    c = TestClient(app)
    r = c.post("/api/v1/chat", json={"message": "hi", "project": "demo", "tenant": "demo"})
    assert r.status_code == 401, (r.status_code, r.text)


def test_chat_question_creates_nothing(session_client):
    before = session_client.get("/api/v1/tasks").json()["items"]
    r = _chat(session_client, json.dumps({"question": "Which project should this go in?",
                                          "slice": None, "tasks": []}),
              message_id="q-only-1")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/event-stream")
    frames = _frames(r.text)
    assert any(e == "token" for e, _ in frames), "must stream token events"
    results = [d for e, d in frames if e == "result"]
    assert len(results) == 1
    res = results[0]
    assert res["question"] == "Which project should this go in?"
    assert res["reply"] == res["question"] and res["created"] == 0
    assert res["message_id"] == "q-only-1"
    after = session_client.get("/api/v1/tasks").json()["items"]
    assert len(after) == len(before)


def test_chat_plan_creates_slice_tasks_receipts_and_cards(session_client):
    r = _chat(session_client, json.dumps(PLAN), message_id="plan-1")
    assert r.status_code == 200, r.text
    res = [d for e, d in _frames(r.text) if e == "result"][0]
    assert res["created"] == 3 and res["question"] is None
    assert res["message_id"] == "plan-1"
    sid = res["slice"]["id"]
    tids = [t["id"] for t in res["tasks"]]
    for tid in [sid] + tids:  # inline task cards carry the real ids
        assert f"#{tid}" in res["reply"], res["reply"]
    assert "Slice #" in res["reply"]
    for tid in [sid] + tids:  # every row carries the message id as source
        got = session_client.get(f"/api/v1/tasks/{tid}").json()
        receipts = [x for x in got["receipts"] if x["action"] == "chat:create"]
        assert receipts, got
        assert json.loads(receipts[0]["body"])["source"] == "plan-1"
    got = session_client.get(f"/api/v1/tasks/{sid}").json()
    assert json.loads(got["contracts"][0]["body"])["acceptance_criteria"] == \
        PLAN["slice"]["acceptance_criteria"]
    kinds = {e["kind"] for e in got["evidence"]}
    assert "slice" in kinds
    kid = session_client.get(f"/api/v1/tasks/{tids[0]}").json()
    member = [e for e in kid["evidence"] if e["kind"] == "slice_member"]
    assert member and json.loads(member[0]["body"])["slice_id"] == sid
    ev = session_client.get("/api/v1/events").text  # real outbox rows, tenant-scoped
    created = [d for e, d in _frames(ev)
               if e not in (None, "error") and isinstance(d, dict) and d.get("type") == "task.created"]
    assert {c["task_id"] for c in created} >= {sid, *tids}


def test_chat_plan_attaches_contract_to_every_child_task(session_client):
    r = _chat(session_client, json.dumps(PLAN), message_id="plan-kids-1")
    assert r.status_code == 200, r.text
    res = [d for e, d in _frames(r.text) if e == "result"][0]
    sid = res["slice"]["id"]
    tids = [t["id"] for t in res["tasks"]]
    assert tids, res
    for tid in tids:  # approve guard needs a contract on each child, not just the slice
        got = session_client.get(f"/api/v1/tasks/{tid}").json()
        assert got["contracts"], f"child task {tid} has no contract"
        criteria = json.loads(got["contracts"][0]["body"])["acceptance_criteria"]
        assert criteria == PLAN["slice"]["acceptance_criteria"], got["contracts"][0]
        assert json.loads(got["contracts"][0]["body"])["slice_id"] == sid


def test_chat_question_wins_over_tasks(session_client):
    before = len(session_client.get("/api/v1/tasks").json()["items"])
    msg = {"question": "Ship it as one task or three?", "slice": PLAN["slice"], "tasks": PLAN["tasks"]}
    r = _chat(session_client, json.dumps(msg), message_id="q-wins-1")
    res = [d for e, d in _frames(r.text) if e == "result"][0]
    assert res["question"] == "Ship it as one task or three?" and res["created"] == 0
    assert len(session_client.get("/api/v1/tasks").json()["items"]) == before


def test_chat_caps_tasks_and_rejects_oversize_message(session_client):
    many = dict(PLAN)
    many["tasks"] = [{"title": f"chore {i}", "intent": "do it"} for i in range(10)]
    r = _chat(session_client, json.dumps(many), message_id="cap-1")
    res = [d for e, d in _frames(r.text) if e == "result"][0]
    assert res["created"] == 9, res  # 1 slice + MAX_TASKS
    r = _chat(session_client, "x" * 4001, message_id="big-1")
    assert r.status_code == 422, (r.status_code, r.text)


def test_chat_http_stream_parses_sse_chunks(monkeypatch):
    import ledger.chat as chatmod
    """The non-stub HTTP branch: canned SSE chunks over a local socket."""
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    chunks = [{"choices": [{"delta": {"content": '{"question": "Which pro'}}]},
              {"choices": [{"delta": {"content": 'ject?", "slice": null, "tasks": []}'}}]}]
    body = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            self.rfile.read(length)
            raw = body.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            # no Content-Length: chunked delivery splits SSE lines mid-frame,
            # which the client must reassemble before parsing
            self.end_headers()
            for i in range(0, len(raw), 7):
                self.wfile.write(raw[i:i + 7])
                self.wfile.flush()

        def log_message(self, *a):
            pass

    server = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        provider = {"name": "meta",
                    "base_url": f"http://127.0.0.1:{server.server_port}",
                    "model": "dummy", "api_key": "dummy-local-key"}
        # split across odd chunk boundaries: the client must reassemble lines
        text = "".join(chatmod.stream_completion(
            [{"role": "user", "content": "does not matter"}], provider))
    finally:
        server.shutdown()
    assert json.loads(text)["question"] == "Which project?"


def test_chat_retries_empty_turn_then_succeeds(session_client, monkeypatch):
    import ledger.chat as chatmod

    calls = {"n": 0}

    def fake_complete(messages, provider):
        calls["n"] += 1
        return "" if calls["n"] < 3 else json.dumps(PLAN)

    monkeypatch.setattr(chatmod, "complete_once", fake_complete)
    r = _chat(session_client, "anything", message_id="retry-1")
    assert r.status_code == 200, r.text
    frames = _frames(r.text)
    attempts = [d["attempt"] for e, d in frames if e == "retrying"]
    assert attempts == [1, 2], frames
    res = [d for e, d in frames if e == "result"][0]
    assert res["created"] == 3 and res["message_id"] == "retry-1"
    got = session_client.get(f"/api/v1/tasks/{res['slice']['id']}").json()
    body = json.loads([x for x in got["receipts"] if x["action"] == "chat:create"][0]["body"])
    assert body["source"] == "retry-1" and body["retries"] == 2


def test_chat_explicit_plan_phrasing_offers_instead_of_converting(session_client, monkeypatch):
    import ledger.chat as chatmod

    plan = dict(PLAN, intake={"class": "task", "confidence": 0.9, "category": "feature"})
    monkeypatch.setattr(chatmod, "complete_once",
                        lambda messages, provider: json.dumps(plan))
    before = len(session_client.get("/api/v1/tasks").json()["items"])
    r = _chat(session_client, "Prepare a plan for simple auth", message_id="explicit-plan-1")
    assert r.status_code == 200, r.text
    res = [d for e, d in _frames(r.text) if e == "result"][0]
    assert res["offer"] and res["offer"]["plan_slug"], res
    assert res["badge"]["kind"] == "saved" and res["badge"]["label"].startswith("Saved: "), res
    assert [t for t in res.get("tasks", [])] == []
    assert len(session_client.get("/api/v1/tasks").json()["items"]) == before


def test_chat_gives_up_when_everything_empty(session_client, monkeypatch):
    import ledger.chat as chatmod

    before = len(session_client.get("/api/v1/tasks").json()["items"])
    monkeypatch.setattr(chatmod, "complete_once", lambda messages, provider: "")
    r = _chat(session_client, "anything", message_id="retry-3")
    assert r.status_code == 200, r.text
    frames = _frames(r.text)
    assert [d for e, d in frames if e == "result"] == []
    assert any(e == "error" for e, _ in frames)
    assert len(session_client.get("/api/v1/tasks").json()["items"]) == before


LIVE = pytest.mark.skipif(not (os.environ.get("META_API_KEY")
                                and os.environ.get("META_MODEL_API_BASE_URL")),
                          reason="live Meta check needs META_API_KEY + META_MODEL_API_BASE_URL")


@LIVE
def test_chat_live_meta_stream(session_client):
    """One real muse-spark-1.3 turn: tokens stream, result parses. Capped."""
    msg = ('Reply with ONLY this JSON, nothing else: '
           '{"question": "Which project?", "slice": null, "tasks": []}')
    r = _chat(session_client, msg, message_id="live-1")
    assert r.status_code == 200, r.text
    frames = _frames(r.text)
    assert any(e == "token" for e, _ in frames), "no streamed tokens from the live model"
    res = [d for e, d in frames if e == "result"][0]
    assert res["message_id"] == "live-1" and res["question"] == "Which project?"
