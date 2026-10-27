"""001D-W08: Token-efficiency engine — dedupe, cache, summaries, tiers, verification reserve."""
from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
from typing import Literal

ModelTier = Literal["nano", "small", "medium", "large", "verification"]


@dataclass(frozen=True)
class EfficiencyPlanV1:
    tier: ModelTier
    deduped_block_count: int
    cached_hit_count: int
    summary_applied: bool
    estimated_tokens_before: int
    estimated_tokens_after: int
    verification_token_reserve: int
    reason_codes: tuple[str, ...]


class TokenEfficiencyEngine:
    """Reduce prompt mass without dropping verification reserve."""

    def __init__(self) -> None:
        self._cache: dict[str, str] = {}

    def plan(
        self,
        *,
        blocks: list[str],
        role: str,
        verification_token_reserve: int,
        prefer_tier: ModelTier = "small",
    ) -> EfficiencyPlanV1:
        if verification_token_reserve < 0:
            raise ValueError("verification_token_reserve must be >= 0")

        before = sum(len(b) for b in blocks)
        seen: set[str] = set()
        deduped: list[str] = []
        cache_hits = 0
        for block in blocks:
            digest = sha256(block.encode("utf-8")).hexdigest()
            if digest in seen:
                continue
            seen.add(digest)
            if digest in self._cache:
                cache_hits += 1
                deduped.append(self._cache[digest])
            else:
                self._cache[digest] = block
                deduped.append(block)

        summary_applied = False
        if role in {"planner", "reviewer"} and len(deduped) > 4:
            # Keep first + last + hashed middles as summary proxy (deterministic).
            middles = [sha256(b.encode()).hexdigest()[:16] for b in deduped[1:-1]]
            deduped = [deduped[0], f"SUMMARY:{','.join(middles)}", deduped[-1]]
            summary_applied = True

        after = sum(len(b) for b in deduped)
        # Always leave verification reserve out of available estimate.
        available_after = max(0, after)
        tier: ModelTier = prefer_tier
        if role == "verifier":
            tier = "verification"
        elif after > 50_000:
            tier = "large"
        elif after > 12_000:
            tier = "medium"

        reasons = ["DEDUPE_APPLIED"]
        if cache_hits:
            reasons.append("CACHE_HITS")
        if summary_applied:
            reasons.append("SUMMARY_APPLIED")
        reasons.append("VERIFICATION_RESERVE_HELD")

        return EfficiencyPlanV1(
            tier=tier,
            deduped_block_count=len(deduped),
            cached_hit_count=cache_hits,
            summary_applied=summary_applied,
            estimated_tokens_before=before,
            estimated_tokens_after=available_after,
            verification_token_reserve=verification_token_reserve,
            reason_codes=tuple(reasons),
        )
