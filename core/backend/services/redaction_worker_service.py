"""Deterministic redaction worker for sensitive payload paths."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
PHONE_RE = re.compile(r"\b(?:\+?1[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?){2}\d{4}\b")
SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
API_KEY_RE = re.compile(r"\b(?:sk|pk|api)[-_][A-Za-z0-9]{16,}\b", re.IGNORECASE)


@dataclass
class RedactionResult:
    redacted_payload: dict[str, Any]
    masked_count: int
    redaction_applied: bool
    sensitive_route: bool
    patterns_masked: list[str]

    def to_report(self) -> dict[str, Any]:
        return {
            "redaction_applied": self.redaction_applied,
            "sensitive_route": self.sensitive_route,
            "masked_count": self.masked_count,
            "patterns_masked": self.patterns_masked,
        }


def _mask_text(value: str) -> tuple[str, int, list[str]]:
    count = 0
    patterns: list[str] = []
    replaced = value
    for label, regex in (
        ("email", EMAIL_RE),
        ("phone", PHONE_RE),
        ("ssn", SSN_RE),
        ("api_key", API_KEY_RE),
    ):
        replaced, changes = regex.subn(f"[REDACTED_{label.upper()}]", replaced)
        if changes:
            count += changes
            patterns.append(label)
    return replaced, count, patterns


def _walk(value: Any) -> tuple[Any, int, list[str]]:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        total = 0
        patterns: list[str] = []
        for key, item in value.items():
            redacted, count, pats = _walk(item)
            out[str(key)] = redacted
            total += count
            patterns.extend(pats)
        return out, total, patterns
    if isinstance(value, list):
        out_list: list[Any] = []
        total = 0
        patterns: list[str] = []
        for item in value:
            redacted, count, pats = _walk(item)
            out_list.append(redacted)
            total += count
            patterns.extend(pats)
        return out_list, total, patterns
    if isinstance(value, str):
        masked, count, pats = _mask_text(value)
        return masked, count, pats
    return value, 0, []


def apply_redaction_worker(
    *,
    payload: dict[str, Any],
    sensitive_route: bool,
) -> RedactionResult:
    if not sensitive_route:
        return RedactionResult(
            redacted_payload=payload,
            masked_count=0,
            redaction_applied=False,
            sensitive_route=False,
            patterns_masked=[],
        )
    if payload.get("simulate_redaction_failure"):
        raise ValueError("REDACTION_WORKER_FAILURE_SIMULATED")
    redacted, count, patterns = _walk(payload)
    return RedactionResult(
        redacted_payload=redacted if isinstance(redacted, dict) else payload,
        masked_count=count,
        redaction_applied=True,
        sensitive_route=True,
        patterns_masked=sorted(list(dict.fromkeys(patterns))),
    )
