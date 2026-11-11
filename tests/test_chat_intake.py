"""Slice C: intake router + plan confirm gate on POST /v1/chat (SSE)."""
import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("ROUVR_AUTH_SECRET", "test-secret-for-chat-intake-12345678901")
    monkeypatch.setenv("ROUVR_ADMIN_PASSWORD", "chat-intake-password-0123456789")
    monkeypatch.setenv("ROUVR_LEDGER_TOKEN", "stack-secret")
    monkeypatch.delenv("ROUVR_INSECURE_DEV", raising=False)
    monkeypatch.delenv("ROUVR_MODEL_PROVIDER", raising=False)
    monkeypatch.delenv("META_API_KEY", raising=False)
    from ledger.app import make_app

    app = make_app(f"sqlite:///{tmp_path}/intake.db")
    reg = TestClient(app)
    headers = {"x-rouvr-token": "stack-secret"}
    reg.post("/v1/tenants", json={"tenant": "demo"}, headers=headers).raise_for_status()
    reg.post("/v1/projects", json={"tenant": "demo", "project": "demo"},
             headers=headers).raise_for_status()
    return app


@pytest.fixture()
def session_client(app):
    c = TestClient(app)
    login = c.post("/auth/login", data={"username": "admin",
                                        "password": "chat-intake-password-0123456789"})
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


def _chat(session_client, message, project="demo", message_id=None, **extra):
    body = {"message": message, "project": project, "tenant": "demo"}
    if message_id:
        body["message_id"] = message_id
    body.update(extra)
    return session_client.post("/api/v1/chat", json=body, headers={"Origin": "http://testserver"})


def _result(r):
    assert r.status_code == 200, r.text
    res = [d for e, d in _frames(r.text) if e == "result"]
    assert len(res) == 1, r.text
    return res[0]


def _turn_msg(cls="chat", confidence=0.9, category=None, **kw):
    turn = {"question": None, "slice": None, "tasks": [],
            "intake": {"class": cls, "confidence": confidence, "category": category}}
    turn.update(kw)
    return json.dumps(turn)


def _plan_msg(cls="chat", category="feature", n=2, **kw):
    tasks = [{"title": f"step {i}", "intent": f"do step {i}"} for i in range(1, n + 1)]
    turn = {"question": None,
            "slice": {"title": "Ship the thing", "intent": "ship it",
                      "acceptance_criteria": ["it ships"]},
            "tasks": tasks,
            "answer": "here is the plan",
            "intake": {"class": cls, "confidence": 0.8, "category": category}}
    turn.update(kw)
    return json.dumps(turn)


def test_intake_chat_answers_and_logs(session_client):
    r = _chat(session_client, _turn_msg(answer="hello there"), message_id="c-1")
    res = _result(r)
    assert res["reply"] == "hello there"
    assert res["badge"] == {"kind": "logged", "label": "Logged"}
    assert res["classification"] == "chat"
    assert res["autonomy"] == "ask-for-everything"
    turns = session_client.get("/api/v1/chat/turns",
                               params={"project": "demo"}).json()["items"]
    assert [(t["message_id"], t["class"]) for t in turns] == [("c-1", "chat")]
    assert turns[0]["reply"] == "hello there"
    items = session_client.get("/api/v1/chat/classifications",
                               params={"project": "demo"}).json()["items"]
    assert len(items) == 1
    row = items[0]
    assert row["turn_id"] == "c-1" and row["class"] == "chat"
    assert row["confidence"] == pytest.approx(0.9)
    assert row["autonomy_level"] == "ask-for-everything"
    assert row["decided_by"] and row["decided_at"]  # who/when recorded
    assert row["created_ids"] == {}


def test_intake_knowledge_saves_entry(session_client):
    turn = _turn_msg(cls="knowledge", answer="noted it",
                     knowledge={"kind": "decision", "title": "Use sqlite",
                                "body": "sqlite for the ledger"})
    res = _result(_chat(session_client, turn, message_id="k-1"))
    assert res["badge"]["kind"] == "saved" and res["badge"]["label"].startswith("Saved: ")
    assert "Saved: " in res["reply"]
    kid = res["knowledge"]["id"]
    got = session_client.get(f"/api/v1/knowledge/{kid}").json()
    assert got["verified"] == 0 and got["kind"] == "decision"
    assert f"turn-k-1" in got["tags"]
    items = session_client.get("/api/v1/chat/classifications",
                               params={"project": "demo",
                                       "message_id": "k-1"}).json()["items"]
    assert items[0]["created_ids"] == {"knowledge": [kid]}


