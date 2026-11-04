"""MCP front door: tool registration + create/show against a live ledger app."""
import asyncio
import threading

import httpx
import pytest
import uvicorn


@pytest.fixture(scope="module")
def ledger_url(tmp_path_factory, monkeypatch_factory=None):
    from env_scope import scoped_env

    with scoped_env(ROUVR_INSECURE_DEV="1", ROUVR_LEDGER_TOKEN=None):
        from ledger.app import make_app
        db = tmp_path_factory.mktemp("mcpledger") / "l.db"
        config = uvicorn.Config(make_app(f"sqlite:///{db}"), host="127.0.0.1", port=18221,
                                log_level="error")
        server = uvicorn.Server(config)
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        for _ in range(100):
            try:
                if httpx.get("http://127.0.0.1:18221/health", timeout=1).status_code == 200:
                    break
            except httpx.TransportError:
                pass
            import time; time.sleep(0.1)
        yield "http://127.0.0.1:18221"
        server.should_exit = True


async def test_mcp_tools_registered():
    from rouvr.mcp_server import mcp
    names = [t.name for t in await mcp.list_tools()]
    assert names == ["workhub_create_tenant", "workhub_create_project",
                     "knowledge_create", "knowledge_search", "knowledge_get",
                     "knowledge_history", "knowledge_update", "knowledge_confirm",
                     "knowledge_store", "knowledge_compact", "knowledge_snapshot",
                     "knowledge_store_and_close", "knowledge_curate",
                     "workhub_create_task", "workhub_health", "workhub_list_tasks",
                     "knowledge_context_bundle_tool",
                     "workhub_show_task", "workhub_retire_task",
                     "workhub_post_message",
                     "workhub_read_messages", "workhub_peer_sessions",
                     "workhub_message_agent", "workhub_agent_windows",
                     "workhub_reap_windows", "rouvr_start_task", "rouvr_schedule",
                     "rouvr_schedule_ops"]


def test_parse_windows_matches_muse_and_terminal_not_server():
    from rouvr.mcp_server import _parse_windows
    ps = ("vpsr 111 0.0 0.1 100 100 ? Ssl 10:00 0:01 /usr/libexec/gnome-terminal-server\n"
          "vpsr 222 0.0 0.1 100 100 ? Sl 10:00 0:01 muse-bin --yolo --workspace /tmp/w/rouvr__7__a1 seed text\n"
          "vpsr 333 0.0 0.1 100 100 ? Sl 10:00 0:01 gnome-terminal --wait -- bash -c cd /tmp/w/rouvr__8__a1 && exec muse\n"
          "vpsr 444 0.0 0.1 100 100 ? Sl 10:00 0:01 muse-bin --workspace /home/user/project chat\n"
          "vpsr 555 0.0 0.1 100 100 ? Sl 10:00 0:01 /bin/bash -c grep rouvr__7__a1 /tmp/x; sleep 1\n")
    wins = _parse_windows(ps)
    assert [(w["pid"], w["task_id"]) for w in wins] == [(222, 7), (333, 8)]


def test_stale_windows_terminal_and_orphan_only():
    from rouvr.mcp_server import _stale_windows
    wins = [{"pid": 1, "task_id": 1}, {"pid": 2, "task_id": 2}, {"pid": 3, "task_id": 3},
            {"pid": 4, "task_id": 4}, {"pid": 5, "task_id": 5}]
    states = {1: "done", 2: "blocked", 3: "claimed", 4: "needs_review"}  # 5 = gone from WorkHub
    assert [w["pid"] for w in _stale_windows(wins, states)] == [1, 2, 5]


_FAKE_SUITE = {"test_authored.py": "def test_authored_criterion():\n    assert 2 + 2 == 4\n"}


def _ensure_scope(ledger_url, tenant="acme", project="site"):
    """S022: register the partition every create below runs inside."""
    import httpx
    with httpx.Client(base_url=ledger_url, timeout=10) as c:
        c.post("/v1/tenants", json={"tenant": tenant}).raise_for_status()
        c.post("/v1/projects", json={"tenant": tenant, "project": project}).raise_for_status()


def _inject_fake_author(monkeypatch, ms, files=None):
    """Deterministic stand-in for the test_author model turn."""
    body = dict(files) if files is not None else dict(_FAKE_SUITE)
    monkeypatch.setattr(ms, "_author_hidden_suite", lambda criteria: body)


