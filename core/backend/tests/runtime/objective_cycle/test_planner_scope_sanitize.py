from backend.infrastructure.model_gateway.intelligence_layer.service import (
    _sanitize_file_hints,
    _sanitize_proposed_scope,
)


def test_sanitize_file_hints_strips_trailing_prose() -> None:
    assert _sanitize_file_hints(
        [".env (inspection only; no credential modification)"]
    ) == [".env"]
    assert _sanitize_proposed_scope(["backend/config.py (maybe)"]) == ["backend/config.py"]


def test_sanitize_preserves_dotfiles_without_inventing_scope() -> None:
    # Empty planner hints stay empty — no .env fallback from user text.
    assert _sanitize_file_hints([]) == []
    assert _sanitize_file_hints(["./.env"]) == [".env"]
    assert _sanitize_file_hints(
        [r"D:\v2.1\hyeve\services\hyeve-integration-service\.env"]
    ) == [".env"]


def test_admit_allowed_file_sanitizer() -> None:
    from backend.aix_core.runtime.executor.read_file_execute import sanitize_allowed_file_entry

    assert sanitize_allowed_file_entry(".env (inspection only; no credential modification)") == ".env"
    assert sanitize_allowed_file_entry(".env") == ".env"