def test_intake_task_creates_task_with_category_and_contract(session_client):
    turn = _turn_msg(cls="task", category="bugfix", answer="on it",
                     task={"title": "Fix login", "intent": "repair the flow"},
                     contract={"acceptance_criteria": ["login works"]})
    res = _result(_chat(session_client, turn, message_id="t-1"))
    tid = res["tasks"][0]["id"]
    assert res["badge"] == {"kind": "task", "label": f"Task #{tid}"}
    assert f"#{tid}" in res["reply"]
    got = session_client.get(f"/api/v1/tasks/{tid}").json()
    assert got["state"] == "open"
    body = json.loads(got["contracts"][0]["body"])
    assert body["acceptance_criteria"] == ["login works"]
    assert body["category"] == "bugfix"
    receipts = [x for x in got["receipts"] if x["action"] == "chat:create"]
    assert receipts and json.loads(receipts[0]["body"])["source"] == "t-1"


def test_intake_task_plan_creates_first_then_pipeline(session_client):
    res = _result(_chat(session_client, _plan_msg(cls="task", category="ops"),
                        message_id="tp-1"))
    assert res["slice"] is not None and res["created"] == 4  # task + slice + 2 steps
    assert res["badge"]["label"].startswith("Task #")
    first_id = res["tasks"][0]["id"]
    assert res["tasks"][0]["id"] != res["slice"]["id"]
    got = session_client.get(f"/api/v1/knowledge/search",
                             params={"q": "Ship the thing",
                                     "project": "demo"}).json()["items"]
    assert got, "plan entry must exist"
    items = session_client.get("/api/v1/chat/classifications",
                               params={"project": "demo",
                                       "message_id": "tp-1"}).json()["items"]
    created = items[0]["created_ids"]
    assert created["task"] == first_id and created["confirmed"] is True
    plan_entry = session_client.get(f"/api/v1/knowledge/{created['plan_entry']}").json()
    assert plan_entry["verified"] == 1  # imperative confirms the entry


def test_intake_ambiguous_question_and_chat_default(session_client):
    before = session_client.get("/api/v1/tasks").json()["items"]
    r = _chat(session_client, _turn_msg(cls="ambiguous",
                                        question="Which project?"),
              message_id="a-1")
    res = _result(r)
    assert res["question"] == "Which project?" and res["created"] == 0
    assert res["badge"] == {"kind": "logged", "label": "Logged"}
    assert len(session_client.get("/api/v1/tasks").json()["items"]) == len(before)
    r2 = _chat(session_client, _turn_msg(cls="ambiguous", answer="just chatting"),
               message_id="a-2")
    assert _result(r2)["reply"] == "just chatting"  # defaults to chat


def test_plan_offer_then_text_yes_converts(session_client):
    before = len(session_client.get("/api/v1/tasks").json()["items"])
    res = _result(_chat(session_client, _plan_msg(), message_id="p-1"))
    assert res["created"] == 0 and res["slice"] is None
    assert "Shall I turn this into an actionable plan? It becomes 2 tasks:" in res["reply"]
    assert "step 1" in res["reply"] and "step 2" in res["reply"]
    assert res["badge"]["label"].startswith("Saved: plan-")
    offer = res["offer"]
    assert offer["task_titles"] == ["step 1", "step 2"]
    assert len(session_client.get("/api/v1/tasks").json()["items"]) == before  # asked, nothing yet
    entry = session_client.get(f"/api/v1/knowledge/{offer['plan_entry_id']}").json()
    assert entry["verified"] == 0
    res2 = _result(_chat(session_client, "yes", message_id="p-2"))
    assert res2["slice"] is not None and res2["created"] == 3
    assert res2["badge"] == {"kind": "task", "label": f"Task #{res2['slice']['id']}"}
    assert session_client.get(f"/api/v1/knowledge/{offer['plan_entry_id']}").json()["verified"] == 1
    for tid in [res2["slice"]["id"]] + [t["id"] for t in res2["tasks"]]:
        got = session_client.get(f"/api/v1/tasks/{tid}").json()
        assert got["contracts"], f"task {tid} needs a contract"
        assert json.loads(got["contracts"][0]["body"])["plan_slug"] == offer["plan_slug"]


