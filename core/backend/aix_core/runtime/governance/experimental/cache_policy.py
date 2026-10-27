"""AIX-E2E-CYCLE-001M-W06: performance/caching.

The named requirement is "no stale authority": a cache entry is invalid the instant the
underlying aggregate's authority marker (its ledger `sequence_number` from `001B`'s
event-sourced repository) advances, regardless of how much wall-clock TTL remains.
`AuthorityAwareCache.get` checks the authority version on every read; a TTL-only cache
that ignored this would be exactly the "stale authority" bug this packet exists to
prevent.
"""

from __future__ import annotations

from dataclasses import dataclass
from time import monotonic
from typing import Any


class CachePolicyError(ValueError):
    pass


@dataclass(frozen=True)
class CacheEntryV1:
    value: Any
    authority_version: int
    cached_at_monotonic: float
    ttl_seconds: float


class AuthorityAwareCache:
    def __init__(self, *, clock: Any = monotonic) -> None:
        self._clock = clock
        self._entries: dict[str, CacheEntryV1] = {}

    def set(self, key: str, *, value: Any, authority_version: int, ttl_seconds: float) -> None:
        if ttl_seconds <= 0:
            raise CachePolicyError("ttl_seconds must be positive")
        self._entries[key] = CacheEntryV1(
            value=value,
            authority_version=authority_version,
            cached_at_monotonic=self._clock(),
            ttl_seconds=ttl_seconds,
        )

    def get(self, key: str, *, current_authority_version: int) -> Any | None:
        """Returns the cached value only if BOTH the TTL has not expired AND
        `current_authority_version` still matches what was cached — either condition
        failing is a cache miss, never a stale hit. Returns `None` on any miss; a hit
        that legitimately caches `None` is out of scope for this bounded proof."""
        entry = self._entries.get(key)
        if entry is None:
            return None
        if entry.authority_version != current_authority_version:
            self.invalidate(key)
            return None
        if self._clock() - entry.cached_at_monotonic > entry.ttl_seconds:
            self.invalidate(key)
            return None
        return entry.value

    def invalidate(self, key: str) -> None:
        self._entries.pop(key, None)

    def invalidate_all(self) -> None:
        self._entries.clear()

    def __contains__(self, key: str) -> bool:
        return key in self._entries


__all__ = [
    "CachePolicyError",
    "CacheEntryV1",
    "AuthorityAwareCache",
]
