"""validate_vps_runtime_config: drift reporting for Hyeve dependencies."""

from __future__ import annotations

from types import SimpleNamespace

from backend.hyeve_aix_service.config import validate_vps_runtime_config


def _settings(**overrides: object) -> object:
    base = {
        "RUNTIME_PLANNER_API_URL": "http://127.0.0.1:8020",
        "WORKHUB_API_URL": "http://127.0.0.1:8226",
        "CODEINTEL_API_URL": "http://127.0.0.1:8331",
        "OBJECTIVE_CYCLE_WORKSPACE_BASE": "/srv/rouvr/workspace/cycles",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_explicit_loopback_config_is_clean(monkeypatch) -> None:
    monkeypatch.setenv("HYEVE_AIX_SERVICE_RUNTIME_PLANNER_API_URL", "http://127.0.0.1:8020")
    monkeypatch.setenv("HYEVE_AIX_SERVICE_WORKHUB_API_URL", "http://127.0.0.1:8226")
    monkeypatch.setenv("HYEVE_AIX_SERVICE_CODEINTEL_API_URL", "http://127.0.0.1:8331")
    monkeypatch.setenv("HYEVE_AIX_SERVICE_OBJECTIVE_CYCLE_WORKSPACE_BASE", "/srv/rouvr/workspace/cycles")
    report = validate_vps_runtime_config(_settings())
    assert report["clean"] is True
    assert report["drift"] == []


def test_docker_default_is_flagged(monkeypatch) -> None:
    for var in (
        "HYEVE_AIX_SERVICE_RUNTIME_PLANNER_API_URL",
        "HYEVE_AIX_SERVICE_WORKHUB_API_URL",
        "HYEVE_AIX_SERVICE_CODEINTEL_API_URL",
        "HYEVE_AIX_SERVICE_OBJECTIVE_CYCLE_WORKSPACE_BASE",
    ):
        monkeypatch.delenv(var, raising=False)
    report = validate_vps_runtime_config(
        _settings(
            RUNTIME_PLANNER_API_URL="http://integration:8020",
            WORKHUB_API_URL="http://workhub:8221",
            CODEINTEL_API_URL="http://codeintel:8331",
        )
    )
    assert report["clean"] is False
    assert sorted(report["drift"]) == ["codeintel", "integration", "workhub"]


def test_windows_workspace_base_is_flagged(monkeypatch) -> None:
    monkeypatch.setenv("HYEVE_AIX_SERVICE_RUNTIME_PLANNER_API_URL", "http://127.0.0.1:8020")
    monkeypatch.setenv("HYEVE_AIX_SERVICE_WORKHUB_API_URL", "http://127.0.0.1:8226")
    monkeypatch.setenv("HYEVE_AIX_SERVICE_CODEINTEL_API_URL", "http://127.0.0.1:8331")
    monkeypatch.delenv("HYEVE_AIX_SERVICE_OBJECTIVE_CYCLE_WORKSPACE_BASE", raising=False)
    report = validate_vps_runtime_config(_settings(OBJECTIVE_CYCLE_WORKSPACE_BASE="D:\\v2.1\\workspaces"))
    assert report["clean"] is False
    assert report["drift"] == ["workspace_base"]