def test_plan_resend_reuses_entry_and_double_convert_is_idempotent(session_client):
    r1 = _result(_chat(session_client, _plan_msg(), message_id="pd-1"))
    r2 = _result(_chat(session_client, _plan_msg(), message_id="pd-1"))
    assert r1["offer"]["plan_entry_id"] == r2["offer"]["plan_entry_id"]
    n_before = len(session_client.get("/api/v1/tasks").json()["items"])
    c1 = _result(_chat(session_client, "convert", message_id="pd-2",
                       plan_action="convert",
                       plan_entry=r1["offer"]["plan_entry_id"]))
    c2 = _result(_chat(session_client, "convert", message_id="pd-3",
                       plan_action="convert",
                       plan_entry=r1["offer"]["plan_entry_id"]))
    assert c1["slice"]["id"] == c2["slice"]["id"]  # one slice, double send
    assert c1["tasks"] == c2["tasks"]
    assert "(already converted" in c2["reply"]
    assert len(session_client.get("/api/v1/tasks").json()["items"]) == n_before + 3


def test_plan_keep_creates_nothing(session_client):
    before = len(session_client.get("/api/v1/tasks").json()["items"])
    res = _result(_chat(session_client, _plan_msg(), message_id="pk-1"))
    res2 = _result(_chat(session_client, "no", message_id="pk-2"))
    assert "no tasks created" in res2["reply"]
    assert res2["badge"]["label"].startswith("Saved: ")
    assert len(session_client.get("/api/v1/tasks").json()["items"]) == before
    assert session_client.get(
        f"/api/v1/knowledge/{res['offer']['plan_entry_id']}").json()["verified"] == 0


def test_plan_edit_versions_then_reoffers(session_client):
    res = _result(_chat(session_client, _plan_msg(), message_id="pe-1"))
    old_id = res["offer"]["plan_entry_id"]
    revised = json.dumps({"question": None,
                          "slice": {"title": "Ship the thing", "intent": "ship it well",
                                    "acceptance_criteria": ["it ships"]},
                          "tasks": [{"title": "step one renamed", "intent": "do it"}],
                          "intake": {"class": "chat", "confidence": 1.0,
                                     "category": "feature"}})
    res2 = _result(_chat(session_client, revised, message_id="pe-2",
                         plan_action="edit", plan_entry=old_id))
    assert res2["offer"]["plan_entry_id"] != old_id
    assert res2["offer"]["task_titles"] == ["step one renamed"]
    assert "It becomes 1 tasks:" in res2["reply"]
    new_entry = session_client.get(f"/api/v1/knowledge/{res2['offer']['plan_entry_id']}").json()
    assert new_entry["version"] == 2
    hist = session_client.get(
        f"/api/v1/knowledge/{res2['offer']['plan_entry_id']}/history").json()["items"]
    assert [h["id"] for h in hist] == [old_id, res2["offer"]["plan_entry_id"]]
    # the revised offer still converts
    res3 = _result(_chat(session_client, "yes", message_id="pe-3"))
    assert res3["slice"] is not None and res3["created"] == 2


def test_only_important_autoconverts_docs_but_asks_feature(session_client):
    session_client.post("/api/v1/chat/autonomy",
                        json={"tenant": "demo", "project": "demo",
                              "autonomy": "only-important"},
                        headers={"Origin": "http://testserver"}).raise_for_status()
    docs = _result(_chat(session_client, _plan_msg(category="docs"), message_id="oi-1"))
    assert docs["slice"] is not None  # auto-converted
    feat = _result(_chat(session_client, _plan_msg(category="feature"), message_id="oi-2"))
    assert feat["slice"] is None and feat["offer"] is not None  # asked
    session_client.post("/api/v1/chat/autonomy",
                        json={"tenant": "demo", "project": "demo",
                              "autonomy": "ask-for-everything"},
                        headers={"Origin": "http://testserver"}).raise_for_status()


def test_free_autoconverts_and_body_override_works(session_client):
    session_client.post("/api/v1/chat/autonomy",
                        json={"tenant": "demo", "project": "demo",
                              "autonomy": "free"},
                        headers={"Origin": "http://testserver"}).raise_for_status()
    res = _result(_chat(session_client, _plan_msg(category="ops"), message_id="fr-1"))
    assert res["slice"] is not None and res["autonomy"] == "free"
    session_client.post("/api/v1/chat/autonomy",
                        json={"tenant": "demo", "project": "demo",
                              "autonomy": "ask-for-everything"},
                        headers={"Origin": "http://testserver"}).raise_for_status()
    res2 = _result(_chat(session_client, _plan_msg(category="ops"), message_id="fr-2",
                         autonomy="free"))
    assert res2["slice"] is not None and res2["autonomy"] == "free"
    res3 = _result(_chat(session_client, _plan_msg(category="ops"), message_id="fr-3"))
    assert res3["offer"] is not None  # stored default asks again


