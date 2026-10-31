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


def test_coder_prompt_forbids_push():
    from rouvr.muse import build_task_prompt
    prompt = build_task_prompt({"id": 1, "title": "t", "acceptance_criteria": []}, "r", "b", "")
    assert "do NOT push" in prompt and "supervisor grants" in prompt


def test_gh_token_file_0600(tmp_path, monkeypatch):
    from rouvr.gitops import gh_token
    p = tmp_path / "gh-token"
    p.write_text("filetok")
    os.chmod(p, 0o600)
    monkeypatch.setenv("ROUVR_GH_TOKEN_FILE", str(p))
    assert gh_token() == "filetok"
    os.chmod(p, 0o644)
    assert gh_token() == ""  # world-readable token file refused
    p.unlink()
    assert gh_token() == ""  # absent file: public repos only, fail closed


def test_clone_env_minimal(tmp_path, monkeypatch):
    from rouvr.gitops import clone_env
    p = tmp_path / "gh-token"
    p.write_text("filetok")
    os.chmod(p, 0o600)
    monkeypatch.setenv("ROUVR_GH_TOKEN_FILE", str(p))
    monkeypatch.setenv("GH_TOKEN", "host-token")
    monkeypatch.setenv("ROUVR_LEDGER_TOKEN", "ledger-token")
    monkeypatch.setenv("META_MODEL_API_KEY", "meta-key")
    monkeypatch.setenv("WORKHUB_JWT", "jwt")
    env = clone_env()
    assert env["GH_TOKEN"] == "filetok"  # scoped file wins, host env never leaks
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    for leaked in ("ROUVR_LEDGER_TOKEN", "META_MODEL_API_KEY", "WORKHUB_JWT"):
        assert leaked not in env


def test_revoke_git_grant(tmp_path):
    from rouvr.gitops import revoke_git_grant
    p = tmp_path / ".git-grant-7.json"
    p.write_text("{}")
    assert revoke_git_grant(tmp_path, "7") is True
    assert not p.exists()
    assert revoke_git_grant(tmp_path, "7") is False
