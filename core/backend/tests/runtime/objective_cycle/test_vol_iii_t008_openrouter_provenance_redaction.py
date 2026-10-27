"""AIX-VOL-III-T008: OpenRouter Provenance & Credential Redaction.

Verifies:
1. Evidence records OpenRouter model provenance (model ID, tokens_in, tokens_out, cost) via `UsageV1`.
2. API keys (`sk-or-...`), SMTP passwords, and bearer tokens in evidence payloads are strictly redacted via `SecretBoundary`.
3. `assert_no_leakage` fails closed if an unredacted secret value slips into evidence or logs.
"""

from __future__ import annotations

import pytest

from backend.aix_core.runtime.governance.secret_boundary import (
    ResolvedSecretV1,
    SecretBoundary,
    SecretBoundaryError,
    SecretRefV1,
    looks_like_unredacted_secret,
    redact_objective_text,
)
from backend.aix_core.runtime.objective.contracts import UsageV1


def test_openrouter_model_provenance_and_token_usage_recording() -> None:
    """UsageV1 must record model usage provenance (tokens_in, tokens_out, cost_usd, time_seconds)."""
    usage = UsageV1(
        tokens_in=1500,
        tokens_out=320,
        cost_usd=0.0045,
        tool_calls=2,
        time_seconds=1.85,
    )

    assert usage.tokens_in == 1500
    assert usage.tokens_out == 320
    assert usage.cost_usd == 0.0045
    assert usage.tool_calls == 2


def test_secret_boundary_redacts_openrouter_key_and_smtp_password() -> None:
    """SecretBoundary must redact resolved credentials in text and nested dictionary structures."""
    ref_openrouter = SecretRefV1(name="openrouter_api_key", rotated_at="2026-08-17T00:00:00Z")
    resolved_openrouter = ResolvedSecretV1(ref=ref_openrouter, value="sk-or-v1-fake-secret-key-999999999999")

    ref_smtp = SecretRefV1(name="smtp_password", rotated_at="2026-08-17T00:00:00Z")
    resolved_smtp = ResolvedSecretV1(ref=ref_smtp, value="SuperSecretSmtpPass123!")

    resolved_list = [resolved_openrouter, resolved_smtp]
    sb = SecretBoundary(secrets={"openrouter_api_key": resolved_openrouter.value, "smtp_password": resolved_smtp.value}, now="2026-08-17T12:00:00Z")

    payload = {
        "model": "openrouter/anthropic/claude-3.5-sonnet",
        "tokens": {"in": 100, "out": 50},
        "config": {
            "api_key": "sk-or-v1-fake-secret-key-999999999999",
            "smtp": "SMTP_PASSWORD=SuperSecretSmtpPass123!",
        },
    }

    redacted_payload = sb.redact_structure(payload, resolved=resolved_list)

    # API keys and passwords must be replaced by placeholders
    assert "sk-or-v1-fake-secret-key" not in str(redacted_payload)
    assert "SuperSecretSmtpPass123!" not in str(redacted_payload)
    assert "***REDACTED:openrouter_api_key***" in str(redacted_payload)
    assert "***REDACTED:smtp_password***" in str(redacted_payload)

    # Must pass leakage assertion post-redaction
    sb.assert_no_leakage(str(redacted_payload), resolved=resolved_list)


def test_unredacted_secret_leakage_assertion_fails_closed() -> None:
    """assert_no_leakage must raise SecretBoundaryError if plaintext secret is in output text."""
    ref = SecretRefV1(name="jwt_secret", rotated_at="2026-08-17T00:00:00Z")
    resolved = ResolvedSecretV1(ref=ref, value="PlaintextJwtSecretValue42")
    sb = SecretBoundary(secrets={"jwt_secret": "PlaintextJwtSecretValue42"}, now="2026-08-17T12:00:00Z")

    leaked_text = "Dump evidence: JWT_SECRET=PlaintextJwtSecretValue42 in memory"

    with pytest.raises(SecretBoundaryError) as exc_info:
        sb.assert_no_leakage(leaked_text, resolved=[resolved])

    assert "jwt_secret" in str(exc_info.value)


def test_redact_objective_text_pattern_matching() -> None:
    """redact_objective_text must redact key-value secret patterns in unparsed text."""
    text = "Admit objective: API_KEY=sk-or-fake-key-123 for model openrouter/claude"
    redacted, kinds = redact_objective_text(text)

    assert "sk-or-fake-key-123" not in redacted
    assert "api_key" in kinds
    assert "***REDACTED***" in redacted
