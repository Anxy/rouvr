from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


class ProposalWorkspaceError(RuntimeError):
    pass


@dataclass
class ProposalWorkspaceContext:
    proposal_id: str
    project_id: str
    tenant_id: str | None
    job_id: str
    task_number: int | None
    repo_root: Path
    source_root: Path
    checkout_root: Path
    workspace_root: Path
    artifact_root: Path
    branch_name: str
    base_branch: str | None
    base_commit: str
    created_at_utc: str
    retained: bool = False
    retention_reason: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "proposal_id": self.proposal_id,
            "project_id": self.project_id,
            "tenant_id": self.tenant_id,
            "job_id": self.job_id,
            "task_number": self.task_number,
            "repo_root": str(self.repo_root),
            "source_root": str(self.source_root),
            "checkout_root": str(self.checkout_root),
            "workspace_root": str(self.workspace_root),
            "artifact_root": str(self.artifact_root),
            "branch_name": self.branch_name,
            "base_branch": self.base_branch,
            "base_commit": self.base_commit,
            "created_at_utc": self.created_at_utc,
            "retained": self.retained,
            "retention_reason": self.retention_reason,
        }


class ProposalWorkspaceManager:
    def __init__(
        self,
        base_root: str | Path,
        *,
        git_bin: str = "git",
        branch_prefix: str = "aix/proposals",
        min_free_bytes: int = 0,
    ) -> None:
        self.base_root = Path(base_root).resolve()
        self.base_root.mkdir(parents=True, exist_ok=True)
        self.git_bin = str(git_bin or "git").strip() or "git"
        self.branch_prefix = str(branch_prefix or "aix/proposals").strip().strip("/") or "aix/proposals"
        self.min_free_bytes = max(0, int(min_free_bytes or 0))

    def create_workspace(
        self,
        *,
        project_id: str,
        job_id: str,
        source_root: str | Path,
        tenant_id: str | None = None,
        task_number: int | None = None,
        branch_name: str | None = None,
        base_ref: str = "HEAD",
    ) -> ProposalWorkspaceContext:
        source = Path(source_root).resolve()
        repo_root = Path(self._git(source, "rev-parse", "--show-toplevel")).resolve()
        try:
            relative_workspace = source.relative_to(repo_root)
        except ValueError:
            relative_workspace = Path(".")
        base_commit = self._git(repo_root, "rev-parse", base_ref)
        base_branch = self._git(repo_root, "branch", "--show-current", allow_empty=True) or None
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
        project_segment = _safe_path_segment(project_id, label="project_id")
        tenant_segment = _safe_path_segment(tenant_id, label="tenant_id") if str(tenant_id or "").strip() else None
        proposal_id = f"{project_segment}-task-{task_number or 'adhoc'}-{stamp}"
        branch = branch_name or self._build_branch_name(task_number=task_number, stamp=stamp)
        checkout_root = (self.base_root / tenant_segment / proposal_id) if tenant_segment else (self.base_root / proposal_id)
        artifact_root = (
            (self.base_root / "_artifacts" / tenant_segment / proposal_id)
            if tenant_segment
            else (self.base_root / "_artifacts" / proposal_id)
        )
        self._assert_free_space_available(root=self.base_root)
        if checkout_root.exists():
            raise ProposalWorkspaceError(f"proposal workspace already exists: {checkout_root}")
        if artifact_root.exists():
            raise ProposalWorkspaceError(f"proposal artifact root already exists: {artifact_root}")

        try:
            self._run_git(repo_root, "worktree", "add", "--detach", str(checkout_root), base_commit)
            self._run_git(checkout_root, "checkout", "-b", branch)
        except Exception:
            self._remove_git_worktree(repo_root=repo_root, checkout_root=checkout_root)
            raise
        workspace_root = (checkout_root / relative_workspace).resolve()
        if not workspace_root.exists():
            self._remove_git_worktree(repo_root=repo_root, checkout_root=checkout_root)
            raise ProposalWorkspaceError(f"proposal workspace subpath does not exist after clone: {workspace_root}")
        artifact_root.mkdir(parents=True, exist_ok=False)

        context = ProposalWorkspaceContext(
            proposal_id=proposal_id,
            project_id=project_id,
            tenant_id=tenant_id,
            job_id=job_id,
            task_number=task_number,
            repo_root=repo_root,
            source_root=source,
            checkout_root=checkout_root,
            workspace_root=workspace_root,
            artifact_root=artifact_root,
            branch_name=branch,
            base_branch=base_branch,
            base_commit=base_commit,
            created_at_utc=datetime.now(UTC).isoformat(),
        )
        self._exclude_runtime_metadata(checkout_root)
        self._write_metadata(context)
        return context

    def capture_review_artifacts(self, *, context: ProposalWorkspaceContext) -> dict[str, object]:
        capture_root = context.checkout_root if context.checkout_root.exists() else context.workspace_root
        status_lines = [
            line.rstrip()
            for line in self._git(capture_root, "status", "--short", allow_empty=True).splitlines()
            if line.strip()
        ]
        changed_candidates: list[str] = []
        seen_candidates: set[str] = set()
        for line in status_lines:
            if line.startswith("?? "):
                continue
            path = _status_path(line)
            if path and path not in seen_candidates:
                seen_candidates.add(path)
                changed_candidates.append(path)
        untracked_files = [
            line.strip().replace("\\", "/")
            for line in self._git(
                capture_root,
                "ls-files",
                "--others",
                "--exclude-standard",
                allow_empty=True,
            ).splitlines()
            if line.strip()
        ]
        for path in untracked_files:
            if path not in seen_candidates:
                seen_candidates.add(path)
                changed_candidates.append(path)
        changed_files = self._canonicalize_changed_files(
            context=context,
            changed_files=changed_candidates,
        )
        diff_parts = [
            self._git(capture_root, "diff", "--binary", allow_empty=True),
            self._git(capture_root, "diff", "--cached", "--binary", allow_empty=True),
        ]
        for path in untracked_files:
            untracked_diff = self._git_diff_untracked_file(capture_root=capture_root, relative_path=path)
            if untracked_diff.strip():
                diff_parts.append(untracked_diff)
        diff_text = "\n".join(part.rstrip("\r\n") for part in diff_parts if part.strip())
        diff_path = context.artifact_root / "diff.patch"
        diff_path.write_text(diff_text, encoding="utf-8")
        changed_files_path = context.artifact_root / "changed_files.json"
        changed_files_path.write_text(json.dumps(changed_files, indent=2), encoding="utf-8")
        status_path = context.artifact_root / "git-status.txt"
        status_path.write_text("\n".join(status_lines) + ("\n" if status_lines else ""), encoding="utf-8")

        payload: dict[str, object] = {
            "proposal_id": context.proposal_id,
            "branch_name": context.branch_name,
            "base_branch": context.base_branch,
            "base_commit": context.base_commit,
            "workspace_root": str(capture_root),
            "source_workspace_root": str(context.workspace_root),
            "artifact_capture_root": str(capture_root),
            "artifact_root": str(context.artifact_root),
            "changed_files": changed_files,
            "changed_files_ref": str(changed_files_path),
            "diff_ref": str(diff_path),
            "status_ref": str(status_path),
            "status_lines": status_lines,
            "has_changes": bool(changed_files),
        }
        out = context.artifact_root / ".devexecutor_proposal_artifacts.json"
        out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return payload

    def _canonicalize_changed_files(
        self,
        *,
        context: ProposalWorkspaceContext,
        changed_files: list[str],
    ) -> list[str]:
        try:
            workspace_prefix = context.workspace_root.relative_to(context.checkout_root).as_posix().strip("/")
        except ValueError:
            workspace_prefix = ""
        normalized: list[str] = []
        seen: set[str] = set()
        prefix = f"{workspace_prefix}/" if workspace_prefix else ""
        for raw_path in changed_files:
            candidate = str(raw_path or "").strip().replace("\\", "/")
            if not candidate:
                continue
            if prefix and candidate.startswith(prefix):
                candidate = candidate[len(prefix) :]
            if candidate and candidate not in seen:
                seen.add(candidate)
                normalized.append(candidate)
        return normalized

    def _git_diff_untracked_file(self, *, capture_root: Path, relative_path: str) -> str:
        proc = subprocess.run(
            [self.git_bin, "diff", "--no-index", "--binary", "--", os.devnull, relative_path],
            cwd=str(capture_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if proc.returncode not in {0, 1}:
            detail = (proc.stderr or proc.stdout or f"git diff --no-index exited {proc.returncode}").strip()
            raise ProposalWorkspaceError(f"git diff --no-index failed: {detail}")
        return proc.stdout or ""

    def teardown_workspace(
        self,
        *,
        context: ProposalWorkspaceContext,
        retain: bool = False,
        retention_reason: str | None = None,
        drop_branch: bool = False,
    ) -> ProposalWorkspaceContext:
        if retain:
            context.retained = True
            context.retention_reason = retention_reason or "retained_for_review"
            self._write_metadata(context)
            return context
        self._remove_git_worktree(repo_root=context.repo_root, checkout_root=context.checkout_root)
        self._safe_remove_workspace(workspace_root=context.artifact_root)
        if drop_branch:
            self._drop_branch(repo_root=context.repo_root, branch_name=context.branch_name)
        return context

    def _safe_remove_workspace(self, *, workspace_root: Path) -> None:
        if workspace_root.exists():
            shutil.rmtree(workspace_root, ignore_errors=True)

    def _remove_git_worktree(self, *, repo_root: Path, checkout_root: Path) -> None:
        if checkout_root.exists():
            try:
                self._run_git(repo_root, "worktree", "remove", "--force", str(checkout_root), allow_empty=True)
            except ProposalWorkspaceError:
                self._safe_remove_workspace(workspace_root=checkout_root)

    def _drop_branch(self, *, repo_root: Path, branch_name: str) -> None:
        branch = str(branch_name or "").strip()
        if not branch:
            return
        self._run_git(repo_root, "branch", "-D", branch, allow_empty=True)

    def _assert_free_space_available(self, *, root: Path) -> None:
        if self.min_free_bytes <= 0:
            return
        usage = shutil.disk_usage(root)
        if int(usage.free) >= self.min_free_bytes:
            return
        raise ProposalWorkspaceError(
            "proposal workspace free space below threshold: "
            f"root={root} free_bytes={int(usage.free)} required_free_bytes={self.min_free_bytes}"
        )

    def _build_branch_name(self, *, task_number: int | None, stamp: str) -> str:
        if task_number is None:
            return f"{self.branch_prefix}/{stamp}"
        return f"{self.branch_prefix}/task-{task_number}-{stamp}"

    def _git(self, cwd: Path, *args: str, allow_empty: bool = False) -> str:
        completed = self._run_git(cwd, *args, allow_empty=allow_empty)
        return completed

    def _run_git(self, cwd: Path, *args: str, allow_empty: bool = False) -> str:
        try:
            proc = subprocess.run(
                [self.git_bin, *args],
                cwd=str(cwd),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
        except FileNotFoundError as exc:
            raise ProposalWorkspaceError(f"git executable not found: {self.git_bin}") from exc
        if proc.returncode != 0:
            stderr = (proc.stderr or "").strip()
            stdout = (proc.stdout or "").strip()
            detail = stderr or stdout or f"git exited {proc.returncode}"
            raise ProposalWorkspaceError(f"git {' '.join(args)} failed: {detail}")
        output = (proc.stdout or "").strip()
        if output or allow_empty:
            return output
        return ""

    def _write_metadata(self, context: ProposalWorkspaceContext) -> None:
        payload = json.dumps(context.to_dict(), indent=2)
        meta = context.workspace_root / ".devexecutor_proposal_workspace.json"
        meta.write_text(payload, encoding="utf-8")
        if context.checkout_root != context.workspace_root:
            checkout_meta = context.checkout_root / ".devexecutor_proposal_workspace.json"
            checkout_meta.write_text(payload, encoding="utf-8")

    def _exclude_runtime_metadata(self, checkout_root: Path) -> None:
        exclude_path = self._resolve_git_path(checkout_root, "info/exclude")
        exclude_path.parent.mkdir(parents=True, exist_ok=True)
        existing = exclude_path.read_text(encoding="utf-8") if exclude_path.exists() else ""
        lines = {line.strip() for line in existing.splitlines() if line.strip()}
        updated = False
        for entry in (".devexecutor_proposal_workspace.json", ".devexecutor_proposal_artifacts.json"):
            if entry not in lines:
                existing += ("" if existing.endswith(("\n", "\r")) or not existing else "\n") + entry + "\n"
                updated = True
        if updated:
            exclude_path.write_text(existing, encoding="utf-8")

    def _resolve_git_path(self, cwd: Path, pathspec: str) -> Path:
        resolved = Path(self._git(cwd, "rev-parse", "--git-path", pathspec))
        if resolved.is_absolute():
            return resolved
        return (cwd / resolved).resolve()


def _status_path(line: str) -> str | None:
    text = str(line or "").rstrip()
    if len(text) < 3:
        return None
    if len(text) >= 3 and text[1] == " ":
        path = text[2:].strip()
    else:
        path = text[3:].strip()
    if " -> " in path:
        path = path.rsplit(" -> ", 1)[-1].strip()
    return path.replace("\\", "/") or None


def _safe_path_segment(value: str | None, *, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ProposalWorkspaceError(f"{label} is required")
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", text).strip(".-")
    if not safe:
        raise ProposalWorkspaceError(f"{label} does not contain a safe path segment")
    return safe


__all__ = ["ProposalWorkspaceContext", "ProposalWorkspaceError", "ProposalWorkspaceManager"]