def test_mcp_create_and_show(monkeypatch, ledger_url, tmp_path):
    monkeypatch.setenv("WORKHUB_BASE_URL", ledger_url)
    monkeypatch.setenv("HIDDEN_TESTS_ROOT", str(tmp_path / "hidden"))
    import importlib
    import rouvr.mcp_server as ms
    importlib.reload(ms)
    _inject_fake_author(monkeypatch, ms)
    _ensure_scope(ledger_url)
    task = ms.workhub_create_task("t", "d", ["c1"], "acme", "site")
    assert task["state"] == "open" and task["id"] == 1
    shown = ms.workhub_show_task(1)
    assert shown["contracts"] and shown["contracts"][0]["body"]


def test_mcp_create_writes_hidden_suite_as_test_author(monkeypatch, ledger_url, tmp_path):
    import json
    monkeypatch.setenv("WORKHUB_BASE_URL", ledger_url)
    monkeypatch.setenv("HIDDEN_TESTS_ROOT", str(tmp_path / "hidden"))
    import importlib
    import rouvr.mcp_server as ms
    importlib.reload(ms)
    _inject_fake_author(monkeypatch, ms)
    _ensure_scope(ledger_url)
    task = ms.workhub_create_task("t-hidden", "do the thing", ["returns 200"], "acme", "site")
    manifest = json.loads((tmp_path / "hidden" / str(task["id"]) / "MANIFEST.json").read_text())
    assert manifest["created_by"] == "test_author"
    assert any(name.startswith("test_") for name in manifest["files"])
    body = (tmp_path / "hidden" / str(task["id"]) / "test_authored.py").read_text()
    assert "assert True" not in body  # authored checks, never placeholders
    assert "assert 2 + 2 == 4" in body
    from rouvr.hiddentests import assert_coder_clean, fetch_hidden_suite
    hidden_dir, manifest_obj = fetch_hidden_suite(tmp_path / "hidden", str(task["id"]))
    coder_ws = tmp_path / "coder_ws"
    coder_ws.mkdir()
    assert_coder_clean(coder_ws, hidden_dir, manifest_obj)  # coder ws untouched


def test_mcp_create_refuses_empty_criteria_before_any_write(monkeypatch, ledger_url, tmp_path):
    """G2: no criteria means no sealable suite, so the create is refused
    before any ledger write — the test step never hits 'no hidden suite'."""
    import httpx

    monkeypatch.setenv("WORKHUB_BASE_URL", ledger_url)
    monkeypatch.setenv("HIDDEN_TESTS_ROOT", str(tmp_path / "hidden"))
    import importlib
    import rouvr.mcp_server as ms
    importlib.reload(ms)
    _ensure_scope(ledger_url)
    with httpx.Client(base_url=ledger_url, timeout=10) as c:
        before = c.get("/v1/tasks").json()["items"]
    with pytest.raises(RuntimeError, match="acceptance criterion"):
        ms.workhub_create_task("t-empty", "do the thing", [], "acme", "site")
    with httpx.Client(base_url=ledger_url, timeout=10) as c:
        after = c.get("/v1/tasks").json()["items"]
    assert after == before  # refused before any ledger write


def test_mcp_agent_messages_roundtrip(monkeypatch, ledger_url, tmp_path):
    monkeypatch.setenv("WORKHUB_BASE_URL", ledger_url)
    monkeypatch.setenv("HIDDEN_TESTS_ROOT", str(tmp_path / "hidden"))
    import importlib
    import rouvr.mcp_server as ms
    importlib.reload(ms)
    _inject_fake_author(monkeypatch, ms)
    _ensure_scope(ledger_url)
    ms.workhub_create_task("t2", "d", ["c1"], "acme", "site")
    ms.workhub_post_message(1, "coder", "reviewer", "used punct param name from criteria")
    ms.workhub_post_message(1, "reviewer", "coder", "please also cover empty name")
    assert ms.workhub_read_messages(1, "reviewer") == [
        {"from": "coder", "to": "reviewer", "text": "used punct param name from criteria"}]
    assert len(ms.workhub_read_messages(1)) == 2


