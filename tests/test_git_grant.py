"""Git credential grant: supervisor-only, file-based, never from persona creds."""
import json
import os
from pathlib import Path
from types import SimpleNamespace

from rouvr.activities import Activities


def _acts(tmp_path):
    acts = object.__new__(Activities)
    acts.h = SimpleNamespace(settings=SimpleNamespace(workspace_root=tmp_path))
    return acts


def test_grant_requires_0600_and_token(tmp_path):
    acts = _acts(tmp_path)
    assert acts._git_grant("7") == ""
    p = tmp_path / ".git-grant-7.json"
    p.write_text(json.dumps({"github_token": "tok"}))
    os.chmod(p, 0o644)
    assert acts._git_grant("7") == ""  # world-readable grant refused
    os.chmod(p, 0o600)
    assert acts._git_grant("7") == "tok"


def test_builder_prompt_forbids_push():
    from rouvr.muse import build_task_prompt
    prompt = build_task_prompt({"id": 1, "title": "t", "acceptance_criteria": []}, "r", "b", "")
    assert "do NOT push" in prompt and "supervisor grants" in prompt
