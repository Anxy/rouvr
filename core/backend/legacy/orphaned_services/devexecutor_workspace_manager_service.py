from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4


@dataclass
class WorkspaceContext:
    workspace_id: str
    project_id: str
    job_id: str
    task_number: int | None
    workspace_root: Path
    source_root: Path | None
    created_at_utc: str
    retained: bool = False
    retention_reason: str | None = None

    def to_dict(self) -> dict:
        return {
            "workspace_id": self.workspace_id,
            "project_id": self.project_id,
            "job_id": self.job_id,
            "task_number": self.task_number,
            "workspace_root": str(self.workspace_root),
            "source_root": str(self.source_root) if self.source_root else None,
            "created_at_utc": self.created_at_utc,
            "retained": self.retained,
            "retention_reason": self.retention_reason,
        }


class WorkspaceManager:
    def __init__(self, base_root: str | Path):
        self.base_root = Path(base_root).resolve()
        self.base_root.mkdir(parents=True, exist_ok=True)

    def create_workspace(
        self,
        *,
        project_id: str,
        job_id: str,
        task_number: int | None = None,
        source_root: str | Path | None = None,
    ) -> WorkspaceContext:
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        workspace_id = f"{project_id}-{job_id}-{stamp}-{uuid4().hex[:8]}"
        ws_root = self.base_root / workspace_id
        ws_root.mkdir(parents=True, exist_ok=False)
        source = Path(source_root).resolve() if source_root else None
        if source is not None and source.exists():
            self._bootstrap_source(source, ws_root)
        ctx = WorkspaceContext(
            workspace_id=workspace_id,
            project_id=project_id,
            job_id=job_id,
            task_number=task_number,
            workspace_root=ws_root,
            source_root=source,
            created_at_utc=datetime.now(UTC).isoformat(),
        )
        self._write_metadata(ctx)
        return ctx

    def teardown_workspace(
        self,
        *,
        context: WorkspaceContext,
        retain: bool = False,
        retention_reason: str | None = None,
    ) -> WorkspaceContext:
        if retain:
            context.retained = True
            context.retention_reason = retention_reason or "retained_by_policy"
            self._write_metadata(context)
            return context
        if context.workspace_root.exists():
            shutil.rmtree(context.workspace_root)
        return context

    def build_artifact_manifest(self, *, context: WorkspaceContext) -> dict:
        files = []
        if context.workspace_root.exists():
            for p in sorted(context.workspace_root.rglob("*")):
                if p.is_file():
                    files.append(
                        {
                            "path": str(p.relative_to(context.workspace_root)),
                            "size_bytes": p.stat().st_size,
                        }
                    )
        manifest = {
            "workspace_id": context.workspace_id,
            "project_id": context.project_id,
            "job_id": context.job_id,
            "task_number": context.task_number,
            "retained": context.retained,
            "retention_reason": context.retention_reason,
            "file_count": len(files),
            "files": files,
        }
        out = context.workspace_root / ".devexecutor_artifact_manifest.json"
        out.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return manifest

    def _bootstrap_source(self, source_root: Path, workspace_root: Path) -> None:
        for item in source_root.iterdir():
            if item.name in {".git", "__pycache__"}:
                continue
            target = workspace_root / item.name
            if item.is_dir():
                shutil.copytree(item, target)
            else:
                shutil.copy2(item, target)

    def _write_metadata(self, context: WorkspaceContext) -> None:
        meta = context.workspace_root / ".devexecutor_workspace.json"
        meta.write_text(json.dumps(context.to_dict(), indent=2), encoding="utf-8")
