"""AIX-E2E-CYCLE-001K-W07 Celery/platform-event isolation tests."""

from __future__ import annotations

import pytest

from backend.aix_core.runtime.governance.experimental.celery_isolation import (
    CeleryIsolationError,
    RedisEndpoint,
    validate_isolation,
)


class TestRedisEndpointParse:
    def test_parses_host_port_db(self) -> None:
        endpoint = RedisEndpoint.parse("redis://localhost:6379/2")
        assert endpoint == RedisEndpoint(host="localhost", port=6379, db_index=2)

    def test_defaults_port_and_db(self) -> None:
        endpoint = RedisEndpoint.parse("redis://localhost")
        assert endpoint.port == 6379
        assert endpoint.db_index == 0

    def test_non_redis_scheme_rejected(self) -> None:
        with pytest.raises(CeleryIsolationError):
            RedisEndpoint.parse("postgresql://localhost/db")

    def test_non_numeric_db_index_rejected(self) -> None:
        with pytest.raises(CeleryIsolationError):
            RedisEndpoint.parse("redis://localhost/not-a-number")


class TestValidateIsolation:
    def test_different_db_index_same_host_is_ok_in_development(self) -> None:
        result = validate_isolation(
            celery_url="redis://localhost:6379/0",
            aix_streams_url="redis://localhost:6379/1",
            environment="development",
        )
        assert result.ok is True
        assert result.errors == ()

    def test_identical_host_port_db_is_hard_error_in_any_environment(self) -> None:
        for env in ("development", "staging", "production"):
            result = validate_isolation(
                celery_url="redis://localhost:6379/0",
                aix_streams_url="redis://localhost:6379/0",
                environment=env,  # type: ignore[arg-type]
            )
            assert result.ok is False
            assert any("IDENTICAL" in e for e in result.errors)

    def test_different_host_is_always_fully_clean(self) -> None:
        result = validate_isolation(
            celery_url="redis://celery-host:6379/0",
            aix_streams_url="redis://streams-host:6379/0",
            environment="production",
        )
        assert result.ok is True
        assert result.warnings == ()

    def test_production_shared_host_different_db_warns_but_does_not_hard_fail(self) -> None:
        result = validate_isolation(
            celery_url="redis://shared-host:6379/0",
            aix_streams_url="redis://shared-host:6379/1",
            environment="production",
        )
        assert result.ok is True
        assert any("PRODUCTION_SHARED_HOST" in w for w in result.warnings)

    def test_development_shared_host_different_db_does_not_warn(self) -> None:
        # The "preferably separate instances" recommendation is production-specific.
        result = validate_isolation(
            celery_url="redis://shared-host:6379/0",
            aix_streams_url="redis://shared-host:6379/1",
            environment="development",
        )
        assert result.ok is True
        assert result.warnings == ()
