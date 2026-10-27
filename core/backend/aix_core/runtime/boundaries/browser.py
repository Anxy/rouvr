"""Browser boundary: Browser Use is deny-by-default behind an allowlist.

No browser fetch may occur unless the target URL matches
`BROWSER_ALLOWLIST` (fnmatch, scheme+host qualified). An empty allowlist
means Browser Use is fully disabled. This is the Wave 5 operator rule:
browsing is an operator capability, never an execution-lane default.
"""

from __future__ import annotations

from fnmatch import fnmatchcase
from urllib.parse import urlparse


def normalize_browser_url(url: str) -> str:
    """Lowercase scheme+host, strip default ports, keep path for matching."""
    text = str(url or "").strip()
    if not text:
        return ""
    try:
        parts = urlparse(text if "://" in text else f"https://{text}")
    except ValueError:
        return ""
    scheme = (parts.scheme or "https").lower()
    host = (parts.hostname or "").lower()
    if not host:
        return ""
    port = parts.port
    default = (scheme == "https" and port == 443) or (scheme == "http" and port == 80)
    netloc = host if port is None or default else f"{host}:{port}"
    return f"{scheme}://{netloc}{parts.path or ''}"


def browser_url_allowed(url: str, *, allowlist: list[str] | tuple[str, ...] | None) -> bool:
    """True iff the normalized URL matches at least one allowlist entry.

    Entries are fnmatch patterns over the normalized URL, e.g.
    ``https://docs.internal.example/*``. Empty/None allowlist => deny all.
    """
    if not allowlist:
        return False
    normalized = normalize_browser_url(url)
    if not normalized:
        return False
    return any(
        fnmatchcase(normalized, str(entry or "").strip().lower())
        for entry in allowlist
        if str(entry or "").strip()
    )


def browser_allowlist_from_settings() -> list[str]:
    """Read the configured allowlist; missing setting => disabled."""
    try:
        from backend.hyeve_aix_service.config import settings as _settings

        raw = getattr(_settings, "BROWSER_ALLOWLIST", []) or []
        return [str(item) for item in raw if str(item or "").strip()]
    except Exception:
        return []
