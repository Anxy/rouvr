#!/usr/bin/env python3
"""Credential broker: resolve ONLY the lane-ops #334 dev identity.

Lane-ops #334 ("Durable credential placement for a second host") carries
no published identity material, so the allowlisted dev identity is an
unresolved input: the broker is parameterised on a single allowlisted
identity slot and defaults to deny-all. Fail closed on anything else.

Secrets discipline: secrets live in memory only. The broker never reads
files, environment, or network; the caller supplies a provider callable
invoked at resolve time, and the broker stores nothing it returns. The
broker never logs or persists secret values.
"""
from __future__ import annotations

from typing import Callable


class CredentialRefused(Exception):
    """Raised whenever an identity must not be resolved (fail closed)."""


class CredentialBroker:
    """Single-slot allowlist broker for the #334 dev identity."""

    def __init__(self, allowlisted_identity: str | None = None) -> None:
        self._allowlisted = allowlisted_identity or None

    @property
    def allowlisted_identity(self) -> str | None:
        return self._allowlisted

    def is_configured(self) -> bool:
        """False while the #334 dev identity remains unresolved."""
        return self._allowlisted is not None

    def resolve(self, identity: str | None,
                provider: Callable[[], object]) -> object:
        """Return the secret for `identity` iff it is the allowlisted one.

        Refuses (raises CredentialRefused) when: the broker is
        unconfigured, `identity` is missing/empty, `identity` differs
        from the allowlisted value, or `provider` is not callable. The
        provider is invoked only on the allow path; its return value is
        handed back without being stored, logged, or persisted.
        """
        if not self.is_configured():
            raise CredentialRefused("broker-unconfigured: #334 dev "
                                    "identity unresolved")
        if not isinstance(identity, str) or not identity:
            raise CredentialRefused("refused: missing identity")
        if identity != self._allowlisted:
            raise CredentialRefused("refused: identity not allowlisted")
        if not callable(provider):
            raise CredentialRefused("refused: no secret provider")
        return provider()

    def __repr__(self) -> str:  # never leak the allowlisted value
        state = "configured" if self.is_configured() else "unconfigured"
        return f"CredentialBroker({state})"
