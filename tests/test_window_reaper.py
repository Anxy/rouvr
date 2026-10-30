"""Supervisor window management: parse live persona windows, reap only terminal tasks."""
from rouvr.mcp_server import _parse_windows, _stale_windows

PS = """vpsr 111 0.0 0.0 31952 19320 ? SNs 15:00 0:00 /usr/bin/python3 /usr/bin/gnome-terminal --wait --title Rouvr muse aaa -- bash -c cd /tmp/rouvr-supervisor/workspaces/rouvr__7__a1 && exec muse --workspace /tmp/rouvr-supervisor/workspaces/rouvr__7__a1 'x'
vpsr 222 0.0 0.0 31952 19320 ? SNs 15:01 0:00 /usr/bin/python3 /usr/bin/gnome-terminal --wait --title Rouvr muse bbb -- bash -c cd /tmp/rouvr-supervisor/workspaces/rouvr__9__a2 && exec muse --workspace /tmp/rouvr-supervisor/workspaces/rouvr__9__a2 'x'
vpsr 333 0.0 0.0 31952 19320 ? SNs 15:02 0:00 /usr/bin/gnome-terminal-server
"""


def test_parse_windows():
    wins = _parse_windows(PS)
    assert wins == [{"pid": 111, "workspace": "/tmp/rouvr-supervisor/workspaces/rouvr__7__a1", "task_id": 7},
                    {"pid": 222, "workspace": "/tmp/rouvr-supervisor/workspaces/rouvr__9__a2", "task_id": 9}]


def test_stale_only_terminal_states():
    wins = _parse_windows(PS)
    stale = _stale_windows(wins, {7: "done", 9: "claimed"})
    assert [w["task_id"] for w in stale] == [7]
    assert _stale_windows(wins, {7: "needs_review", 9: "blocked"})[0]["task_id"] == 9
