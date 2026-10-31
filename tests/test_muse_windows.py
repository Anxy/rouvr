"""Interactive chat windows: argv construction + headless fallback."""
from pathlib import Path

import rouvr.muse as muse_mod
from rouvr.muse import MuseLauncher, chat_command


def test_chat_command_gnome_terminal(monkeypatch, tmp_path):
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr("shutil.which", lambda t: "/usr/bin/" + t)
    argv = chat_command("Rouvr muse abc", tmp_path, tmp_path / "p.md", "", "muse")
    assert argv[0] == "gnome-terminal" and "--wait" in argv and "Rouvr muse abc" in argv
    joined = " ".join(argv)
    assert "--workspace" in joined and "--yolo" in joined and "p.md" in joined  # seeded chat, real session


def test_chat_command_strips_all_worker_secrets(monkeypatch, tmp_path):
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr("shutil.which", lambda t: "/usr/bin/" + t)
    joined = " ".join(chat_command("t", tmp_path, tmp_path / "p.md", "", "muse"))
    for var in ("GH_TOKEN", "ROUVR_LEDGER_TOKEN", "META_MODEL_API_KEY", "WORKHUB_JWT"):
        assert f"-u {var}" in joined  # window agent never inherits worker secrets


def test_chat_command_no_display_falls_back(monkeypatch, tmp_path):
    monkeypatch.delenv("DISPLAY", raising=False)
    assert chat_command("t", tmp_path, tmp_path / "p.md", "", "muse") is None


def test_launcher_defaults_windows_off():
    assert MuseLauncher("muse").launch_windows is False


def test_discover_session_id(tmp_path):
    from rouvr.muse import discover_session_id
    day = tmp_path / "2026" / "10" / "30" / "sess-uuid-1"
    day.mkdir(parents=True)
    (day / "session.jsonl").write_text('{"workspace_root":"/ws/one"}')
    assert discover_session_id(Path("/ws/one"), tmp_path) == "sess-uuid-1"
    assert discover_session_id(Path("/ws/other"), tmp_path) == ""


def test_discover_session_id_verify_dir_does_not_match_coder(tmp_path):
    # Real (unmocked) discovery: the newer reviewer log names the __verify
    # workspace, which has the coder workspace as a path prefix.
    import json as _json
    import os
    from rouvr.muse import discover_session_id
    coder_ws = tmp_path / "rouvr__7__a2"
    verify_ws = tmp_path / "rouvr__7__a2__verify"
    day = tmp_path / "2026" / "10" / "30"
    for name, ws, age in (("coder-uuid", coder_ws, 100), ("verify-uuid", verify_ws, 200)):
        d = day / name
        d.mkdir(parents=True)
        (d / "session.jsonl").write_text(_json.dumps({"workspace_root": str(ws)}) + "\n")
        os.utime(d, (age, age))  # verify session is the newer one
    assert discover_session_id(coder_ws, tmp_path) == "coder-uuid"
    assert discover_session_id(verify_ws, tmp_path) == "verify-uuid"


def test_ready_line_from_persona_prompts():
    from rouvr.muse import ready_line
    coder = "# Persona: Coder (coder)\n\n---\n\nTask 14: Add a helper\n\ndetails"
    reviewer = "# Persona: Reviewer (reviewer)\n\n---\n\nReview task 14: Add a helper\n\ncriteria"
    assert ready_line(coder) == "Coder ready. Working on task 14: Add a helper"
    assert ready_line(reviewer) == "Reviewer ready. Reviewing task 14: Add a helper"
    assert ready_line("plain prompt") == "Agent ready."


def test_chat_seed_inlines_received_messages(monkeypatch, tmp_path):
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr("shutil.which", lambda t: "/usr/bin/" + t)
    p = tmp_path / "p.md"
    p.write_text("Task 1: X\n\nMessages from other agents — show each:\n"
                 "- supervisor: please demo the encore\n- coder: noted\n\nOther section\n")
    joined = " ".join(chat_command("t", tmp_path, p, "", "muse", role="Coder"))
    assert "Message from supervisor : please demo the encore" in joined
    assert "Message from coder : noted" in joined


def test_chat_seed_is_supervisor_style(monkeypatch, tmp_path):
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr("shutil.which", lambda t: "/usr/bin/" + t)
    argv = chat_command("t", tmp_path, tmp_path / "p.md", "", "muse", role="Coder")
    joined = " ".join(argv)
    assert "You are the Rouvr Coder." in joined
    assert "say exactly" not in joined
    assert "Greet the supervisor briefly" in joined
    assert "Message from <sender>" in joined


