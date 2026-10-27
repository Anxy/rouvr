from __future__ import annotations

from backend.services.devexecutor_pyright_lsp_service import (
    _document_symbol_request_timeout_seconds,
    _LSP_MAX_DOCUMENT_SYMBOL_TIMEOUT_SECONDS,
    _LSP_REQUEST_TIMEOUT_SECONDS,
)


def test_document_symbol_timeout_stays_at_default_for_small_sources() -> None:
    source = "def demo() -> int:\n    return 1\n"

    timeout_seconds = _document_symbol_request_timeout_seconds(source)

    assert timeout_seconds == _LSP_REQUEST_TIMEOUT_SECONDS


def test_document_symbol_timeout_scales_for_large_sources() -> None:
    source = ("def demo() -> int:\n    return 1\n" * 30000).rstrip()

    timeout_seconds = _document_symbol_request_timeout_seconds(source)

    assert timeout_seconds > _LSP_REQUEST_TIMEOUT_SECONDS
    assert timeout_seconds > 40.0
    assert timeout_seconds <= _LSP_MAX_DOCUMENT_SYMBOL_TIMEOUT_SECONDS
