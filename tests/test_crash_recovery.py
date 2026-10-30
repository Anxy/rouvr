"""S015: a retried attempt resumes its branch instead of resetting it.

Regression test for the live drill (2026-10-30): build worker SIGKILLed
mid-turn; the Temporal retry reset the branch to base, wiping the coder's
commit, and PR creation failed with 'No commits between'."""
import asyncio
import subprocess
from pathlib import Path

from rouvr.gitops import checkout_branch


def _git(ws, *args):
    subprocess.run(["git", *args], cwd=ws, check=True, capture_output=True)


def test_checkout_branch_resumes_existing_local_branch(tmp_path):
    ws = tmp_path / "repo"
    ws.mkdir()
    _git(ws, "init", "-b", "main")
    _git(ws, "config", "user.email", "t@t")
    _git(ws, "config", "user.name", "t")
    (ws / "f.txt").write_text("base")
    _git(ws, "add", "."); _git(ws, "commit", "-m", "base")
    _git(ws, "update-ref", "refs/remotes/origin/main", "main")
    _git(ws, "checkout", "-b", "rouvr/9/a1")
    (ws / "f.txt").write_text("work")
    _git(ws, "commit", "-am", "coder work")
    _git(ws, "checkout", "main")
    asyncio.run(checkout_branch(ws, "rouvr/9/a1", "main"))
    assert (ws / "f.txt").read_text() == "work"  # commit survived the retry


def test_checkout_branch_creates_fresh_from_base(tmp_path):
    ws = tmp_path / "repo"
    ws.mkdir()
    _git(ws, "init", "-b", "main")
    _git(ws, "config", "user.email", "t@t")
    _git(ws, "config", "user.name", "t")
    (ws / "f.txt").write_text("base")
    _git(ws, "add", "."); _git(ws, "commit", "-m", "base")
    _git(ws, "update-ref", "refs/remotes/origin/main", "main")
    asyncio.run(checkout_branch(ws, "rouvr/9/a1", "main"))
    assert (ws / "f.txt").read_text() == "base"