def test_live_peers_finds_later_attempts(tmp_path, monkeypatch):
    import rouvr.muse as muse_mod
    from rouvr.mcp_server import _live_peers
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setattr(muse_mod, "discover_session_id", lambda ws: f"sid-{ws.name}")
    (tmp_path / "rouvr__7__a2").mkdir()
    (tmp_path / "rouvr__7__a2__verify").mkdir()
    peers = _live_peers(7, {"evidence": []})
    assert [(p["role"], p["session_id"]) for p in peers] == [
        ("coder", "sid-rouvr__7__a2"), ("reviewer", "sid-rouvr__7__a2__verify")]


def test_annotate_stuck_only_for_stale_nonterminal():
    from datetime import datetime, timedelta, timezone
    from rouvr.mcp_server import _annotate
    old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    fresh = datetime.now(timezone.utc).isoformat()
    assert _annotate({"state": "claimed", "updated_at": old, "evidence": []})["stuck"] is True
    assert _annotate({"state": "done", "updated_at": old, "evidence": []})["stuck"] is False
    assert _annotate({"state": "claimed", "updated_at": fresh, "evidence": []})["stuck"] is False


def test_prose_warning_only_for_unpromoted_instructions():
    from rouvr.mcp_server import _prose_warnings
    assert _prose_warnings("Add f. Also leave a note for the reviewer.", ["f works"])
    assert _prose_warnings("Add f.", ["f works"]) == []
    assert _prose_warnings("Add f and message the reviewer.", ["message sent to reviewer"]) == []


def test_show_task_decodes_evidence(monkeypatch, ledger_url, tmp_path):
    monkeypatch.setenv("WORKHUB_BASE_URL", ledger_url)
    monkeypatch.setenv("HIDDEN_TESTS_ROOT", str(tmp_path / "hidden"))
    import importlib
    import rouvr.mcp_server as ms
    importlib.reload(ms)
    _inject_fake_author(monkeypatch, ms)
    _ensure_scope(ledger_url)
    t = ms.workhub_create_task("decode-me", "d", ["c1"], "acme", "site")
    ms.workhub_post_message(t["id"], "coder", "reviewer", "hello")
    shown = ms.workhub_show_task(t["id"])
    msg = [e for e in shown["evidence"] if e.get("kind") == "agent_message"][0]
    assert msg["payload"] == {"from": "coder", "to": "reviewer", "text": "hello"}
    assert shown["stuck"] is False


def test_message_agent_timeout_returns_error_status(monkeypatch, ledger_url, tmp_path):
    monkeypatch.setenv("WORKHUB_BASE_URL", ledger_url)
    monkeypatch.setenv("HIDDEN_TESTS_ROOT", str(tmp_path / "hidden"))
    import importlib
    import subprocess

    import rouvr.mcp_server as ms
    importlib.reload(ms)
    _inject_fake_author(monkeypatch, ms)
    _ensure_scope(ledger_url)
    task = ms.workhub_create_task("t-timeout", "d", ["c1"], "acme", "site")
    monkeypatch.setattr(ms, "_live_peers",
                        lambda tid, t: [{"role": "coder", "session_id": "sid-9"}])

    def boom(*a, **kw):
        raise subprocess.TimeoutExpired(cmd="muse", timeout=30)

    monkeypatch.setattr(subprocess, "run", boom)
    out = ms.workhub_message_agent(task["id"], "coder", "hi")
    assert out == {"target": "sid-9", "status": "error", "detail": "timeout"}


def test_mcp_create_refuses_without_model(monkeypatch, ledger_url, tmp_path):
    """F1: no model, no placeholders — create fails closed, nothing written."""
    monkeypatch.setenv("WORKHUB_BASE_URL", ledger_url)
    monkeypatch.setenv("HIDDEN_TESTS_ROOT", str(tmp_path / "hidden"))
    monkeypatch.setenv("ROUVR_MODEL_PROVIDER", "stub")
    import importlib
    import rouvr.mcp_server as ms
    importlib.reload(ms)
    with pytest.raises(RuntimeError, match="ROUVR_MODEL_PROVIDER"):
        ms.workhub_create_task("t-refused", "d", ["c1"], "acme", "site")
    assert not (tmp_path / "hidden").exists()


