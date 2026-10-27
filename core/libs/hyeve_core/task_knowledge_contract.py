from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

KnowledgeAuthority = Literal["draft", "derived", "authoritative"]
KnowledgeReviewStatus = Literal["pending_human_confirmation", "reviewed", "approved", "archived"]


class TaskKnowledgeCurrent(BaseModel):
    schema_version: str = Field(default="task.knowledge.current")
    knowledge_type: str = Field(min_length=1)
    authority: KnowledgeAuthority = Field(default="draft")
    review_status: KnowledgeReviewStatus = Field(default="pending_human_confirmation")
    summary: str | None = None
    rationale: str | None = None
    source_task: int | None = None
    source_task_id: str | None = None
    source_run: str | None = None
    source_refs: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


def build_task_knowledge_metadata(
    *,
    knowledge_type: str,
    authority: str | None = None,
    review_status: str | None = None,
    summary: str | None = None,
    rationale: str | None = None,
    source_task: int | None = None,
    source_task_id: str | None = None,
    source_run: str | None = None,
    source_refs: list[str] | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    payload = TaskKnowledgeCurrent(
        knowledge_type=str(knowledge_type or "").strip() or "general",
        authority=str(authority or "draft").strip() or "draft",
        review_status=str(review_status or "pending_human_confirmation").strip() or "pending_human_confirmation",
        summary=str(summary or "").strip() or None,
        rationale=str(rationale or "").strip() or None,
        source_task=int(source_task or 0) or None,
        source_task_id=str(source_task_id or "").strip() or None,
        source_run=str(source_run or "").strip() or None,
        source_refs=[str(item).strip() for item in list(source_refs or []) if str(item).strip()],
        tags=[str(item).strip() for item in list(tags or []) if str(item).strip()],
    )
    return payload.model_dump(mode="json")


def project_task_knowledge(metadata_json: dict[str, Any] | None) -> dict[str, Any] | None:
    metadata = dict(metadata_json or {})
    candidate = metadata.get("knowledge_current")
    if isinstance(candidate, dict):
        try:
            return TaskKnowledgeCurrent.model_validate(candidate).model_dump(mode="json")
        except Exception:
            pass
    if not bool(metadata.get("knowledge")):
        return None
    tags = metadata.get("tags") if isinstance(metadata.get("tags"), list) else []
    source_refs: list[str] = []
    source_task = metadata.get("source_task")
    source_run = metadata.get("source_run")
    source_task_id = metadata.get("source_task_id")
    if int(source_task or 0):
        source_refs.append(f"workhub://tasks/{int(source_task)}")
    if str(source_run or "").strip():
        source_refs.append(f"aix://runs/{str(source_run).strip()}")
    return build_task_knowledge_metadata(
        knowledge_type=str(metadata.get("knowledge_type") or metadata.get("kind") or "general").strip() or "general",
        authority="authoritative" if bool(metadata.get("authoritative")) else "draft",
        review_status=str(metadata.get("review_status") or "pending_human_confirmation").strip()
        or "pending_human_confirmation",
        summary=str(metadata.get("summary") or metadata.get("content") or "").strip() or None,
        rationale=str(metadata.get("rationale") or "").strip() or None,
        source_task=int(source_task or 0) or None,
        source_task_id=str(source_task_id or "").strip() or None,
        source_run=str(source_run or "").strip() or None,
        source_refs=source_refs,
        tags=[str(item).strip() for item in tags if str(item).strip()],
    )


__all__ = ["TaskKnowledgeCurrent", "build_task_knowledge_metadata", "project_task_knowledge"]
