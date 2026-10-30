"""Agent-to-agent messages: file pickup, prompt injection, evidence filtering."""
import json

from rouvr.messages import messages_for, read_message_file
from rouvr.muse import build_task_prompt, build_verifier_prompt


def test_read_message_file(tmp_path):
    (tmp_path / "message-verifier.json").write_text(json.dumps({"text": "note the punct param"}))
    assert read_message_file(tmp_path, "verifier") == "note the punct param"
    assert read_message_file(tmp_path, "builder") == ""
    (tmp_path / "message-builder.json").write_text("not json")
    assert read_message_file(tmp_path, "builder") == ""  # malformed never fails a turn


def test_messages_for_filters_recipient():
    rows = [{"type": "agent_message", "payload": {"from": "verifier", "to": "builder", "text": "cover empty"}},
            {"type": "agent_message", "payload": {"from": "builder", "to": "verifier", "text": "ok"}},
            {"type": "test_run", "payload": {"from": "x", "to": "builder", "text": "ignore"}}]
    assert messages_for(rows, "builder") == ["verifier: cover empty"]


def test_prompts_carry_peer_messages():
    task = {"id": 1, "title": "t", "description": "d", "acceptance_criteria": [{"id": "c1", "text": "x"}]}
    assert "verifier: cover empty" in build_task_prompt(task, "r", "b", "", ["verifier: cover empty"])
    assert "builder: ok" in build_verifier_prompt(task, "diff", "ok", ["builder: ok"])
    assert "message-verifier.json" in build_task_prompt(task, "r", "b", "")
    assert "message-builder.json" in build_verifier_prompt(task, "diff", "ok")
