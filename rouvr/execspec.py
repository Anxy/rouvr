"""rouvr/execspec.py — execution spec provider: which repo/branch/commands a task runs with."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import yaml

from rouvr.errors import Code, RouvrError


@dataclass(frozen=True)
class ExecSpec:
    repo: str                     # "org/name"
    base_branch: str
    test_command: list[str]
    allowed_paths: list[str] = field(default_factory=list)
    timeout_s: int = 1500
    digest: str = ""              # sha256 of the canonical spec; recorded as execution_spec evidence


class ExecSpecProvider(Protocol):
    async def get(self, tenant: str, task: dict) -> ExecSpec: ...


class LocalExecSpecProvider:
    """Reads specs/<project>.yaml from a host directory. Phase 1 default (SPEC_PROVIDER=local)."""

    def __init__(self, root: Path) -> None:
        self.root = root

    async def get(self, tenant: str, task: dict) -> ExecSpec:
        project = str(task.get("project_id") or task.get("project") or "")
        for label, val in (("tenant", tenant), ("project", project)):
            if not val or "/" in val or "\\" in val or ".." in val or val.startswith("."):
                raise RouvrError(Code.CONTRACT, f"invalid {label} id: {val!r}")
        path = self.root / tenant / f"{project}.yaml"
        if not path.exists():
            raise RouvrError(Code.CONTRACT, f"no exec spec at {path}")
        raw = path.read_bytes()
        d = yaml.safe_load(raw) or {}
        if not isinstance(d, dict):
            raise RouvrError(Code.CONTRACT, "exec spec must be a mapping")
        for k in ("repo", "base_branch", "test_command"):
            if k not in d:
                raise RouvrError(Code.CONTRACT, f"exec spec missing {k}")
        for k in ("repo", "base_branch"):
            if not isinstance(d[k], str) or not d[k].strip():
                raise RouvrError(Code.CONTRACT, f"exec spec {k} must be a non-blank string")
        timeout_s = d.get("timeout_s", 1500)
        if isinstance(timeout_s, bool) or not isinstance(timeout_s, int) or timeout_s <= 0:
            raise RouvrError(Code.CONTRACT, "exec spec timeout_s must be a positive int")
        for k in ("test_command", "allowed_paths"):
            v = d.get(k) if k == "allowed_paths" else d[k]
            if v is None and k == "allowed_paths":
                continue
            if not isinstance(v, list) or not all(isinstance(x, str) and x.strip() for x in v):
                raise RouvrError(Code.CONTRACT, f"exec spec {k} must be a list of non-blank strings")
            if k == "test_command" and not v:
                raise RouvrError(Code.CONTRACT, "exec spec test_command must not be empty")
        return ExecSpec(repo=d["repo"], base_branch=d["base_branch"], test_command=list(d["test_command"]),
                        allowed_paths=list(d.get("allowed_paths") or []), timeout_s=timeout_s,
                        digest=hashlib.sha256(raw).hexdigest())