def test_autonomy_endpoints_and_validation(session_client):
    got = session_client.get("/api/v1/chat/autonomy",
                             params={"project": "demo"}).json()
    assert got["autonomy"] == "ask-for-everything" and got["default"] is True
    put = session_client.post("/api/v1/chat/autonomy",
                              json={"tenant": "demo", "project": "demo",
                                    "autonomy": "free"},
                              headers={"Origin": "http://testserver"}).json()
    assert put["autonomy"] == "free" and put["updated_by"]
    got2 = session_client.get("/api/v1/chat/autonomy",
                              params={"project": "demo"}).json()
    assert got2["autonomy"] == "free" and got2["default"] is False
    bad = session_client.post("/api/v1/chat/autonomy",
                              json={"tenant": "demo", "project": "demo",
                                    "autonomy": "yolo"},
                              headers={"Origin": "http://testserver"})
    assert bad.status_code == 422
    bad2 = _chat(session_client, _turn_msg(answer="x"), message_id="ax-1",
                 autonomy="yolo")
    assert bad2.status_code == 422
    bad3 = _chat(session_client, "convert", message_id="ax-2", plan_action="frobnicate")
    assert bad3.status_code == 422


def test_reclassify_appends_and_lists(session_client):
    _result(_chat(session_client, _turn_msg(answer="hi"), message_id="rc-1"))
    re = session_client.post("/api/v1/chat/reclassify",
                             json={"tenant": "demo", "project": "demo",
                                   "message_id": "rc-1", "class": "knowledge",
                                   "confidence": 0.7},
                             headers={"Origin": "http://testserver"}).json()
    assert re["class"] == "knowledge" and re["reclass_of"] is not None
    items = session_client.get("/api/v1/chat/classifications",
                               params={"project": "demo",
                                       "message_id": "rc-1"}).json()["items"]
    assert [i["class"] for i in items] == ["chat", "knowledge"]
    assert items[1]["id"] == re["id"] and items[1]["reclass_of"] == items[0]["id"]
    missing = session_client.post("/api/v1/chat/reclassify",
                                  json={"tenant": "demo", "project": "demo",
                                        "message_id": "nope", "class": "chat"},
                                  headers={"Origin": "http://testserver"})
    assert missing.status_code == 404
    bad = session_client.post("/api/v1/chat/reclassify",
                              json={"tenant": "demo", "project": "demo",
                                    "message_id": "rc-1", "class": "frobnicate"},
                              headers={"Origin": "http://testserver"})
    assert bad.status_code == 422


def test_plan_action_errors_without_pending(session_client):
    r = _chat(session_client, "yes", message_id="nx-1")
    assert any(e == "error" for e, _ in _frames(r.text))  # nothing pending: model turn fails
    r2 = _chat(session_client, "convert", message_id="nx-2",
               plan_action="convert", plan_entry=999999)
    assert any(e == "error" for e, _ in _frames(r2.text))
    r3 = _chat(session_client, "edit", message_id="nx-3",
               plan_action="edit", plan_entry=999999)
    assert any(e == "error" for e, _ in _frames(r3.text))
    assert session_client.get("/api/v1/tasks").json()["items"] == []


def test_legacy_plan_without_intake_still_direct(session_client):
    plan = {"question": None,
            "slice": {"title": "Legacy slice", "intent": "legacy",
                      "acceptance_criteria": ["works"]},
            "tasks": [{"title": "chore", "intent": "do it"}]}
    res = _result(_chat(session_client, json.dumps(plan), message_id="leg-1"))
    assert res["created"] == 2 and res["slice"] is not None
    assert "classification" not in res  # legacy path carries no intake fields


