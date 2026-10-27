"""AIX-E2E-CYCLE-001K-W07: Celery/platform-event isolation.

Per the architecture doc's runtime decision: "Celery may use a Redis-compatible runtime,
but Celery jobs and platform events require separate databases/prefixes locally and
preferably separate instances in production." This module is a configuration validator,
not a runtime connector — it parses two Redis-shaped URLs and fails closed if they would
collide, before either is ever used to construct a real connection.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlparse

Environment = Literal["development", "staging", "production"]


class CeleryIsolationError(ValueError):
    pass


@dataclass(frozen=True)
class RedisEndpoint:
    host: str
    port: int
    db_index: int

    @classmethod
    def parse(cls, url: str) -> "RedisEndpoint":
        parsed = urlparse(url)
        if parsed.scheme not in ("redis", "rediss"):
            raise CeleryIsolationError(f"not a redis-shaped URL: {url!r}")
        if not parsed.hostname:
            raise CeleryIsolationError(f"redis URL missing host: {url!r}")
        path = (parsed.path or "").lstrip("/")
        try:
            db_index = int(path) if path else 0
        except ValueError as exc:
            raise CeleryIsolationError(f"redis URL has non-numeric db index: {url!r}") from exc
        return cls(host=parsed.hostname, port=parsed.port or 6379, db_index=db_index)


@dataclass(frozen=True)
class IsolationCheckResult:
    ok: bool
    errors: tuple[str, ...]
    warnings: tuple[str, ...]


def validate_isolation(
    *,
    celery_url: str,
    aix_streams_url: str,
    environment: Environment,
) -> IsolationCheckResult:
    """Hard-fails (returns `ok=False` with an error) if Celery and AIX platform events
    would share the exact same host+port+db — that is never acceptable, in any
    environment: Celery traffic degrading platform events (or vice versa) is exactly the
    failure mode this packet exists to prevent. In `production`, additionally *warns*
    (does not hard-fail — the architecture doc says "preferably", not "must") if the two
    share a host, since the intent is separate instances in production but a shared host
    with disjoint db indices is not the same failure class as a fully shared namespace.
    """
    celery = RedisEndpoint.parse(celery_url)
    streams = RedisEndpoint.parse(aix_streams_url)

    errors: list[str] = []
    warnings: list[str] = []

    same_host_port = (celery.host, celery.port) == (streams.host, streams.port)
    same_db = celery.db_index == streams.db_index

    if same_host_port and same_db:
        errors.append(
            "CELERY_AND_STREAMS_SHARE_IDENTICAL_HOST_PORT_DB: "
            f"both resolve to {celery.host}:{celery.port}/{celery.db_index}"
        )

    if environment == "production" and same_host_port and not errors:
        warnings.append(
            "PRODUCTION_SHARED_HOST_RECOMMEND_SEPARATE_INSTANCE: "
            f"celery and streams both target host {celery.host}:{celery.port} "
            "(different db indices avoid data collision, but architecture doc prefers "
            "separate instances in production)"
        )

    return IsolationCheckResult(ok=not errors, errors=tuple(errors), warnings=tuple(warnings))


__all__ = [
    "Environment",
    "CeleryIsolationError",
    "RedisEndpoint",
    "IsolationCheckResult",
    "validate_isolation",
]
