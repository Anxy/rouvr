"""001D-W05: Prompt registry and versioning."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal


def _utc() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


PromptStatus = Literal["active", "deprecated", "rolled_back"]


@dataclass(frozen=True)
class PromptVersionV1:
    prompt_id: str
    version: str
    role: str
    body_hash: str
    body: str
    status: PromptStatus = "active"
    created_at: str = field(default_factory=_utc)
    change_review_ref: str | None = None


class PromptRegistryError(ValueError):
    pass


class PromptRegistry:
    """In-process versioned prompt registry with rollback evidence."""

    def __init__(self) -> None:
        self._versions: dict[tuple[str, str], PromptVersionV1] = {}
        self._active: dict[str, str] = {}
        self._history: list[dict[str, str]] = []

    def register(self, prompt: PromptVersionV1) -> PromptVersionV1:
        if not prompt.change_review_ref:
            raise PromptRegistryError("change_review_ref required")
        key = (prompt.prompt_id, prompt.version)
        if key in self._versions:
            raise PromptRegistryError(f"prompt version already exists: {key}")
        self._versions[key] = prompt
        if prompt.status == "active":
            self._active[prompt.prompt_id] = prompt.version
        self._history.append(
            {"event": "register", "prompt_id": prompt.prompt_id, "version": prompt.version, "at": _utc()}
        )
        return prompt

    def get_active(self, prompt_id: str) -> PromptVersionV1:
        version = self._active.get(prompt_id)
        if not version:
            raise PromptRegistryError(f"no active prompt: {prompt_id}")
        return self._versions[(prompt_id, version)]

    def deprecate(self, prompt_id: str, version: str, *, review_ref: str) -> PromptVersionV1:
        current = self._versions.get((prompt_id, version))
        if current is None:
            raise PromptRegistryError("unknown prompt version")
        updated = PromptVersionV1(
            prompt_id=current.prompt_id,
            version=current.version,
            role=current.role,
            body_hash=current.body_hash,
            body=current.body,
            status="deprecated",
            created_at=current.created_at,
            change_review_ref=review_ref,
        )
        self._versions[(prompt_id, version)] = updated
        self._history.append(
            {"event": "deprecate", "prompt_id": prompt_id, "version": version, "review_ref": review_ref, "at": _utc()}
        )
        return updated

    def rollback(self, prompt_id: str, to_version: str, *, review_ref: str) -> PromptVersionV1:
        target = self._versions.get((prompt_id, to_version))
        if target is None:
            raise PromptRegistryError("rollback target missing")
        prev = self._active.get(prompt_id)
        if prev:
            old = self._versions[(prompt_id, prev)]
            self._versions[(prompt_id, prev)] = PromptVersionV1(
                prompt_id=old.prompt_id,
                version=old.version,
                role=old.role,
                body_hash=old.body_hash,
                body=old.body,
                status="rolled_back",
                created_at=old.created_at,
                change_review_ref=review_ref,
            )
        self._active[prompt_id] = to_version
        restored = PromptVersionV1(
            prompt_id=target.prompt_id,
            version=target.version,
            role=target.role,
            body_hash=target.body_hash,
            body=target.body,
            status="active",
            created_at=target.created_at,
            change_review_ref=review_ref,
        )
        self._versions[(prompt_id, to_version)] = restored
        self._history.append(
            {
                "event": "rollback",
                "prompt_id": prompt_id,
                "to_version": to_version,
                "from_version": prev or "",
                "review_ref": review_ref,
                "at": _utc(),
            }
        )
        return restored

    def evidence(self) -> list[dict[str, str]]:
        return list(self._history)
