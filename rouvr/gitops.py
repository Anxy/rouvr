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


def gh_token() -> str:
    """Scoped clone/fetch credential (0600 file written by scripts/rouvr).

    Empty when absent: public repos still clone, private ones fail closed —
    never the host's stored git credentials, never exported stack-wide."""
    import os
    p = os.environ.get("ROUVR_GH_TOKEN_FILE", "")
    if p:
        try:
            if Path(p).stat().st_mode & 0o077:
                return ""
            return Path(p).read_text().strip()
        except OSError:
            return ""
    return os.environ.get("GH_TOKEN", "")


def clone_env() -> dict[str, str]:
    """Minimal environment for clone/fetch: PATH plus the scoped token only.

    Nothing else from the worker process leaks into git/gh (no ledger token,
    no model keys, no host credentials), and git never prompts."""
    import os
    env = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", ""),
           "GIT_TERMINAL_PROMPT": "0", "GH_TOKEN": gh_token()}
    for proxy in ("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy"):
        if os.environ.get(proxy):
            env[proxy] = os.environ[proxy]
    return env


def revoke_git_grant(root: Path, task_id: str) -> bool:
    """Delete the per-task git grant file. Best-effort: False when absent."""
    try:
        (root / f".git-grant-{task_id}.json").unlink()
        return True
    except OSError:
        return False


_GH_HELPER = ("-c", "credential.helper=!gh auth git-credential")


async def prepare_workspace(root: Path, repo: str, base: str, branch: str) -> Path:
    ws = workspace_path(root, branch)
    env = clone_env()
    if not (ws / ".git").exists():
        ws.parent.mkdir(parents=True, exist_ok=True)
        await _run("git", *_GH_HELPER, "clone", "--branch", base,
                   f"https://github.com/{repo}.git", str(ws), env=env)
    await _run("git", *_GH_HELPER, "fetch", "origin", cwd=ws, env=env)
    await checkout_branch(ws, branch, base)
    return ws


async def checkout_branch(ws: Path, branch: str, base: str) -> None:
    """Existing local branch (a retried attempt after worker death) is
    resumed as-is; only a fresh attempt starts clean from origin/base.
    Resetting unconditionally wiped the prior attempt's commit and the
    retry then failed PR creation with 'No commits between'."""
    p = await asyncio.create_subprocess_exec("git", "rev-parse", "--verify", "--quiet", branch,
                                             cwd=str(ws), stdout=asyncio.subprocess.DEVNULL,
                                             stderr=asyncio.subprocess.DEVNULL)
    if await p.wait() == 0:
        await _run("git", "checkout", branch, cwd=ws)
    else:
        await _run("git", "checkout", "-B", branch, f"origin/{base}", cwd=ws)


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


async def close_superseded_pr(repo: str, prev_branch: str, new_pr_url: str,
                              env: dict[str, str]) -> str:
    """Close the previous attempt's open PR, superseded by the new one.
    Returns the closed PR url or '' when none was open."""
    existing = json.loads(await _run("gh", "pr", "list", "-R", repo, "--head", prev_branch,
                                     "--state", "open", "--json", "url", env=env) or "[]")
    if not existing:
        return ""
    url = existing[0]["url"]
    await _run("gh", "pr", "close", url, "-R", repo,
               "--comment", f"Superseded by {new_pr_url} (new attempt).", env=env)
    return url


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
