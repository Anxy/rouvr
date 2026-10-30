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


def test_chat_command_no_display_falls_back(monkeypatch, tmp_path):
    monkeypatch.delenv("DISPLAY", raising=False)
    assert chat_command("t", tmp_path, tmp_path / "p.md", "", "muse") is None


def test_launcher_defaults_windows_off():
    assert MuseLauncher("muse").launch_windows is False


def test_discover_session_id(tmp_path):
    from rouvr.muse import discover_session_id
    day = tmp_path / "2026" / "10" / "30" / "sess-uuid-1"
    day.mkdir(parents=True)
    (day / "session.jsonl").write_text('{"x":"\\"workspace_root\\":\\"/ws/one\\"}"')
    assert discover_session_id(Path("/ws/one"), tmp_path) == "sess-uuid-1"
    assert discover_session_id(Path("/ws/other"), tmp_path) == ""


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


def test_clone_env_uses_only_stack_token(monkeypatch):
    from rouvr.gitops import clone_env
    monkeypatch.setenv("GH_TOKEN", "x")
    monkeypatch.setenv("ROUVR_LEDGER_TOKEN", "y")
    env = clone_env()
    assert env["GH_TOKEN"] == "x" and "ROUVR_LEDGER_TOKEN" not in env
    assert env["GIT_TERMINAL_PROMPT"] == "0"
