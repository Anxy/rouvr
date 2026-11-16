"""rouvr/gitops.py — workspace checkout + PR lookup/creation via `gh` CLI (idempotent by branch)."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from rouvr.errors import Code, RouvrError


async def _run(*argv: str, cwd: Path | None = None, env: dict[str, str] | None = None) -> str:
    # env augments the minimal clone_env, never the whole worker environ: a
    # bare {"GH_TOKEN": ...} keeps PATH/HOME so git/gh resolve (live-model
    # finding: FileNotFoundError 'gh' broke PRs) without leaking worker
    # secrets (ledger/model/JWT tokens) into children (audit S1).
    merged = {**clone_env(), **(env or {})}
    p = await asyncio.create_subprocess_exec(*argv, cwd=str(cwd) if cwd else None, env=merged,
                                             stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, err = await p.communicate()
    if p.returncode != 0:
        raise RouvrError(Code.DOWNSTREAM, f"{argv[0]} {argv[1]} failed: {err.decode()[-500:]}")
    return out.decode().strip()


def branch_for(task_id: str, attempt_no: int | None = None) -> str:
    """One delivery branch per task. attempt_no kept for call-site compat; unused.

    Form is ``rouvr/task-{id}`` (not ``rouvr/{id}``): legacy per-attempt branches
    were ``rouvr/{id}/a{N}``, and Git forbids creating ``refs/heads/rouvr/{id}``
    while ``refs/heads/rouvr/{id}/a{N}`` still exists (directory/file conflict).
    """
    return f"rouvr/task-{task_id}"


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


def grant_path(root: Path, task_id: str) -> Path:
    """Per-task git grant file: BESIDE the workspace tree, never inside it.

    An unsandboxed coder turn runs as the worker uid and could otherwise read
    its own grant file out of a sibling workspace directory and self-grant
    push/PR (audit S2). `root` is the workspace root; grants live next to it.
    """
    return root.parent / "grants" / f".git-grant-{task_id}.json"


def default_grant_path(root: Path, tenant: str, project: str) -> Path:
    """Project-default grant from onboarding (fallback when no per-task file)."""
    safe_t = str(tenant).replace("/", "_")
    safe_p = str(project).replace("/", "_")
    return root.parent / "grants" / f".git-grant-default-{safe_t}-{safe_p}.json"


def read_git_grant(
    root: Path,
    task_id: str,
    *,
    tenant: str | None = None,
    project: str | None = None,
) -> str:
    """Return github_token from task grant, else project default. Empty if none.

    Files must be mode 0600 (no group/other bits); otherwise refuse the secret.
    """
    candidates = [grant_path(root, task_id)]
    if tenant and project:
        candidates.append(default_grant_path(root, tenant, project))
    for p in candidates:
        try:
            if p.stat().st_mode & 0o077:
                continue
            token = str(json.loads(p.read_text()).get("github_token") or "")
            if token:
                return token
        except (OSError, ValueError):
            continue
    return ""


def revoke_git_grant(root: Path, task_id: str) -> bool:
    """Delete the per-task git grant file. Best-effort: False when absent."""
    try:
        grant_path(root, task_id).unlink()
        return True
    except OSError:
        return False


_GH_HELPER = ("-c", "credential.helper=!gh auth git-credential")


def _clone_url(repo: str, token: str = "") -> str:
    """HTTPS clone URL. Token embeds only when supplied (never logged)."""
    if token:
        return f"https://x-access-token:{token}@github.com/{repo}.git"
    return f"https://github.com/{repo}.git"


def _git_auth_args(token: str = "") -> tuple[str, ...]:
    """Prefer gh credential helper when GH_TOKEN is set; else bare git."""
    if token or gh_token():
        return _GH_HELPER
    return ()


async def _remote_has_branch(ws: Path, base: str, env: dict[str, str]) -> bool:
    out = await _run("git", "ls-remote", "--heads", "origin", base, cwd=ws, env=env)
    return bool(out.strip())


class EmptyRepoNeedsGrant(RouvrError):
    """Remote has no base branch yet — caller should await a git grant, then retry."""

    def __init__(self, repo: str, base: str) -> None:
        super().__init__(
            Code.DOWNSTREAM,
            f"empty_repo_needs_grant:{repo}:{base}",
            details={"repo": repo, "base": base, "empty": True},
        )


async def _bootstrap_empty_base(ws: Path, repo: str, base: str, token: str,
                                env: dict[str, str]) -> None:
    """Create origin/{base} on an empty GitHub repo (needs push credential)."""
    if not token:
        raise EmptyRepoNeedsGrant(repo, base)
    # Empty clone leaves detached/unborn HEAD — orphan base and push it.
    await _run("git", "checkout", "--orphan", base, cwd=ws, env=env)
    readme = ws / "README.md"
    if not readme.exists():
        readme.write_text(f"# {repo.split('/')[-1]}\n\nBootstrapped by Rouvr.\n")
    await _run("git", "add", "README.md", cwd=ws, env=env)
    await _run(
        "git", "-c", "user.email=rouvr@local", "-c", "user.name=Rouvr",
        "commit", "-m", "chore: initial commit (Rouvr bootstrap)",
        cwd=ws, env=env,
    )
    push_env = {**env, "GH_TOKEN": token}
    auth = _git_auth_args(token)
    try:
        await _run("git", *auth, "push", "-u", "origin", base, cwd=ws, env=push_env)
    except RouvrError:
        # gh may be absent — fall back to token URL remote.
        await _run("git", "remote", "set-url", "origin", _clone_url(repo, token),
                   cwd=ws, env=env)
        await _run("git", "push", "-u", "origin", base, cwd=ws, env=push_env)
        await _run("git", "remote", "set-url", "origin", _clone_url(repo),
                   cwd=ws, env=env)


async def prepare_workspace(root: Path, repo: str, base: str, branch: str,
                            *, token: str = "") -> Path:
    """Clone origin/{base} and create the task branch. Empty remotes are
    bootstrapped when ``token`` (git grant) can push an initial commit."""
    ws = workspace_path(root, branch)
    env = clone_env()
    if token:
        env["GH_TOKEN"] = token
    auth = _git_auth_args(token)
    url = _clone_url(repo, token)
    if not (ws / ".git").exists():
        ws.parent.mkdir(parents=True, exist_ok=True)
        try:
            await _run("git", *auth, "clone", "--branch", base, url, str(ws), env=env)
        except RouvrError as e:
            err = str(e)
            # Empty repo or wrong base: clone without --branch, then ensure base.
            if "not found in upstream" in err or "Remote branch" in err or "not found" in err:
                # Failed --branch clone can leave an empty dest; clear it.
                if ws.exists():
                    import shutil
                    shutil.rmtree(ws, ignore_errors=True)
                await _run("git", *auth, "clone", url, str(ws), env=env)
            else:
                raise
    # Never leave a token URL in remote config after clone.
    try:
        await _run("git", "remote", "set-url", "origin", _clone_url(repo), cwd=ws, env=env)
    except RouvrError:
        pass
    await _run("git", *auth, "fetch", "origin", cwd=ws, env=env)
    if not await _remote_has_branch(ws, base, env):
        await _bootstrap_empty_base(ws, repo, base, token, env)
        await _run("git", *auth, "fetch", "origin", cwd=ws, env=env)
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
