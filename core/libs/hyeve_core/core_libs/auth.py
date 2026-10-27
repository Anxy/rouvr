"""Hyeve auth-mode helpers.

Centralizes local/test auth handling and token defaults.
"""
from __future__ import annotations

import os
import re

_LOCAL_TOKENS_ENV = "HYEVE_LOCAL_AUTH_TOKENS"
_DEFAULT_TOKENS = "test-admin-demo,test-admin-token,local-admin"


def resolve_auth_mode() -> str:
    """Resolve auth mode, honoring service-specific overrides first."""
    for key in (
        "BUSINESS_RULES_SERVICE_AUTH_MODE",
        "WORKFLOW_SERVICE_AUTH_MODE",
        "HYEVE_AUTH_MODE",
    ):
        value = os.getenv(key, "").strip().lower()
        if value:
            return value
    return ""


def is_local_auth_mode() -> bool:
    """Return True when local/test auth mode is enabled."""
    return resolve_auth_mode() in {"local", "test"}


def local_auth_tokens() -> list[str]:
    """Return configured local auth tokens in priority order."""
    raw = os.getenv(_LOCAL_TOKENS_ENV, _DEFAULT_TOKENS)
    tokens = [tok.strip() for tok in re.split(r"[;,\s]+", raw) if tok.strip()]
    return tokens or ["local-admin"]


def is_local_auth_token(token: str) -> bool:
    """Check whether the token is accepted for local auth."""
    return token in set(local_auth_tokens())
