from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Protocol


class LeaseStore(Protocol):
    def set(self, key: str, value: str, *, nx: bool = False, ex: int | None = None) -> bool:
        ...

    def get(self, key: str) -> str | None:
        ...

    def expire(self, key: str, ttl_seconds: int) -> bool:
        ...

    def delete(self, key: str) -> bool:
        ...

    def exists(self, key: str) -> bool:
        ...

    def ttl(self, key: str) -> int:
        ...


class InMemoryLeaseStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: dict[str, tuple[str, float | None]] = {}

    def _purge_if_expired(self, key: str) -> None:
        item = self._items.get(key)
        if item is None:
            return
        _, expires_at = item
        if expires_at is not None and expires_at <= time.monotonic():
            self._items.pop(key, None)

    def set(self, key: str, value: str, *, nx: bool = False, ex: int | None = None) -> bool:
        with self._lock:
            self._purge_if_expired(key)
            if nx and key in self._items:
                return False
            expires_at = None if ex is None else time.monotonic() + max(1, int(ex))
            self._items[key] = (value, expires_at)
            return True

    def get(self, key: str) -> str | None:
        with self._lock:
            self._purge_if_expired(key)
            item = self._items.get(key)
            return item[0] if item is not None else None

    def expire(self, key: str, ttl_seconds: int) -> bool:
        with self._lock:
            self._purge_if_expired(key)
            item = self._items.get(key)
            if item is None:
                return False
            self._items[key] = (item[0], time.monotonic() + max(1, int(ttl_seconds)))
            return True

    def delete(self, key: str) -> bool:
        with self._lock:
            self._purge_if_expired(key)
            return self._items.pop(key, None) is not None

    def exists(self, key: str) -> bool:
        with self._lock:
            self._purge_if_expired(key)
            return key in self._items

    def ttl(self, key: str) -> int:
        with self._lock:
            self._purge_if_expired(key)
            item = self._items.get(key)
            if item is None:
                return -2
            expires_at = item[1]
            if expires_at is None:
                return -1
            remaining = int(expires_at - time.monotonic())
            if remaining <= 0:
                self._items.pop(key, None)
                return -2
            return remaining


class RedisLeaseStore:
    def __init__(self, client: object) -> None:
        self._client = client

    def set(self, key: str, value: str, *, nx: bool = False, ex: int | None = None) -> bool:
        return bool(self._client.set(key, value, nx=nx, ex=ex))

    def get(self, key: str) -> str | None:
        raw = self._client.get(key)
        if raw is None:
            return None
        if isinstance(raw, bytes):
            return raw.decode("utf-8", errors="replace")
        return str(raw)

    def expire(self, key: str, ttl_seconds: int) -> bool:
        return bool(self._client.expire(key, ttl_seconds))

    def delete(self, key: str) -> bool:
        return bool(self._client.delete(key))

    def exists(self, key: str) -> bool:
        return bool(self._client.exists(key))

    def ttl(self, key: str) -> int:
        return int(self._client.ttl(key))


class FailsafeLeaseStore:
    def __init__(self, primary: LeaseStore, fallback: LeaseStore) -> None:
        self._active: LeaseStore = primary
        self._fallback = fallback
        self._failed_over = False

    def _call(self, method_name: str, *args: object, **kwargs: object) -> object:
        try:
            method = getattr(self._active, method_name)
            return method(*args, **kwargs)
        except Exception:
            if not self._failed_over:
                self._active = self._fallback
                self._failed_over = True
                method = getattr(self._active, method_name)
                return method(*args, **kwargs)
            raise

    def set(self, key: str, value: str, *, nx: bool = False, ex: int | None = None) -> bool:
        return bool(self._call("set", key, value, nx=nx, ex=ex))

    def get(self, key: str) -> str | None:
        value = self._call("get", key)
        return None if value is None else str(value)

    def expire(self, key: str, ttl_seconds: int) -> bool:
        return bool(self._call("expire", key, ttl_seconds))

    def delete(self, key: str) -> bool:
        return bool(self._call("delete", key))

    def exists(self, key: str) -> bool:
        return bool(self._call("exists", key))

    def ttl(self, key: str) -> int:
        return int(self._call("ttl", key))


class TaskLeaseEngine:
    def __init__(
        self,
        *,
        store: LeaseStore,
        ttl_sec: int = 300,
        stale_resolver: Callable[[str, str], bool] | None = None,
    ) -> None:
        self._store = store
        self._ttl_sec = max(1, int(ttl_sec))
        self._stale_resolver = stale_resolver

    def configure_stale_resolver(self, resolver: Callable[[str, str], bool] | None) -> None:
        self._stale_resolver = resolver

    def _key(self, task_id: str) -> str:
        return f"lease:{task_id}"

    def claim(self, task_id: str, inv_id: str) -> bool:
        key = self._key(task_id)
        owner = self._store.get(key)
        if owner == inv_id:
            return bool(self._store.expire(key, self._ttl_sec))
        return bool(self._store.set(key, inv_id, nx=True, ex=self._ttl_sec))

    def heartbeat(self, task_id: str, inv_id: str) -> bool:
        key = self._key(task_id)
        if self._store.get(key) != inv_id:
            return False
        return bool(self._store.expire(key, self._ttl_sec))

    def release(self, task_id: str, inv_id: str) -> bool:
        key = self._key(task_id)
        if self._store.get(key) != inv_id:
            return False
        return bool(self._store.delete(key))

    def force_resolve_if_expired(self, task_id: str, reason: str) -> bool:
        key = self._key(task_id)
        expired = not self._store.exists(key) or self._store.ttl(key) <= 0
        if not expired:
            return False
        if self._stale_resolver is None:
            return False
        return bool(self._stale_resolver(task_id, reason))

    def owns(self, task_id: str, inv_id: str) -> bool:
        return self._store.get(self._key(task_id)) == inv_id


def build_best_effort_lease_store(*, redis_url: str | None = None) -> LeaseStore:
    normalized_url = str(redis_url or "").strip()
    if normalized_url:
        try:
            import redis  # type: ignore

            client = redis.from_url(normalized_url, decode_responses=False)
            client.ping()
            return FailsafeLeaseStore(RedisLeaseStore(client), InMemoryLeaseStore())
        except Exception:
            pass
    return InMemoryLeaseStore()
