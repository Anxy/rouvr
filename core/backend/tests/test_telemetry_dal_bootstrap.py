import asyncio
import json
from pathlib import Path

from backend.infrastructure.telemetry.dal_bootstrap import resolve_dal_config_path, resolve_effective_dal_config_path
from backend.infrastructure.telemetry.hyeve_aix_service_telemetry import DAL_STATUS, health_check


def test_resolve_dal_config_path_points_to_repo_file() -> None:
    path = resolve_dal_config_path()

    assert path.name == "dal_config.json"
    assert path == Path(__file__).resolve().parents[1] / "hyeve_aix_service" / "dal_config.json"
    assert path.exists()


def test_effective_dal_config_uses_repo_file_without_runtime_overrides(monkeypatch) -> None:
    monkeypatch.delenv("HYEVE_AIX_SERVICE_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("HYEVE_AIX_SERVICE_REDIS_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)

    assert resolve_effective_dal_config_path() == resolve_dal_config_path()


def test_effective_dal_config_applies_runtime_connection_overrides(monkeypatch) -> None:
    database_url = "postgresql+asyncpg://user:pass@host.docker.internal:5432/hyeve_aix_service"
    redis_url = "redis://redis:6379/0"
    monkeypatch.setenv("HYEVE_AIX_SERVICE_DATABASE_URL", database_url)
    monkeypatch.setenv("HYEVE_AIX_SERVICE_REDIS_URL", redis_url)

    path = resolve_effective_dal_config_path()
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert path != resolve_dal_config_path()
    assert payload["database"]["primary"]["connection_string"] == database_url
    assert payload["cache"]["redis_url"] == redis_url
    assert resolve_dal_config_path().read_text(encoding="utf-8").find("host.docker.internal") == -1


def test_health_check_includes_dal_status() -> None:
    payload = asyncio.run(health_check())

    assert payload["service"] == "hyeve_aix_service"
    assert payload["dal"] is DAL_STATUS
    assert {"ready", "config_path", "source_config_path", "runtime_overrides_applied", "error"} <= set(
        payload["dal"].keys()
    )
