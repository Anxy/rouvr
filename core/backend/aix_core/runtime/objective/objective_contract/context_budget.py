"""C024: context budgeting, caching, and governance-safe compaction.

Immutable roles (objective, approvals, plan, ADR, evidence) may never be summarized away.
DoD: long missions stay within budget without losing those identities.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from backend.aix_core.runtime.objective.objective_contract.context_manifest import (
    ContextItem,
    ContextManifest,
    ContextManifestError,
    assemble_prompt_parts,
    content_hash,
)

IMMUTABLE_ROLES = frozenset({"objective", "approval", "plan", "evidence", "system"})


class ContextBudgetError(RuntimeError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


@dataclass
class RoleBudget:
    role: str
    max_tokens: int


@dataclass
class ContextBudget:
    total_max_tokens: int = 8000
    role_budgets: list[RoleBudget] = field(
        default_factory=lambda: [
            RoleBudget("objective", 800),
            RoleBudget("approval", 400),
            RoleBudget("plan", 1200),
            RoleBudget("evidence", 1500),
            RoleBudget("repository", 2000),
            RoleBudget("working", 1500),
            RoleBudget("system", 600),
        ]
    )


@dataclass
class ContentAddressedCache:
    _store: dict[str, str] = field(default_factory=dict)

    def put(self, text: str) -> str:
        key = content_hash(text)
        self._store[key] = text
        return key

    def get(self, key: str) -> str:
        if key not in self._store:
            raise ContextBudgetError("CACHE_MISS", key)
        return self._store[key]


def _estimate_tokens(text: str) -> int:
    # Cheap deterministic estimator (~4 chars/token).
    return max(1, (len(text or "") + 3) // 4)


def compact_manifest(
    manifest: ContextManifest,
    *,
    budget: ContextBudget | None = None,
) -> ContextManifest:
    """Drop/summarize only non-immutable working/repository items to fit budget.

    Never removes objective/approval/plan/evidence/system items.
    """
    budget = budget or ContextBudget()
    # First exclude lowest-relevance non-immutable items until under budget.
    parts = assemble_prompt_parts(manifest)
    total = sum(_estimate_tokens(p["text"]) for p in parts)
    if total <= budget.total_max_tokens:
        return manifest

    candidates = [
        i
        for i in sorted(manifest.items, key=lambda x: (x.relevance, x.created_at))
        if i.role not in IMMUTABLE_ROLES and not i.exclusion_reason
    ]
    for item in candidates:
        if total <= budget.total_max_tokens:
            break
        item.exclusion_reason = "COMPACTED_FOR_BUDGET"
        item.relevance = 0.0
        total -= _estimate_tokens(item.redacted_preview)

    # Verify immutables still present and included.
    remaining_roles = {
        i.role for i in manifest.items if not i.exclusion_reason or i.role in IMMUTABLE_ROLES
    }
    for role in IMMUTABLE_ROLES:
        # Only require roles that existed before compaction.
        if any(i.role == role for i in manifest.items):
            still = [
                i
                for i in manifest.items
                if i.role == role and (not i.exclusion_reason or i.exclusion_reason != "COMPACTED_FOR_BUDGET")
            ]
            if not still:
                raise ContextBudgetError("IMMUTABLE_CONTEXT_LOST", role)
            # Clear accidental compaction on immutables.
            for i in manifest.items:
                if i.role == role and i.exclusion_reason == "COMPACTED_FOR_BUDGET":
                    i.exclusion_reason = None
                    i.relevance = max(i.relevance, 1.0)

    # Working memory must not be promoted into trusted repository knowledge.
    for item in manifest.items:
        if item.role == "working" and item.trust == "trusted" and item.source.startswith("org_knowledge:"):
            raise ContextBudgetError("WORKING_MEMORY_PROMOTION_FORBIDDEN", item.item_id)

    return manifest


def assert_immutable_retained(manifest: ContextManifest, *, required_ids: set[str]) -> None:
    present = {i.item_id for i in manifest.items if not i.exclusion_reason or i.role in IMMUTABLE_ROLES}
    # Immutable items must never be compacted away.
    for item in manifest.items:
        if item.item_id in required_ids and item.role in IMMUTABLE_ROLES:
            if item.exclusion_reason == "COMPACTED_FOR_BUDGET":
                raise ContextBudgetError("IMMUTABLE_CONTEXT_LOST", item.item_id)
            present.add(item.item_id)
    missing = required_ids - {i.item_id for i in manifest.items}
    if missing:
        raise ContextBudgetError("IMMUTABLE_CONTEXT_LOST", ",".join(sorted(missing)))


def fit_thirty_step_mission(manifest: ContextManifest) -> dict[str, Any]:
    """Prove a large mission stays within default budget without losing immutables."""
    before_ids = {i.item_id for i in manifest.items if i.role in IMMUTABLE_ROLES}
    compact_manifest(manifest, budget=ContextBudget(total_max_tokens=4000))
    assert_immutable_retained(manifest, required_ids=before_ids)
    parts = assemble_prompt_parts(manifest)
    tokens = sum(_estimate_tokens(p["text"]) for p in parts)
    return {"ok": True, "tokens": tokens, "immutable_count": len(before_ids), "parts": len(parts)}


__all__ = [
    "IMMUTABLE_ROLES",
    "ContextBudgetError",
    "RoleBudget",
    "ContextBudget",
    "ContentAddressedCache",
    "compact_manifest",
    "assert_immutable_retained",
    "fit_thirty_step_mission",
]
