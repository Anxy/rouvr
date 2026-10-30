"""Agent-to-agent messages: file pickup, prompt injection, evidence filtering."""
import json

from rouvr.messages import messages_for, read_message_file
from rouvr.muse import build_task_prompt, build_reviewer_prompt


def test_read_message_file(tmp_path):
    (tmp_path / "message-reviewer.json").write_text(json.dumps({"text": "note the punct param"}))
    assert read_message_file(tmp_path, "reviewer") == "note the punct param"
    assert read_message_file(tmp_path, "coder") == ""
    (tmp_path / "message-coder.json").write_text("not json")
    assert read_message_file(tmp_path, "coder") == ""  # malformed never fails a turn


def test_messages_for_filters_recipient():
    rows = [{"type": "agent_message", "payload": {"from": "reviewer", "to": "coder", "text": "cover empty"}},
            {"type": "agent_message", "payload": {"from": "coder", "to": "reviewer", "text": "ok"}},
            {"type": "test_run", "payload": {"from": "x", "to": "coder", "text": "ignore"}}]
    assert messages_for(rows, "coder") == ["reviewer: cover empty"]


def test_prompts_carry_peer_messages():
    task = {"id": 1, "title": "t", "description": "d", "acceptance_criteria": [{"id": "c1", "text": "x"}]}
    assert "reviewer: cover empty" in build_task_prompt(task, "r", "b", "", ["reviewer: cover empty"])
    assert "coder: ok" in build_reviewer_prompt(task, "diff", "ok", ["coder: ok"])
    assert "message-reviewer.json" in build_task_prompt(task, "r", "b", "")
    assert "message-coder.json" in build_reviewer_prompt(task, "diff", "ok")
