"""AIX-E2E-CYCLE-001G-W07 secret/credential boundary tests."""

from __future__ import annotations

import pytest

from backend.aix_core.runtime.governance.secret_boundary import (
    ResolvedSecretV1,
    SecretBoundary,
    SecretBoundaryError,
    SecretRefV1,
    looks_like_unredacted_secret,
)

NOW = "2026-07-30T00:00:00Z"


class TestResolve:
    def test_resolve_known_secret(self) -> None:
        boundary = SecretBoundary({"api-key-1": "sk-super-secret-value"}, now=NOW)
        resolved = boundary.resolve(SecretRefV1(name="api-key-1", rotated_at="2026-01-01T00:00:00Z"))
        assert resolved.value == "sk-super-secret-value"

    def test_resolve_unknown_secret_fails_closed(self) -> None:
        boundary = SecretBoundary({}, now=NOW)
        with pytest.raises(SecretBoundaryError):
            boundary.resolve(SecretRefV1(name="missing", rotated_at="2026-01-01T00:00:00Z"))

    def test_resolve_expired_secret_fails_closed(self) -> None:
        boundary = SecretBoundary({"api-key-1": "value"}, now=NOW)
        with pytest.raises(SecretBoundaryError):
            boundary.resolve(
                SecretRefV1(
                    name="api-key-1",
                    rotated_at="2026-01-01T00:00:00Z",
                    not_valid_after="2026-07-01T00:00:00Z",
                )
            )

    def test_resolve_not_yet_expired_succeeds(self) -> None:
        boundary = SecretBoundary({"api-key-1": "value"}, now=NOW)
        resolved = boundary.resolve(
            SecretRefV1(
                name="api-key-1",
                rotated_at="2026-01-01T00:00:00Z",
                not_valid_after="2026-12-31T00:00:00Z",
            )
        )
        assert resolved.value == "value"


class TestRedaction:
    def _resolved(self, name: str, value: str) -> ResolvedSecretV1:
        return ResolvedSecretV1(ref=SecretRefV1(name=name, rotated_at=NOW), value=value)

    def test_redact_text_replaces_value_with_ref_name(self) -> None:
        boundary = SecretBoundary({}, now=NOW)
        resolved = [self._resolved("db-password", "hunter2")]
        redacted = boundary.redact_text("connecting with password hunter2 now", resolved=resolved)
        assert "hunter2" not in redacted
        assert "***REDACTED:db-password***" in redacted

    def test_longer_secret_redacted_before_shorter_substring_secret(self) -> None:
        boundary = SecretBoundary({}, now=NOW)
        # "ab" is a substring of "abcdef"; redacting "ab" first would leave "cdef"
        # visible and defeat the point of redacting the longer secret.
        resolved = [self._resolved("short", "ab"), self._resolved("long", "abcdef")]
        redacted = boundary.redact_text("token=abcdef", resolved=resolved)
        assert "abcdef" not in redacted
        assert "cdef" not in redacted
        assert "***REDACTED:long***" in redacted

    def test_redact_structure_recurses_dict_list(self) -> None:
        boundary = SecretBoundary({}, now=NOW)
        resolved = [self._resolved("tok", "topsecret")]
        data = {"a": ["topsecret", {"b": "topsecret suffix"}], "c": 42}
        redacted = boundary.redact_structure(data, resolved=resolved)
        assert redacted["a"][0] == "***REDACTED:tok***"
        assert "topsecret" not in redacted["a"][1]["b"]
        assert redacted["c"] == 42

    def test_assert_no_leakage_raises_when_secret_present(self) -> None:
        boundary = SecretBoundary({}, now=NOW)
        resolved = [self._resolved("tok", "topsecret")]
        with pytest.raises(SecretBoundaryError):
            boundary.assert_no_leakage("still has topsecret in it", resolved=resolved)

    def test_assert_no_leakage_passes_after_redaction(self) -> None:
        boundary = SecretBoundary({}, now=NOW)
        resolved = [self._resolved("tok", "topsecret")]
        redacted = boundary.redact_text("has topsecret in it", resolved=resolved)
        boundary.assert_no_leakage(redacted, resolved=resolved)  # must not raise


class TestHeuristicScan:
    @pytest.mark.parametrize(
        "text",
        ["api_key: abc123", "SECRET=xyz", "password : hunter2", "token=abcdef"],
    )
    def test_detects_likely_secret_shapes(self, text: str) -> None:
        assert looks_like_unredacted_secret(text) is True

    def test_plain_text_not_flagged(self) -> None:
        assert looks_like_unredacted_secret("this is a normal log line about a test run") is False
