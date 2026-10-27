"""AIX-E2E-CYCLE-001M-W06 performance/caching tests."""

from __future__ import annotations

import pytest

from backend.aix_core.runtime.governance.experimental.cache_policy import (
    AuthorityAwareCache,
    CachePolicyError,
)


class _FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now


class TestSetGet:
    def test_non_positive_ttl_rejected(self) -> None:
        cache = AuthorityAwareCache()
        with pytest.raises(CachePolicyError):
            cache.set("k", value=1, authority_version=1, ttl_seconds=0)

    def test_hit_within_ttl_and_matching_authority(self) -> None:
        clock = _FakeClock()
        cache = AuthorityAwareCache(clock=clock)
        cache.set("k", value="v1", authority_version=5, ttl_seconds=10)
        assert cache.get("k", current_authority_version=5) == "v1"

    def test_miss_on_missing_key(self) -> None:
        cache = AuthorityAwareCache()
        assert cache.get("missing", current_authority_version=1) is None


class TestStaleAuthorityNeverServed:
    def test_stale_authority_is_a_miss_even_within_ttl(self) -> None:
        clock = _FakeClock()
        cache = AuthorityAwareCache(clock=clock)
        cache.set("k", value="v1", authority_version=5, ttl_seconds=1000)
        clock.now += 1  # well within TTL
        assert cache.get("k", current_authority_version=6) is None  # authority advanced

    def test_stale_hit_invalidates_the_entry(self) -> None:
        clock = _FakeClock()
        cache = AuthorityAwareCache(clock=clock)
        cache.set("k", value="v1", authority_version=5, ttl_seconds=1000)
        cache.get("k", current_authority_version=6)
        assert "k" not in cache

    def test_expired_ttl_is_a_miss_even_with_matching_authority(self) -> None:
        clock = _FakeClock()
        cache = AuthorityAwareCache(clock=clock)
        cache.set("k", value="v1", authority_version=5, ttl_seconds=10)
        clock.now += 11
        assert cache.get("k", current_authority_version=5) is None


class TestInvalidation:
    def test_invalidate_removes_entry(self) -> None:
        cache = AuthorityAwareCache()
        cache.set("k", value="v1", authority_version=1, ttl_seconds=10)
        cache.invalidate("k")
        assert "k" not in cache

    def test_invalidate_all_clears_everything(self) -> None:
        cache = AuthorityAwareCache()
        cache.set("k1", value=1, authority_version=1, ttl_seconds=10)
        cache.set("k2", value=2, authority_version=1, ttl_seconds=10)
        cache.invalidate_all()
        assert "k1" not in cache and "k2" not in cache