def test_mcp_model_env_loader_prefers_explicit_env(monkeypatch, tmp_path):
    """Observability fix: the loader fills missing creds from a file but
    never overrides explicit env, and ignores a missing file (stub stays)."""
    import os
    import rouvr.mcp_server as ms
    saved = {k: os.environ.get(k)
             for k in ("ROUVR_MODEL_PROVIDER", "META_API_KEY", "HIDDEN_TESTS_ROOT")}
    try:
        f = tmp_path / "meta.env"
        f.write_text("export ROUVR_MODEL_PROVIDER=meta\nexport META_API_KEY=k\n")
        os.environ.pop("ROUVR_MODEL_PROVIDER", None)
        os.environ.pop("META_API_KEY", None)
        os.environ.pop("HIDDEN_TESTS_ROOT", None)
        ms._load_local_model_env(str(f))
        assert os.environ["ROUVR_MODEL_PROVIDER"] == "meta"
        assert os.environ["HIDDEN_TESTS_ROOT"].endswith(os.path.join(".data", "hidden-tests"))
        os.environ["ROUVR_MODEL_PROVIDER"] = "stub"  # explicit env wins
        ms._load_local_model_env(str(f))
        assert os.environ["ROUVR_MODEL_PROVIDER"] == "stub"
        os.environ.pop("ROUVR_MODEL_PROVIDER", None)
        ms._load_local_model_env(str(tmp_path / "nope.env"))  # missing: no crash
        assert "ROUVR_MODEL_PROVIDER" not in os.environ
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_mcp_health_reports_stub_instead_of_breaking(monkeypatch, ledger_url, tmp_path):
    """Observability: with no model, the probe reports provider=stub and a
    reachable ledger instead of raising — silence becomes a visible row."""
    monkeypatch.setenv("WORKHUB_BASE_URL", ledger_url)
    monkeypatch.setenv("HIDDEN_TESTS_ROOT", str(tmp_path / "hidden"))
    monkeypatch.setenv("ROUVR_MODEL_PROVIDER", "stub")
    import importlib
    import rouvr.mcp_server as ms
    importlib.reload(ms)
    status = ms.workhub_health()
    assert status["ledger"] == "ok"
    assert status["provider"] == "stub"
    assert status["hidden_tests_writable"] is None  # root not created yet


def test_mcp_author_rejects_malformed_model_output(monkeypatch):
    """F1: unparsable model output or bad filenames refuse, never partial."""
    import rouvr.mcp_server as ms

    class Garbage:
        name = "meta"
        def chat(self, messages, **kw):
            return "not json{"
    monkeypatch.setattr("backend.rouvr_models.providers.get_provider", lambda *a, **k: Garbage())
    with pytest.raises(RuntimeError, match="[Jj]son|pars"):
        ms._author_hidden_suite(["c1"])

    class Evil:
        name = "meta"
        def chat(self, messages, **kw):
            return '{"../evil.py": "x"}'
    monkeypatch.setattr("backend.rouvr_models.providers.get_provider", lambda *a, **k: Evil())
    with pytest.raises(RuntimeError, match="[Ff]lat|name"):
        ms._author_hidden_suite(["c1"])

    class Plain:
        name = "meta"
        def chat(self, messages, **kw):
            return '{"test_durations.py": "def test_x():\\n    assert 1\\n"}'
    monkeypatch.setattr("backend.rouvr_models.providers.get_provider", lambda *a, **k: Plain())
    with pytest.raises(RuntimeError, match="test_rouvr_hidden_"):
        ms._author_hidden_suite(["c1"])


def _compliant_suite(n=5, cite="[c1]", name="test_rouvr_hidden_x.py"):
    import json
    lines = []
    for i in range(1, n + 1):
        lines += [f"def test_x{i}():  # {cite}",
                  f"    assert {i} + {i} == {2 * i}", ""]
    return json.dumps({name: "\n".join(lines)})


def test_mcp_author_rejects_wrong_test_count(monkeypatch):
    """Bounded suite: fewer than 5 or more than 8 tests refuse."""
    import rouvr.mcp_server as ms

    class Few:
        name = "meta"
        def chat(self, messages, **kw):
            return _compliant_suite(n=3)
    monkeypatch.setattr("backend.rouvr_models.providers.get_provider", lambda *a, **k: Few())
    with pytest.raises(RuntimeError, match="5-8"):
        ms._author_hidden_suite(["c1"])


