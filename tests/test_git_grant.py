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
    from rouvr.gitops import grant_path
    acts = _acts(tmp_path)
    assert acts._git_grant("7a") == ""
    p = grant_path(tmp_path, "7a")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"github_token": "tok"}))
    os.chmod(p, 0o644)
    assert acts._git_grant("7a") == ""  # world-readable grant refused
    os.chmod(p, 0o600)
    assert acts._git_grant("7a") == "tok"


def test_grant_lives_outside_workspace_tree(tmp_path):
    """Audit S2: a decoy grant inside the workspace tree must be ignored —
    an unsandboxed turn must not self-grant from a sibling directory."""
    from rouvr.gitops import grant_path
    acts = _acts(tmp_path)
    (tmp_path / ".git-grant-7b.json").write_text(json.dumps({"github_token": "decoy"}))
    assert acts._git_grant("7b") == ""
    assert grant_path(tmp_path, "7").parent != tmp_path
    assert grant_path(tmp_path, "7").parent.name == "grants"


def test_coder_prompt_forbids_push():
    from rouvr.muse import build_task_prompt
    prompt = build_task_prompt({"id": 1, "title": "t", "acceptance_criteria": []}, "r", "b", "")
    assert "do NOT push" in prompt and "supervisor grants" in prompt


async def test_run_merges_env_over_os_environ():
    """Live-model regression: _run(env={'GH_TOKEN': ...}) must keep PATH so
    git/gh resolve instead of FileNotFoundError."""
    import sys
    from rouvr.gitops import _run
    out = await _run(sys.executable, "-c",
                     "import os;print(os.environ.get('ROUVR_PROBE_X'), os.environ.get('PATH', '') != '')",
                     env={"ROUVR_PROBE_X": "1"})
    assert out.split() == ["1", "True"]


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
    monkeypatch.setenv("META_API_KEY", "meta-key")
    monkeypatch.setenv("WORKHUB_JWT", "jwt")
    env = clone_env()
    assert env["GH_TOKEN"] == "filetok"  # scoped file wins, host env never leaks
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    for leaked in ("ROUVR_LEDGER_TOKEN", "META_API_KEY", "WORKHUB_JWT"):
        assert leaked not in env


def test_revoke_git_grant(tmp_path):
    from rouvr.gitops import grant_path, revoke_git_grant
    p = grant_path(tmp_path, "7c")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{}")
    assert revoke_git_grant(tmp_path, "7c") is True
    assert not p.exists()
    assert revoke_git_grant(tmp_path, "7c") is False


async def test_run_hides_worker_secrets_from_children(tmp_path, monkeypatch):
    """Audit S1/T1 negative: worker secrets must not reach _run children,
    while PATH/HOME and caller vars do."""
    import sys
    from rouvr.gitops import _run
    monkeypatch.setenv("ROUVR_LEDGER_TOKEN", "ledger-canary")
    monkeypatch.setenv("META_API_KEY", "model-canary")
    out = await _run(sys.executable, "-c",
                     "import os;print(os.environ.get('ROUVR_PROBE_Y'),"
                     "os.environ.get('PATH', '') != '',"
                     "os.environ.get('ROUVR_LEDGER_TOKEN'),"
                     "os.environ.get('META_API_KEY'))",
                     env={"ROUVR_PROBE_Y": "1"})
    assert out.split() == ["1", "True", "None", "None"]