def test_intake_task_resend_returns_same_task(session_client):
    turn = _turn_msg(cls="task", category="bugfix", answer="on it",
                     task={"title": "Fix login", "intent": "repair the flow"},
                     contract={"acceptance_criteria": ["login works"]})
    first = _result(_chat(session_client, turn, message_id="t-dup"))
    tid = first["tasks"][0]["id"]
    before = session_client.get("/api/v1/tasks").json()["items"]
    second = _result(_chat(session_client, turn, message_id="t-dup"))
    assert second["tasks"][0]["id"] == tid
    assert "already created" in second["reply"]
    after = session_client.get("/api/v1/tasks").json()["items"]
    assert [t["id"] for t in after] == [t["id"] for t in before]
    items = session_client.get("/api/v1/chat/classifications",
                               params={"project": "demo",
                                       "message_id": "t-dup"}).json()["items"]
    assert items[-1]["created_ids"].get("replayed") is True


def test_intake_knowledge_resend_returns_same_entry(session_client):
    turn = _turn_msg(cls="knowledge", answer="noted it",
                     knowledge={"kind": "decision", "title": "Use sqlite",
                                "body": "sqlite for the ledger"})
    first = _result(_chat(session_client, turn, message_id="k-dup"))
    kid = first["knowledge"]["id"]
    second = _result(_chat(session_client, turn, message_id="k-dup"))
    assert second["knowledge"]["id"] == kid
    assert "already saved" in second["reply"]


def test_identity_block_contains_tools_skills_rules_and_cap():
    from ledger import chat as chatmod

    tools = [{"name": f"tool-{i}", "summary": "does things"} for i in range(60)]
    skills = [{"slug": f"skill-{i}", "title": f"Skill {i}",
               "blurb": "when to use it"} for i in range(10)]
    rules = [f"knowledge:{i}@v1" for i in range(10)]
    block = chatmod.identity_block(tools, skills, 42, [f"Task {i}" for i in range(6)],
                                   rules, "acme", "site")
    assert chatmod.IDENTITY_MARKER in block
    assert "tenant 'acme', project 'site'" in block
    assert "tool-0" in block and "skill-0" in block and "knowledge:0@v1" in block
    assert "Open tasks (42)" in block
    assert len(block) <= chatmod.IDENTITY_CAP + 30  # cap + truncation note
    forced = chatmod.identity_block(tools, skills, 42, [f"Task {i}" for i in range(6)],
                                    rules, "acme", "site", cap=200)
    assert len(forced) <= 230 and "truncated at 200 chars" in forced
    short = chatmod.identity_block(tools[:2], [], 0, [], [], "acme", "site")
    assert "truncated at" not in short and "(none)" in short


def test_live_tool_manifest_equals_registry():
    import asyncio

    from ledger import chat as chatmod
    from rouvr.mcp_server import mcp

    live = {t.name for t in asyncio.run(mcp.list_tools())}
    assert {t["name"] for t in chatmod.live_tool_manifest()} == live
    assert live, "registry must not be empty"


def test_chat_system_prompt_carries_manifest_and_skills(session_client, monkeypatch):
    from ledger import chat as chatmod

    seen = {}
    real = chatmod.complete_once

    def capture(messages, provider, **kw):
        seen["system"] = messages[0]["content"]
        return real(messages, provider, **kw)

    monkeypatch.setattr(chatmod, "complete_once", capture)
    session_client.post("/api/v1/knowledge/store",
                        json={"tenant": "demo", "project": "demo",
                              "items": [{"kind": "skill", "title": "Probe skill",
                                         "body": "use it for probing"}]},
                        headers={"Origin": "http://testserver"})
    turn = _turn_msg(cls="chat", answer="hello there")
    res = _result(_chat(session_client, turn, message_id="id-1"))
    assert res["reply"] == "hello there"
    system = seen["system"]
    assert chatmod.IDENTITY_MARKER in system
    assert "workhub_create_task" in system  # a real live tool name
    assert "probe-skill" in system  # the seeded skill slug
    assert "tenant 'demo', project 'demo'" in system


def test_supervisor_and_planner_surfaces_keep_separate_logs(session_client):
    _result(_chat(session_client, _turn_msg(answer="from supervisor"),
                  message_id="sup-1", surface="supervisor"))
    _result(_chat(session_client, _turn_msg(answer="from planner"),
                  message_id="plan-1", surface="planner"))
    # Default list is supervisor-only (legacy callers + Supervisor UI).
    sup = session_client.get("/api/v1/chat/turns",
                             params={"project": "demo"}).json()["items"]
    assert [(t["message_id"], t["surface"]) for t in sup] == [("sup-1", "supervisor")]
    plan = session_client.get(
        "/api/v1/chat/turns",
        params={"project": "demo", "surface": "planner"},
    ).json()["items"]
    assert [(t["message_id"], t["surface"]) for t in plan] == [("plan-1", "planner")]