def test_mcp_author_rejects_missing_criterion_cite(monkeypatch):
    """Every criterion needs a citing test."""
    import rouvr.mcp_server as ms

    class Uncited:
        name = "meta"
        def chat(self, messages, **kw):
            return _compliant_suite(n=5, cite="# no criterion here")
    monkeypatch.setattr("backend.rouvr_models.providers.get_provider", lambda *a, **k: Uncited())
    with pytest.raises(RuntimeError, match="cites criterion"):
        ms._author_hidden_suite(["c1"])


def test_mcp_author_rejects_second_file(monkeypatch):
    """Exactly one test file per suite."""
    import json
    import rouvr.mcp_server as ms

    class Two:
        name = "meta"
        def chat(self, messages, **kw):
            first = json.loads(_compliant_suite())
            first["test_rouvr_hidden_y.py"] = "def test_y1():  # [c1]\n    assert 1\n"
            return json.dumps(first)
    monkeypatch.setattr("backend.rouvr_models.providers.get_provider", lambda *a, **k: Two())
    with pytest.raises(RuntimeError, match="exactly one"):
        ms._author_hidden_suite(["c1"])


def test_mcp_author_retries_once_on_length_then_seals(monkeypatch):
    """Length truncation retries once concise, then seals."""
    import rouvr.mcp_server as ms
    calls = []

    class Truncating:
        name = "meta"
        def chat_complete(self, messages, **kw):
            calls.append(messages)
            if len(calls) == 1:
                return {"content": '{"test_rouvr_hidden_x.py": "def test_x', "finish_reason": "length"}
            return {"content": _compliant_suite(), "finish_reason": "stop"}
    monkeypatch.setattr("backend.rouvr_models.providers.get_provider", lambda *a, **k: Truncating())
    files = ms._author_hidden_suite(["c1"])
    assert list(files) == ["test_rouvr_hidden_x.py"]
    assert len(calls) == 2
    assert "concise" in calls[1][-1]["content"].lower()


def test_mcp_author_fails_closed_after_second_length(monkeypatch):
    """Two truncations in a row refuse with a clear reason."""
    import rouvr.mcp_server as ms

    class AlwaysLong:
        name = "meta"
        def chat_complete(self, messages, **kw):
            return {"content": '{"half": ', "finish_reason": "length"}
    monkeypatch.setattr("backend.rouvr_models.providers.get_provider", lambda *a, **k: AlwaysLong())
    with pytest.raises(RuntimeError, match="twice"):
        ms._author_hidden_suite(["c1"])


def test_mcp_author_uses_extended_timeout(monkeypatch):
    """Suite generation is the longest create-path call; it must allow slow models."""
    import json
    import rouvr.mcp_server as ms
    seen = {}
    lines = []
    for i in range(1, 6):
        lines += [f"def test_x{i}():  # [c1]",
                  f"    assert {i} + {i} == {2 * i}", ""]
    suite = {"test_rouvr_hidden_x.py": "\n".join(lines)}

    class Patient:
        name = "meta"
        def chat(self, messages, **kw):
            seen.update(kw)
            return json.dumps(suite)
    monkeypatch.setattr("backend.rouvr_models.providers.get_provider", lambda *a, **k: Patient())
    files = ms._author_hidden_suite(["c1"])
    assert list(files) == ["test_rouvr_hidden_x.py"]
    assert seen.get("timeout", 0) > 60
    assert seen.get("reasoning_effort") == "low"


def test_waiting_lists_escalation_reason_from_json_string_body(monkeypatch, ledger_url, tmp_path):
    """F3: ledger string bodies parse; malformed bodies fall back, never raise."""
    import json
    import httpx
    monkeypatch.setenv("WORKHUB_BASE_URL", ledger_url)
    monkeypatch.setenv("HIDDEN_TESTS_ROOT", str(tmp_path / "hidden"))
    import importlib
    import rouvr.mcp_server as ms
    importlib.reload(ms)
    _inject_fake_author(monkeypatch, ms)
    _ensure_scope(ledger_url)
    t1 = ms.workhub_create_task("t-reason", "d", ["c1"], "acme", "site")
    httpx.post(f"{ledger_url}/v1/tasks/{t1['id']}/evidence",
               json={"kind": "escalation", "fence": "e:1",
                     "body": {"reason": "rework_exhausted"}}).raise_for_status()
    httpx.post(f"{ledger_url}/v1/tasks/{t1['id']}/transition",
               json={"to": "blocked", "idempotency_key": "k1"}).raise_for_status()
    t2 = ms.workhub_create_task("t-badbody", "d", ["c1"], "acme", "site")
    httpx.post(f"{ledger_url}/v1/tasks/{t2['id']}/evidence",
               json={"kind": "escalation", "fence": "e:2", "body": "not json{"}).raise_for_status()
    httpx.post(f"{ledger_url}/v1/tasks/{t2['id']}/transition",
               json={"to": "blocked", "idempotency_key": "k2"}).raise_for_status()
    waiting = {w["task_id"]: w["reason"] for w in ms.workhub_list_tasks()["waiting_on_supervisor"]}
    assert waiting[t1["id"]] == "escalation:rework_exhausted"
    assert waiting[t2["id"]] == "blocked"


