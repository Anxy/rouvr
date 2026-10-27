"""Real repository tool layer used by the objective_cycle source-edit executor.

Every tool operation is confined to `repository_root` (never the AIX bookkeeping
`.tmp/aix-cycle-workspaces` tree) via the same resolve-then-`relative_to()` boundary
pattern used by `workspace_isolation.py`. Read/search/status/write/patch leaf logic is
reused directly from the separate, already-tested `devexecutor_*` execution lane
(`backend/services/devexecutor_readonly_tools_service.py`,
`devexecutor_mutation_tools_service.py`, `devexecutor_patch_engine_service.py`,
`devexecutor_command_runner_service.py`) rather than reimplemented — those are
dependency-light, `workspace_root`-scoped pure functions with no coupling to that other
lane's plan/run/proposal/runlog machinery. `create_file`/`delete_file`/`get_git_diff` and
the sandbox-gated `run_command`/`run_tests` wrapper are new here.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from backend.aix_core.runtime.governance.sandbox import CommandNetworkResourceSandbox
from backend.services import devexecutor_command_runner_service as _cmd
from backend.services import devexecutor_mutation_tools_service as _mutate
from backend.services import devexecutor_patch_engine_service as _patch
from backend.services import devexecutor_readonly_tools_service as _read

OnEvent = Callable[[str, dict[str, Any]], None]


class RepositoryToolError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(code if not detail else f"{code}:{detail}")


class ApprovalRequiredError(RepositoryToolError):
    """Raised by `delete_file` unless the caller explicitly passes `approved=True`."""

    def __init__(self, relative_path: str) -> None:
        super().__init__("DELETE_REQUIRES_APPROVAL", relative_path)
        self.relative_path = relative_path


def _noop_on_event(event_type: str, payload_refs: dict[str, Any]) -> None:
    return None


def _skip_semantic_validation(*, root: Path, prospective_files: Any) -> tuple[None, str]:
    """`apply_patch_with_outcome`'s default semantic validator shells out to a pyright
    LSP subprocess and raises `FileNotFoundError` (uncaught) when `node`/`pyright`
    aren't on `PATH` — an environment dependency this toolbox does not require.
    Returning `(None, "")` skips both the pass and fail branches in the caller (only
    `True`/`False` are acted on), leaving our own `completion_gates.py` verification
    step as the actual correctness check for applied patches."""
    return None, ""


def _skip_pyright_runner(
    *, root: Path, changed_files: Any, validation_scope: Any, additional_targets: Any
) -> tuple[bool, str]:
    """Same reasoning as `_skip_semantic_validation`, for the separate *post-apply*
    pyright pass (`apply_patch_with_outcome`'s `pyright_runner` hook) — unlike the
    pre-apply hook, a `False` here would revert an already-applied patch, so this must
    return `True` (accepted) rather than `None`."""
    return True, "pyright post-apply validation skipped: not required by objective_cycle toolbox"


def _confine(root: Path, relative_path: str) -> Path:
    """Resolve `relative_path` against `root` and reject any escape (traversal or
    symlink) — same posture as `workspace_isolation.py`: fail closed, never
    auto-corrected."""
    base = root.resolve()
    candidate = (base / relative_path).resolve()
    try:
        candidate.relative_to(base)
    except ValueError as exc:
        raise RepositoryToolError("PATH_ESCAPES_WORKSPACE_ROOT", relative_path) from exc
    return candidate


def _parse_diff_stats(diff_text: str) -> dict[str, dict[str, int]]:
    """Per-file added/deleted line counts parsed from unified `git diff` output."""
    stats: dict[str, dict[str, int]] = {}
    current: str | None = None
    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            parts = line.split(" ")
            if len(parts) >= 4:
                candidate = parts[3]
                current = candidate[2:] if candidate.startswith("b/") else candidate
                stats.setdefault(current, {"additions": 0, "deletions": 0})
            continue
        if current is None:
            continue
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            stats[current]["additions"] += 1
        elif line.startswith("-"):
            stats[current]["deletions"] += 1
    return stats


@dataclass
class RepositoryToolbox:
    """The 10 required tools, all path-confined to `repository_root`, all emitting
    real events through `on_event(event_type, payload_refs)` on every real call —
    never a hardcoded status line."""

    repository_root: Path
    on_event: OnEvent = field(default=_noop_on_event)

    def __post_init__(self) -> None:
        self.repository_root = Path(self.repository_root).resolve()
        if not self.repository_root.is_dir():
            raise RepositoryToolError("REPOSITORY_ROOT_MISSING", str(self.repository_root))

    def _emit(self, event_type: str, payload_refs: dict[str, Any]) -> None:
        self.on_event(event_type, {k: str(v) for k, v in payload_refs.items()})

    # -- read-only tools -----------------------------------------------------

    def list_directory(self, relative_path: str = ".", *, include_hidden: bool = False) -> dict[str, Any]:
        _confine(self.repository_root, relative_path)
        result = _read.list_dir(
            workspace_root=self.repository_root, relative_path=relative_path, include_hidden=include_hidden
        )
        self._emit(
            "tool.list_directory",
            {"relative_path": relative_path, "entry_count": len(result["entries"])},
        )
        return result

    def search_files(self, query: str, relative_path: str = ".") -> dict[str, Any]:
        _confine(self.repository_root, relative_path)
        result = _read.search(workspace_root=self.repository_root, pattern=query, relative_path=relative_path)
        # rg_json returns absolute paths; callers must see repo-relative keys so
        # discovery/codegen never double-edit the same file under two path spellings.
        root = self.repository_root.resolve()
        normalized_hits: list[dict[str, Any]] = []
        for hit in list(result.get("hits") or []):
            raw = str(hit.get("path") or hit.get("relative_path") or "").strip()
            if not raw:
                continue
            try:
                abs_path = Path(raw)
                if not abs_path.is_absolute():
                    abs_path = (root / raw).resolve()
                else:
                    abs_path = abs_path.resolve()
                rel = abs_path.relative_to(root).as_posix()
            except (OSError, ValueError):
                rel = raw.replace("\\", "/")
            normalized_hits.append({**hit, "path": rel, "relative_path": rel})
        result = {**result, "hits": normalized_hits}
        self._emit("tool.search", {"pattern": query, "hit_count": len(result["hits"])})
        return result

    def read_file(self, relative_path: str, *, max_bytes: int = 65536) -> dict[str, Any]:
        _confine(self.repository_root, relative_path)
        result = _read.read_file(
            workspace_root=self.repository_root,
            relative_path=relative_path,
            source="filesystem",
            max_bytes=max_bytes,
        )
        self._emit("tool.file_read", {"relative_path": relative_path, "size_bytes": result["size_bytes"]})
        return result

    def read_range(
        self,
        relative_path: str,
        *,
        start_line: int | None = None,
        end_line: int | None = None,
        symbol: str | None = None,
        max_chars: int = 20000,
    ) -> dict[str, Any]:
        """Read a bounded line/symbol region — avoid loading entire large files."""
        _confine(self.repository_root, relative_path)
        result = _read.read_range(
            workspace_root=self.repository_root,
            relative_path=relative_path,
            start_line=start_line,
            end_line=end_line,
            symbol=symbol,
            max_chars=max_chars,
        )
        self._emit(
            "tool.file_read_range",
            {
                "relative_path": relative_path,
                "start_line": result.get("start_line"),
                "end_line": result.get("end_line"),
                "line_count": result.get("line_count"),
            },
        )
        return result

    def get_git_status(self) -> dict[str, Any]:
        result = _read.get_status(workspace_root=self.repository_root)
        self._emit(
            "tool.git_status",
            {"is_git_repo": result["is_git_repo"], "change_count": len(result.get("changes") or [])},
        )
        return result

    # -- mutation tools --------------------------------------------------------

    def create_file(self, relative_path: str, content: str) -> dict[str, Any]:
        target = _confine(self.repository_root, relative_path)
        if target.exists():
            raise RepositoryToolError("FILE_ALREADY_EXISTS", relative_path)
        result = _mutate.write_file(
            workspace_root=self.repository_root, relative_path=relative_path, content=content
        )
        self._emit("tool.file_written", {"relative_path": relative_path, "bytes_after": result.get("bytes_after")})
        return result

    def apply_patch(self, patch_text: str) -> dict[str, Any]:
        if not patch_text or not patch_text.strip():
            raise RepositoryToolError("EMPTY_PATCH")
        outcome = _patch.apply_patch_with_outcome(
            workspace_root=self.repository_root,
            patch_text=patch_text,
            semantic_validator=_skip_semantic_validation,
            pyright_runner=_skip_pyright_runner,
        )
        payload = outcome.model_dump(mode="python") if hasattr(outcome, "model_dump") else dict(outcome)
        if payload.get("status") != "applied":
            raise RepositoryToolError(
                "PATCH_APPLY_FAILED",
                str(payload.get("reason_code") or payload.get("stderr") or "unknown"),
            )
        self._emit(
            "tool.patch_applied",
            {
                "changed_files": ",".join(payload.get("changed_files") or []),
                "reason_code": payload.get("reason_code"),
            },
        )
        return payload

    def delete_file(self, relative_path: str, *, approved: bool = False) -> dict[str, Any]:
        target = _confine(self.repository_root, relative_path)
        if not approved:
            raise ApprovalRequiredError(relative_path)
        if not target.is_file():
            raise RepositoryToolError("FILE_NOT_FOUND", relative_path)
        target.unlink()
        self._emit("tool.file_deleted", {"relative_path": relative_path, "approved": True})
        return {"relative_path": relative_path, "deleted": True}

    # -- command tools -----------------------------------------------------------

    def run_command(
        self, command: list[str], *, allowlist: tuple[str, ...], timeout_seconds: float = 120.0
    ) -> dict[str, Any]:
        joined = " ".join(command)
        decision = CommandNetworkResourceSandbox().evaluate_command(command=joined, command_allowlist=allowlist)
        if decision.disposition != "ALLOW":
            raise RepositoryToolError("COMMAND_DENIED", ",".join(decision.reason_codes))
        allow_prefixes = [pattern.split(" ") for pattern in allowlist]
        result = _cmd.run_command(
            workspace_root=self.repository_root,
            command=command,
            allow_prefixes=allow_prefixes,
            timeout_seconds=timeout_seconds,
        )
        self._emit("tool.command_run", {"command": joined, "returncode": result.get("returncode")})
        return result

    def run_tests(self, test_command: list[str], *, timeout_seconds: float = 300.0) -> dict[str, Any]:
        """`run_command` with the test command itself as its own single-entry
        allowlist — a caller asking to run tests only ever gets to run that exact
        command, nothing else."""
        from backend.aix_core.runtime.executor.python_argv import resolve_python_argv

        resolved = resolve_python_argv(list(test_command))
        allowlist = (" ".join(resolved),)
        return self.run_command(resolved, allowlist=allowlist, timeout_seconds=timeout_seconds)

    # -- git diff ---------------------------------------------------------------

    def get_git_diff(self, paths: list[str] | None = None) -> dict[str, Any]:
        git_meta = Path(self.repository_root) / ".git"
        if not git_meta.exists():
            self._emit(
                "tool.git_diff",
                {"changed_file_count": 0, "returncode": 129, "skipped": "not_a_git_repository"},
            )
            return {"diff": "", "stats": {}, "returncode": 129}
        cmd = ["git", "-C", str(self.repository_root), "diff", "--no-color"]
        if paths:
            cmd.append("--")
            cmd.extend(paths)
        proc = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30
        )
        # 129 = not a repo / usage error; applied-patch evidence still proves the mutation.
        if proc.returncode not in (0, 1):
            self._emit(
                "tool.git_diff",
                {"changed_file_count": 0, "returncode": proc.returncode, "stderr": (proc.stderr or "")[:200]},
            )
            return {"diff": "", "stats": {}, "returncode": proc.returncode}
        diff_text = proc.stdout
        stats = _parse_diff_stats(diff_text)
        self._emit(
            "tool.git_diff", {"changed_file_count": len(stats), "returncode": proc.returncode}
        )
        return {"diff": diff_text, "stats": stats, "returncode": proc.returncode}


__all__ = [
    "RepositoryToolError",
    "ApprovalRequiredError",
    "RepositoryToolbox",
]
