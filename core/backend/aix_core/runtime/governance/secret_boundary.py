"""AIX-E2E-CYCLE-001G-W07: secret/credential boundary for step execution.

Scope: this packet does not integrate a real secret store (Vault/KMS/etc. — none is
wired into this codebase's objective-cycle path yet). What it provides is real: a
`SecretRef` never carries a plaintext value across this boundary in the same object that
gets logged or bundled into evidence, and a redaction pass that scans arbitrary
text/structures for known secret values and replaces them before anything is allowed
into a transcript, log line, or evidence bundle.

Rotation: a `SecretRef` carries `rotated_at`; `SecretBoundary.resolve` rejects a ref whose
caller-supplied `not_valid_after` has passed, so a rotated/expired credential cannot be
resolved for a new attempt even if some caller still holds a stale reference to it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


class SecretBoundaryError(ValueError):
    """Raised when a secret cannot be safely resolved or when a redaction target is
    invalid. Never returns a partially-redacted result on error — fails closed."""


@dataclass(frozen=True)
class SecretRefV1:
    """A named pointer to a secret. `value` is intentionally NOT a field here — a ref
    is the thing that is allowed to appear in logs/evidence; the value only ever exists
    in a `ResolvedSecretV1`, which this module's own methods refuse to serialize."""

    name: str
    rotated_at: str
    not_valid_after: str | None = None


@dataclass(frozen=True)
class ResolvedSecretV1:
    """Holds an actual secret value. Deliberately has no `__repr__`/`__str__` override
    that would print `value` — Python's default dataclass repr *does* include field
    values, so callers must never `repr()`/log this object directly; use `ref` for
    logging instead. This is documented, not silently guaranteed by the type system."""

    ref: SecretRefV1
    value: str


class SecretBoundary:
    def __init__(self, secrets: dict[str, str], *, now: str) -> None:
        """`secrets` maps ref name -> plaintext value; `now` is an ISO-8601 timestamp
        string used for expiry comparison (injected, not read from the system clock,
        so expiry checks are deterministic and testable)."""
        self._secrets = dict(secrets)
        self._now = now

    def resolve(self, ref: SecretRefV1) -> ResolvedSecretV1:
        if ref.not_valid_after is not None and ref.not_valid_after <= self._now:
            raise SecretBoundaryError(f"secret {ref.name!r} expired at {ref.not_valid_after!r}")
        if ref.name not in self._secrets:
            raise SecretBoundaryError(f"no value available for secret ref {ref.name!r}")
        return ResolvedSecretV1(ref=ref, value=self._secrets[ref.name])

    def redact_text(self, text: str, *, resolved: list[ResolvedSecretV1]) -> str:
        """Replace every occurrence of a resolved secret's plaintext value with a
        stable placeholder naming the ref, never the value. Longest values are
        replaced first so a short secret that happens to be a substring of a longer
        one doesn't get partially redacted and leak the remainder."""
        redacted = text
        for item in sorted(resolved, key=lambda r: len(r.value), reverse=True):
            if not item.value:
                continue
            redacted = redacted.replace(item.value, f"***REDACTED:{item.ref.name}***")
        return redacted

    def redact_structure(self, data: Any, *, resolved: list[ResolvedSecretV1]) -> Any:
        """Recursively redact secret values out of a JSON-like structure (dict/list/str)
        before it is allowed into an evidence bundle or log record."""
        if isinstance(data, str):
            return self.redact_text(data, resolved=resolved)
        if isinstance(data, dict):
            return {key: self.redact_structure(value, resolved=resolved) for key, value in data.items()}
        if isinstance(data, list):
            return [self.redact_structure(item, resolved=resolved) for item in data]
        if isinstance(data, tuple):
            return tuple(self.redact_structure(item, resolved=resolved) for item in data)
        return data

    def assert_no_leakage(self, text: str, *, resolved: list[ResolvedSecretV1]) -> None:
        """Raise `SecretBoundaryError` if any resolved secret's plaintext value is still
        present in `text`. Call this on anything about to be written to evidence/logs as
        a last-line-of-defense check, independent of whether redaction was applied."""
        for item in resolved:
            if item.value and item.value in text:
                raise SecretBoundaryError(f"secret {item.ref.name!r} value present in output text")


# `\b` before the credential word fails to match when the word is itself preceded by an
# underscore-joined prefix (e.g. `SMTP_PASSWORD=...`) because `_` and the following
# letter are both "word" characters, so no boundary exists between them. `(^|[^A-Za-z0-9])`
# matches start-of-string or any non-alphanumeric separator (including `_`) instead, so
# `SMTP_PASSWORD=`, `DB_PASSWORD:`, `MY-API-KEY=` etc. are all caught, not just a bare
# `password=`. Captures the leading separator (1) and keyword (2) so
# `redact_objective_text` can preserve them in its replacement.
_REDACT_VALUE_PATTERN = re.compile(
    r"(?i)(^|[^A-Za-z0-9])(api[_-]?key|secret|token|password)(\s*[:=]\s*)(\S+)"
)


def looks_like_unredacted_secret(text: str) -> bool:
    """Heuristic scan for `key: value`/`key=value`-shaped secret-looking text that
    slipped past explicit redaction. Best-effort only — a False result is not proof of
    safety; `assert_no_leakage` against known resolved values is the real guarantee."""
    return bool(_REDACT_VALUE_PATTERN.search(text))


def redact_objective_text(text: str) -> tuple[str, list[str]]:
    """Server-side mirror of the extension's `secrets.ts::redactSecrets` — same
    architecture (replace the whole `KEY=VALUE` span with a placeholder naming the
    kind, never the value), applied here as defense-in-depth since the backend must
    never trust that client-side redaction actually ran. Returns `(redacted_text,
    kinds_found)`; `kinds_found` is empty when nothing matched."""
    kinds: list[str] = []

    def _sub(match: "re.Match[str]") -> str:
        kinds.append(match.group(2).lower())
        return f"{match.group(1)}{match.group(2)}{match.group(3)}***REDACTED***"

    redacted = _REDACT_VALUE_PATTERN.sub(_sub, text)
    return redacted, kinds


__all__ = [
    "SecretBoundaryError",
    "SecretRefV1",
    "ResolvedSecretV1",
    "redact_objective_text",
    "SecretBoundary",
    "looks_like_unredacted_secret",
]
