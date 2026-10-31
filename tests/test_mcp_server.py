"""MCP front door: tool registration + create/show against a live ledger app."""
import asyncio
import threading

import httpx
import pytest
import uvicorn


@pytest.fixture(scope="module")
def ledger_url(tmp_path_factory):
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
    assert names == ["workhub_create_task", "workhub_list_tasks",
                     "workhub_show_task", "workhub_post_message",
                     "workhub_read_messages", "workhub_peer_sessions",
                     "workhub_message_agent", "workhub_agent_windows",
                     "workhub_reap_windows", "rouvr_start_task", "rouvr_schedule"]


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


def test_mcp_create_and_show(monkeypatch, ledger_url, tmp_path):
    monkeypatch.setenv("WORKHUB_BASE_URL", ledger_url)
    monkeypatch.setenv("HIDDEN_TESTS_ROOT", str(tmp_path / "hidden"))
    import importlib
    import rouvr.mcp_server as ms
    importlib.reload(ms)
    task = ms.workhub_create_task("t", "d", ["c1"])
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
    task = ms.workhub_create_task("t-hidden", "do the thing", ["returns 200"])
    manifest = json.loads((tmp_path / "hidden" / str(task["id"]) / "MANIFEST.json").read_text())
    assert manifest["created_by"] == "test_author"
    assert any(name.startswith("test_") for name in manifest["files"])
    from rouvr.hiddentests import assert_coder_clean, fetch_hidden_suite
    hidden_dir, manifest_obj = fetch_hidden_suite(tmp_path / "hidden", str(task["id"]))
    coder_ws = tmp_path / "coder_ws"
    coder_ws.mkdir()
    assert_coder_clean(coder_ws, hidden_dir, manifest_obj)  # coder ws untouched


def test_mcp_agent_messages_roundtrip(monkeypatch, ledger_url, tmp_path):
    monkeypatch.setenv("WORKHUB_BASE_URL", ledger_url)
    monkeypatch.setenv("HIDDEN_TESTS_ROOT", str(tmp_path / "hidden"))
    import importlib
    import rouvr.mcp_server as ms
    importlib.reload(ms)
    ms.workhub_create_task("t2", "d", ["c1"])
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
    t = ms.workhub_create_task("decode-me", "d", ["c1"])
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
    task = ms.workhub_create_task("t-timeout", "d", ["c1"])
    monkeypatch.setattr(ms, "_live_peers",
                        lambda tid, t: [{"role": "coder", "session_id": "sid-9"}])

    def boom(*a, **kw):
        raise subprocess.TimeoutExpired(cmd="muse", timeout=30)

    monkeypatch.setattr(subprocess, "run", boom)
    out = ms.workhub_message_agent(task["id"], "coder", "hi")
    assert out == {"target": "sid-9", "status": "error", "detail": "timeout"}
