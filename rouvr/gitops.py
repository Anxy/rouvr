"""rouvr/gitops.py — workspace checkout + PR lookup/creation via `gh` CLI (idempotent by branch)."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from rouvr.errors import Code, RouvrError


async def _run(*argv: str, cwd: Path | None = None, env: dict[str, str] | None = None) -> str:
    p = await asyncio.create_subprocess_exec(*argv, cwd=str(cwd) if cwd else None, env=env,
                                             stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, err = await p.communicate()
    if p.returncode != 0:
        raise RouvrError(Code.DOWNSTREAM, f"{argv[0]} {argv[1]} failed: {err.decode()[-500:]}")
    return out.decode().strip()


def branch_for(task_id: str, attempt_no: int) -> str:
    return f"rouvr/{task_id}/a{attempt_no}"


def workspace_path(root: Path, branch: str) -> Path:
    return root / branch.replace("/", "__")


async def prepare_workspace(root: Path, repo: str, base: str, branch: str) -> Path:
    ws = workspace_path(root, branch)
    if not (ws / ".git").exists():
        ws.parent.mkdir(parents=True, exist_ok=True)
        await _run("git", "clone", "--branch", base, f"https://github.com/{repo}.git", str(ws))
    await _run("git", "fetch", "origin", cwd=ws)
    await _run("git", "checkout", "-B", branch, f"origin/{base}", cwd=ws)  # reset: attempt is a clean slate
    return ws


async def ensure_pr(ws: Path, repo: str, base: str, branch: str, title: str, env: dict[str, str]) -> tuple[str, str]:
    """Returns (pr_url, head_sha). Reuses an open PR for the branch if one exists."""
    head = await _run("git", "rev-parse", "HEAD", cwd=ws)
    existing = json.loads(await _run("gh", "pr", "list", "-R", repo, "--head", branch, "--state", "open",
                                     "--json", "url", env=env) or "[]")
    if existing:
        return existing[0]["url"], head
    url = await _run("gh", "pr", "create", "-R", repo, "--base", base, "--head", branch, "--title", title,
                     "--body", "Opened by Rouvr.", env=env)
    return url, head


async def clone_at(repo: str, sha: str, base: str, dest: Path, env: dict[str, str] | None = None) -> Path:
    """Fresh clone checked out at sha (the PR head tests run against), with base fetched for diffing."""
    await _run("git", "clone", "--no-checkout", f"https://github.com/{repo}.git", str(dest), env=env)
    await _run("git", "fetch", "origin", base, sha, cwd=dest, env=env)
    await _run("git", "checkout", sha, cwd=dest)
    return dest


def ci_status(rollup: list[dict]) -> str:
    """Map a `gh pr view --json statusCheckRollup` payload. No failing checks = success."""
    ok = {"SUCCESS", "SKIPPED", "NEUTRAL"}
    return "success" if all((c.get("conclusion") or "") in ok for c in rollup) else "failure"


async def pr_facts(pr_url: str, env: dict[str, str]) -> tuple[str, str]:
    """(head_branch, ci_status) observed from GitHub. Empty rollup (no CI) reads as success."""
    data = json.loads(await _run("gh", "pr", "view", pr_url, "--json", "headRefName,statusCheckRollup", env=env))
    return str(data.get("headRefName", "")), ci_status(data.get("statusCheckRollup") or [])


async def diff_range(ws: Path, base: str, head: str) -> str:
    """Unified diff of base...head for the cheat-scan. Empty string when identical."""
    p = await asyncio.create_subprocess_exec("git", "-C", str(ws), "diff", f"{base}...{head}",
                                             stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    out, _ = await p.communicate()
    return out.decode(errors="replace")
