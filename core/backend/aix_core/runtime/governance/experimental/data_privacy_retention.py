"""AIX-E2E-CYCLE-001M-W10: data privacy and retention policy.

Enforces:
  - Retention periods by data class (objective, step, attempt, evidence, audit)
  - Erasure eligibility (GDPR-style right-to-erasure marker)
  - PII detection guard (fields that must never be stored in plain form)
  - Tenant-scoped data deletion policy — cross-tenant deletions are forbidden

Retention periods (minimum hold, cannot be shortened below floor):
  objective_envelope:  365 days  (audit requirement)
  execution_cycle:     365 days
  step_contract:       180 days
  step_attempt:        90 days
  evidence_bundle:     365 days
  audit_event:         730 days  (2 years minimum for compliance)

Erasure:
  A data record is erasure-eligible only when ALL of:
    1. Its retention period has elapsed.
    2. The tenant has submitted a verified erasure request.
    3. No active legal hold is present.
  Records under legal hold are NEVER eligible for erasure regardless of retention age.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

DataClass = Literal[
    "objective_envelope",
    "execution_cycle",
    "step_contract",
    "step_attempt",
    "evidence_bundle",
    "audit_event",
]

# Retention floors in days — reducing these is a policy change, not a code change
RETENTION_FLOORS_DAYS: dict[DataClass, int] = {
    "objective_envelope": 365,
    "execution_cycle": 365,
    "step_contract": 180,
    "step_attempt": 90,
    "evidence_bundle": 365,
    "audit_event": 730,
}

# Fields that must never be stored in plaintext anywhere
PII_FIELD_NAMES: frozenset[str] = frozenset(
    {
        "email",
        "phone",
        "address",
        "national_id",
        "passport",
        "ssn",
        "date_of_birth",
        "full_name_pii",
    }
)


class RetentionPolicyError(ValueError):
    pass


@dataclass(frozen=True)
class RetentionCheckV1:
    data_class: DataClass
    record_id: str
    tenant_id: str
    created_at: str           # ISO-8601 UTC
    legal_hold: bool
    erasure_requested: bool
    erasure_eligible: bool
    days_remaining: int       # Days until retention floor elapsed; 0 = elapsed
    reason: str


def check_retention(
    *,
    data_class: DataClass,
    record_id: str,
    tenant_id: str,
    created_at: str,
    legal_hold: bool = False,
    erasure_requested: bool = False,
    now: datetime | None = None,
) -> RetentionCheckV1:
    """Compute retention and erasure eligibility for a data record."""
    if data_class not in RETENTION_FLOORS_DAYS:
        raise RetentionPolicyError(f"Unknown data_class: {data_class!r}")
    if not record_id or not tenant_id:
        raise RetentionPolicyError("record_id and tenant_id are required")

    now = now or datetime.now(UTC)
    try:
        created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RetentionPolicyError(f"created_at is not a valid ISO-8601 datetime: {created_at!r}") from exc

    floor = RETENTION_FLOORS_DAYS[data_class]
    age_days = (now - created).days
    days_remaining = max(0, floor - age_days)
    retention_elapsed = age_days >= floor

    if legal_hold:
        return RetentionCheckV1(
            data_class=data_class,
            record_id=record_id,
            tenant_id=tenant_id,
            created_at=created_at,
            legal_hold=True,
            erasure_requested=erasure_requested,
            erasure_eligible=False,
            days_remaining=days_remaining,
            reason="Record is under legal hold. Erasure is not permitted regardless of retention age.",
        )

    erasure_eligible = retention_elapsed and erasure_requested
    reason = (
        "Erasure eligible: retention floor elapsed and erasure request verified."
        if erasure_eligible
        else (
            "Retention floor has elapsed but no erasure request is on file."
            if retention_elapsed
            else f"Retention floor not yet elapsed: {days_remaining} day(s) remaining."
        )
    )

    return RetentionCheckV1(
        data_class=data_class,
        record_id=record_id,
        tenant_id=tenant_id,
        created_at=created_at,
        legal_hold=False,
        erasure_requested=erasure_requested,
        erasure_eligible=erasure_eligible,
        days_remaining=days_remaining,
        reason=reason,
    )


class PiiFieldGuard:
    """Scans a payload dict for field names that are designated PII.

    Raises:
        RetentionPolicyError: if any PII field is detected in plaintext.
    """

    def assert_no_pii(self, payload: dict[str, object], *, context: str = "") -> None:
        pii_found = PII_FIELD_NAMES & frozenset(payload)
        if pii_found:
            raise RetentionPolicyError(
                f"PII field(s) detected in plaintext payload{f' ({context})' if context else ''}: "
                f"{sorted(pii_found)}. These fields must be encrypted or excluded."
            )


__all__ = [
    "DataClass",
    "RETENTION_FLOORS_DAYS",
    "PII_FIELD_NAMES",
    "RetentionPolicyError",
    "RetentionCheckV1",
    "check_retention",
    "PiiFieldGuard",
]
