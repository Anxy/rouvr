from __future__ import annotations

import pytest

from backend.services.redaction_worker_service import apply_redaction_worker


def test_redaction_worker_masks_sensitive_patterns() -> None:
    payload = {
        "email": "test.user@example.com",
        "phone": "+1 212-555-1234",
        "nested": {"ssn": "123-45-6789", "api_key": "sk-secretkey1234567890"},
    }
    result = apply_redaction_worker(payload=payload, sensitive_route=True)
    assert result.redaction_applied is True
    assert result.masked_count >= 4
    assert "[REDACTED_EMAIL]" in result.redacted_payload["email"]
    assert "[REDACTED_PHONE]" in result.redacted_payload["phone"]
    assert "[REDACTED_SSN]" in result.redacted_payload["nested"]["ssn"]


def test_redaction_worker_skips_non_sensitive_route() -> None:
    payload = {"message": "hello"}
    result = apply_redaction_worker(payload=payload, sensitive_route=False)
    assert result.redaction_applied is False
    assert result.redacted_payload == payload


def test_redaction_worker_failure_path_defined() -> None:
    with pytest.raises(ValueError) as exc:
        apply_redaction_worker(payload={"simulate_redaction_failure": True}, sensitive_route=True)
    assert "REDACTION_WORKER_FAILURE_SIMULATED" in str(exc.value)
