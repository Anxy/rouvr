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
    assert "--workspace" in joined and "p.md" in joined  # seeded chat, real session


def test_chat_command_no_display_falls_back(monkeypatch, tmp_path):
    monkeypatch.delenv("DISPLAY", raising=False)
    assert chat_command("t", tmp_path, tmp_path / "p.md", "", "muse") is None


def test_launcher_defaults_windows_off():
    assert MuseLauncher("muse").launch_windows is False