def test_mcp_http_auth_gate(monkeypatch):
    """F2b: no token, no serve — unless explicit ROUVR_INSECURE_DEV=1."""
    import rouvr.mcp_server as ms
    monkeypatch.setenv("ROUVR_LEDGER_TOKEN", "tok")
    monkeypatch.delenv("ROUVR_INSECURE_DEV", raising=False)
    monkeypatch.delenv("ROUVR_ALLOW_ANON", raising=False)
    assert ms._mcp_http_auth() == "tok"
    monkeypatch.delenv("ROUVR_LEDGER_TOKEN", raising=False)
    monkeypatch.setenv("ROUVR_INSECURE_DEV", "1")
    assert ms._mcp_http_auth() == ""
    monkeypatch.delenv("ROUVR_INSECURE_DEV", raising=False)
    monkeypatch.delenv("ROUVR_ALLOW_ANON", raising=False)
    with pytest.raises(RuntimeError, match="ROUVR_LEDGER_TOKEN"):
        ms._mcp_http_auth()


def test_create_attaches_decomposition_evidence(monkeypatch, ledger_url, tmp_path):
    """T21.5: create calls the decomposer; enrichment recorded, coder blind."""
    monkeypatch.setenv("WORKHUB_BASE_URL", ledger_url)
    monkeypatch.setenv("HIDDEN_TESTS_ROOT", str(tmp_path / "hidden"))
    import importlib
    import rouvr.mcp_server as ms
    importlib.reload(ms)
    _inject_fake_author(monkeypatch, ms)
    candidate = {"requirements": [{"id": "R1", "text": "t", "source_ref": "s"}],
                 "tasks": [{"id": "T1"}], "coverage_ledger": {"R1": ["T1"]},
                 "dropped_content": []}
    monkeypatch.setattr(ms, "_decompose_brief", lambda *a: candidate)
    _ensure_scope(ledger_url)
    task = ms.workhub_create_task("t-decomp", "do the thing", ["returns 200"], "acme", "site")
    detail = ms.workhub_show_task(task["id"])
    kinds = [e.get("kind") for e in detail.get("evidence") or []]
    assert "decomposition" in kinds
    bodies = [e.get("body") for e in detail.get("evidence") or []
              if e.get("kind") == "decomposition"]
    assert bodies and bodies[0].get("status") == "enriched"
    assert bodies[0]["candidate"] == candidate


def test_create_records_skipped_decomposition_without_model(monkeypatch, ledger_url, tmp_path):
    """T21.5: no model for decompose -> recorded skip, suite still enforced."""
    monkeypatch.setenv("WORKHUB_BASE_URL", ledger_url)
    monkeypatch.setenv("HIDDEN_TESTS_ROOT", str(tmp_path / "hidden"))
    monkeypatch.setenv("ROUVR_MODEL_PROVIDER", "stub")
    import importlib
    import rouvr.mcp_server as ms
    importlib.reload(ms)
    _inject_fake_author(monkeypatch, ms)

    def boom(*a):
        raise RuntimeError("no model")
    monkeypatch.setattr(ms, "_decompose_brief", boom)
    _ensure_scope(ledger_url)
    task = ms.workhub_create_task("t-decomp-skip", "d", ["c1"], "acme", "site")
    detail = ms.workhub_show_task(task["id"])
    bodies = [e.get("body") for e in detail.get("evidence") or []
              if e.get("kind") == "decomposition"]
    assert bodies and bodies[0].get("status") == "skipped"
