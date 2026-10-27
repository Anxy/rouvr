"""
Pack-aware error catalog implementation.

The catalog resolves error codes using the layered messages stored in
``PackBundle.messages`` and falls back to a global catalog when no override is
present.  Messages use Python ``str.format`` placeholders, making it easy to
pass contextual parameters.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..core_libs.interfaces import ErrorCatalog
from ..core_libs.types import PackBundle, ResolvedError


@dataclass
class ErrorCatalogConfig:
    """Configuration for :class:`LayeredErrorCatalog`."""

    default_messages: dict[str, dict[str, str]] = field(default_factory=dict)
    default_http_hints: dict[str, int] = field(default_factory=dict)
    default_locale: str = "en"


class LayeredErrorCatalog(ErrorCatalog):
    """Resolve error codes using pack messages with sane fallbacks."""

    def __init__(self, config: ErrorCatalogConfig | None = None):
        self._config = config or ErrorCatalogConfig()

    def resolve(
        self,
        code: str,
        *,
        params: dict[str, Any] | None = None,
        bundle: PackBundle | None = None,
        locale: str | None = None,
    ) -> ResolvedError:
        params = params or {}
        locale = locale or self._config.default_locale

        message = self._resolve_message(code, locale, bundle) or self._fallback_message(code, locale, params)

        http_hint = self._resolve_http_hint(code, bundle)

        formatted_message = message
        if "{" in message:
            try:
                formatted_message = message.format(**params)
            except KeyError:
                # Preserve template to aid debugging if parameters are missing.
                formatted_message = message

        return ResolvedError(
            code=code,
            message=formatted_message,
            http_status_hint=http_hint,
            locale=locale,
            params=params,
        )

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #
    def _resolve_message(
        self,
        code: str,
        locale: str,
        bundle: PackBundle | None,
    ) -> str | None:
        if not bundle:
            return None

        locales = [
            locale,
            self._config.default_locale,
            "en",
        ]

        for candidate in locales:
            messages = bundle.messages.get(candidate)
            if not messages:
                continue
            message = self._resolve_nested(code, messages)
            if isinstance(message, str):
                return message
        return None

    def _fallback_message(
        self,
        code: str,
        locale: str,
        params: dict[str, Any],
    ) -> str:
        message = self._resolve_nested(
            code,
            self._config.default_messages.get(locale, {}),
        )
        if isinstance(message, str):
            return message

        # Absolute fallback: keep the code to aid debugging
        return f"{code} ({params})"

    def _resolve_http_hint(self, code: str, bundle: PackBundle | None) -> int:
        if bundle and code in bundle.http_hints:
            return int(bundle.http_hints[code])
        return int(self._config.default_http_hints.get(code, 400))

    @staticmethod
    def _resolve_nested(code: str, tree: dict[str, Any]) -> str | None:
        current: Any = tree
        for part in code.split("."):
            if not isinstance(current, dict):
                return None
            current = current.get(part)
            if current is None:
                return None
        return current if isinstance(current, str) else None
