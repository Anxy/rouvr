from __future__ import annotations

from backend.hyeve_aix_service.config import reload_settings


def test_service_base_urls_strip_api_current_suffix(monkeypatch) -> None:
    monkeypatch.setenv("HYEVE_AIX_SERVICE_WORKHUB_API_URL", "http://workhub:8221/api/v1")
    monkeypatch.setenv("HYEVE_AIX_SERVICE_OPSBUS_API_URL", "http://opsbus:8222/api/v1/")
    monkeypatch.setenv("HYEVE_AIX_SERVICE_CODEINTEL_API_URL", "http://codeintel:8331/api/v1")
    monkeypatch.setenv("HYEVE_AIX_SERVICE_AYRPACK_API_URL", "http://hyeve-ayrpack-service:8000/api/v1/")

    settings = reload_settings()

    assert str(settings.WORKHUB_API_URL) == "http://workhub:8221/"
    assert str(settings.OPSBUS_API_URL) == "http://opsbus:8222/"
    assert str(settings.CODEINTEL_API_URL) == "http://codeintel:8331/"
    assert str(settings.AYRPACK_API_URL) == "http://hyeve-ayrpack-service:8000/"


def test_operator_token_falls_back_for_service_auth_tokens(monkeypatch) -> None:
    monkeypatch.setenv("HYEVE_OPERATOR_TOKEN", "operator-token")
    monkeypatch.delenv("HYEVE_AIX_SERVICE_WORKHUB_API_TOKEN", raising=False)
    monkeypatch.delenv("HYEVE_AIX_SERVICE_OPSBUS_API_TOKEN", raising=False)
    monkeypatch.delenv("HYEVE_AIX_SERVICE_CODEINTEL_API_TOKEN", raising=False)
    monkeypatch.delenv("HYEVE_AIX_SERVICE_RUNTIME_PLANNER_AUTH_TOKEN", raising=False)

    settings = reload_settings()

    assert settings.WORKHUB_API_TOKEN == "operator-token"
    assert settings.WORKHUB_CLOSEOUT_API_TOKEN is None
    assert settings.OPSBUS_API_TOKEN is None
    assert settings.CODEINTEL_API_TOKEN == "operator-token"
    assert settings.RUNTIME_PLANNER_AUTH_TOKEN == "operator-token"


def test_service_specific_auth_tokens_override_operator_fallback(monkeypatch) -> None:
    monkeypatch.setenv("HYEVE_OPERATOR_TOKEN", "operator-token")
    monkeypatch.setenv("HYEVE_AIX_SERVICE_WORKHUB_API_TOKEN", "workhub-token")
    monkeypatch.setenv("HYEVE_AIX_SERVICE_WORKHUB_CLOSEOUT_API_TOKEN", "workhub-closeout-token")
    monkeypatch.setenv("HYEVE_AIX_SERVICE_OPSBUS_API_TOKEN", "opsbus-token")
    monkeypatch.setenv("HYEVE_AIX_SERVICE_CODEINTEL_API_TOKEN", "codeintel-token")
    monkeypatch.setenv("HYEVE_AIX_SERVICE_RUNTIME_PLANNER_AUTH_TOKEN", "planner-token")

    settings = reload_settings()

    assert settings.WORKHUB_API_TOKEN == "workhub-token"
    assert settings.WORKHUB_CLOSEOUT_API_TOKEN == "workhub-closeout-token"
    assert settings.OPSBUS_API_TOKEN == "opsbus-token"
    assert settings.CODEINTEL_API_TOKEN == "codeintel-token"
    assert settings.RUNTIME_PLANNER_AUTH_TOKEN == "planner-token"
