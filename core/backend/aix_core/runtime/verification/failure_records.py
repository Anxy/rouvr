"""AIX-W08-E01-T001: Normalized Failure Records & Signatures.

Normalizes raw execution/verification failures into distinguishable failure domains:
CODE, TEST, ENVIRONMENT, TOOL, POLICY, NETWORK, DEPENDENCY, MERGE, ASSUMPTION.
Computes deterministic failure signatures for deduplication and retry accounting.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

FailureDomain = Literal[
    "CODE",
    "TEST",
    "ENVIRONMENT",
    "TOOL",
    "POLICY",
    "NETWORK",
    "DEPENDENCY",
    "MERGE",
    "ASSUMPTION",
]


class FailureRecordError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class NormalizedFailureRecord:
    failure_id: str  # sha256 failure signature
    domain: FailureDomain
    reason_code: str
    normalized_message: str
    raw_message: str
    occurred_at: str


def classify_failure_domain(reason_code: str, raw_message: str) -> FailureDomain:
    """Classifies raw failure reason and message into exactly one distinguishable FailureDomain."""
    combined = f"{reason_code} {raw_message}".lower()

    if any(k in combined for k in ("connection refused", "http 5", "socket timeout", "network_flake", "econnrefused")):
        return "NETWORK"
    if any(k in combined for k in ("import_error", "modulenotfounderror", "package_conflict", "cannot import name")):
        return "DEPENDENCY"
    if any(k in combined for k in ("merge conflict", "patch apply failure", "patch_rejected", "git conflict")):
        return "MERGE"
    if any(k in combined for k in ("unauthorized", "budget_exceeded", "secret_leakage", "policy_violation", "forbidden")):
        return "POLICY"
    if any(k in combined for k in ("snapshot_stale", "stale_plan", "assumption_violated", "graph_stale")):
        return "ASSUMPTION"
    if any(k in combined for k in ("permission denied", "env_missing", "python version", "no such file or directory")):
        return "ENVIRONMENT"
    if any(k in combined for k in ("command not found", "tool_error", "timeout", "process exit")):
        return "TOOL"
    if any(k in combined for k in ("assertionerror", "test failed", "pytest", "failed test")):
        return "TEST"

    # Default to CODE for syntax, type, and runtime logic errors
    return "CODE"


def normalize_failure(reason_code: str, raw_message: str) -> NormalizedFailureRecord:
    """Normalizes raw failure string and derives deterministic failure signature."""
    domain = classify_failure_domain(reason_code, raw_message)
    cleaned = re.sub(r"\s+", " ", raw_message.strip())[:256]
    sig_payload = f"{domain}:{reason_code}:{cleaned}"
    failure_id = hashlib.sha256(sig_payload.encode("utf-8")).hexdigest()

    return NormalizedFailureRecord(
        failure_id=failure_id,
        domain=domain,
        reason_code=reason_code,
        normalized_message=cleaned,
        raw_message=raw_message,
        occurred_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    )


__all__ = [
    "FailureDomain",
    "FailureRecordError",
    "NormalizedFailureRecord",
    "classify_failure_domain",
    "normalize_failure",
]