def test_chat_window_scrubs_credential_env(monkeypatch, tmp_path):
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr("shutil.which", lambda t: "/usr/bin/" + t)
    joined = " ".join(chat_command("t", tmp_path, tmp_path / "p.md", "", "muse"))
    assert "env -u GH_TOKEN -u ROUVR_LEDGER_TOKEN" in joined


async def test_headless_turn_drains_stderr_and_unlinks_prompt(tmp_path):
    script = tmp_path / "fake-muse"
    script.write_text("#!/bin/sh\n"
                      "python3 -c \"import sys; sys.stderr.write('x' * 300000)\"\n"
                      "printf '%s\\n' '{\"payload_type\": \"run.terminal.completed\", \"payload\": {\"text\": \"done\"}}'\n")
    script.chmod(0o755)
    ws = tmp_path / "ws"
    ws.mkdir()
    turn = await MuseLauncher(str(script)).run(prompt="hi", model="", cwd=ws, env={}, timeout_s=30)
    assert turn.last_message == "done"
    assert not list(tmp_path.glob(".rouvr-prompt-*.md"))


async def test_headless_hung_exit_is_killed_after_stdout_closes(tmp_path):
    import time

    import pytest
    from rouvr.errors import RouvrError
    script = tmp_path / "fake-muse"
    script.write_text("#!/bin/sh\n"
                      "printf '%s\\n' '{\\\"payload_type\\\": \\\"run.terminal.completed\\\", \\\"payload\\\": {\\\"text\\\": \\\"done\\\"}}'\n"
                      "exec 1>&-\n"
                      "sleep 60\n")  # stdout closed, process lingers: wait() must not hang
    script.chmod(0o755)
    ws = tmp_path / "ws"
    ws.mkdir()
    start = time.monotonic()
    with pytest.raises(RouvrError):
        await MuseLauncher(str(script), kill_grace_s=0.2).run(
            prompt="hi", model="", cwd=ws, env={}, timeout_s=120)
    assert time.monotonic() - start < 30  # grace + slack, not the 60 s sleep


async def test_headless_failure_keeps_stderr_tail(tmp_path):
    import pytest
    from rouvr.errors import RouvrError
    script = tmp_path / "fake-muse"
    script.write_text("#!/bin/sh\n"
                      "python3 -c \"import sys; sys.stderr.write('x' * 300000 + 'y' * 100000)\"\n"
                      "exit 3\n")
    script.chmod(0o755)
    ws = tmp_path / "ws"
    ws.mkdir()
    with pytest.raises(RouvrError) as ei:
        await MuseLauncher(str(script)).run(prompt="hi", model="", cwd=ws, env={}, timeout_s=30)
    assert "y" * 100 in str(ei.value) and "x" * 100 not in str(ei.value)  # tail, not head


async def test_chat_turn_zero_exit_without_output_raises(monkeypatch, tmp_path):
    import pytest
    from rouvr.errors import Code, RouvrError
    monkeypatch.setattr(muse_mod, "discover_session_id", lambda cwd, root=None: "")
    launcher = MuseLauncher("muse")
    with pytest.raises(RouvrError) as ei:
        await launcher._run_chat(["/bin/sh", "-c", "exit 0"], "sid-1", tmp_path, {}, 30, None)
    assert ei.value.code == Code.DOWNSTREAM


async def test_chat_turn_nonzero_exit_without_output_raises(monkeypatch, tmp_path):
    import pytest
    from rouvr.errors import Code, RouvrError
    monkeypatch.setattr(muse_mod, "discover_session_id", lambda cwd, root=None: "")
    launcher = MuseLauncher("muse")
    with pytest.raises(RouvrError) as ei:
        await launcher._run_chat(["/bin/sh", "-c", "exit 1"], "sid-1", tmp_path, {}, 30, None)
    assert ei.value.code == Code.DOWNSTREAM
    (tmp_path / "submit.json").write_text("{}")
    turn = await launcher._run_chat(["/bin/sh", "-c", "exit 1"], "sid-1", tmp_path, {}, 30, None)
    assert turn.exit_code == 0  # output landed: watcher-killed chat still counts as done


def test_clone_env_uses_only_stack_token(monkeypatch):
    from rouvr.gitops import clone_env
    monkeypatch.setenv("GH_TOKEN", "x")
    monkeypatch.setenv("ROUVR_LEDGER_TOKEN", "y")
    env = clone_env()
    assert env["GH_TOKEN"] == "x" and "ROUVR_LEDGER_TOKEN" not in env
    assert env["GIT_TERMINAL_PROMPT"] == "0"
