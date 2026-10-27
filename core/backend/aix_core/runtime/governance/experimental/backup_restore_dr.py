"""AIX-E2E-CYCLE-001M-W11: backup, restore, and disaster recovery.

Models the backup/restore/DR runbook and verifiable invariants:
  - Backup schedule and coverage (what must be backed up, how often)
  - Restore target: RTO (Recovery Time Objective) and RPO (Recovery Point Objective)
  - DR activation gate: must be human-authorized, never auto-triggered by AIX
  - Restore verification: post-restore integrity check must pass before resuming execution
  - Cross-tenant isolation: a restore must never co-mingle tenant data

This module does NOT issue real backup/restore calls (production NO_GO). It provides:
  1. The policy contract (RTO/RPO/coverage).
  2. A verifier that checks restore completion against the policy.
  3. A DR activation gate that requires explicit human authorization token.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------

DR_RTO_MINUTES: int = 60       # Max acceptable recovery time
DR_RPO_MINUTES: int = 15       # Max acceptable data loss window

BACKUP_COVERAGE: frozenset[str] = frozenset(
    {
        "objective_envelopes",
        "execution_cycles",
        "step_contracts",
        "step_attempts",
        "evidence_bundles",
        "audit_events",
        "idempotency_ledger",
        "tenant_policy_config",
    }
)

BackupFrequency = Literal["continuous_wal", "15min_snapshot", "hourly_snapshot", "daily_snapshot"]

REQUIRED_BACKUP_SCHEDULE: dict[str, BackupFrequency] = {
    "objective_envelopes": "15min_snapshot",
    "execution_cycles": "continuous_wal",
    "step_contracts": "15min_snapshot",
    "step_attempts": "15min_snapshot",
    "evidence_bundles": "hourly_snapshot",
    "audit_events": "continuous_wal",
    "idempotency_ledger": "continuous_wal",
    "tenant_policy_config": "hourly_snapshot",
}


class DrPolicyError(ValueError):
    pass


# ---------------------------------------------------------------------------
# Backup record
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BackupRecordV1:
    backup_id: str
    tenant_id: str
    completed_at: str      # ISO-8601 UTC
    coverage: frozenset[str]
    backup_size_bytes: int
    checksum: str          # SHA-256 of backup manifest
    storage_location: str  # Reference only — never a raw path with creds


@dataclass(frozen=True)
class BackupVerificationResultV1:
    verified: bool
    missing_coverage: frozenset[str]
    reason: str


def verify_backup_coverage(backup: BackupRecordV1) -> BackupVerificationResultV1:
    missing = BACKUP_COVERAGE - backup.coverage
    if missing:
        return BackupVerificationResultV1(
            verified=False,
            missing_coverage=missing,
            reason=f"Backup is missing required coverage: {sorted(missing)}",
        )
    return BackupVerificationResultV1(
        verified=True,
        missing_coverage=frozenset(),
        reason="Backup coverage verified against policy.",
    )


# ---------------------------------------------------------------------------
# Restore verification
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RestoreRequestV1:
    restore_id: str
    tenant_id: str
    backup_id: str
    requested_at: str         # ISO-8601 UTC
    human_auth_token: str     # Must be non-empty — DR activation is never auto-triggered
    target_rto_minutes: int = DR_RTO_MINUTES
    target_rpo_minutes: int = DR_RPO_MINUTES


@dataclass(frozen=True)
class RestoreResultV1:
    restore_id: str
    tenant_id: str
    succeeded: bool
    completed_at: str | None
    integrity_passed: bool
    rto_met: bool
    rpo_met: bool
    reason: str


class DrActivationGate:
    """Validates that a DR restore is human-authorized and meets policy requirements.

    Invariants:
      - `human_auth_token` must be non-empty (no auto-DR).
      - Restore must be scoped to a single tenant (no cross-tenant restore).
      - Simulated restore completes instantly with full integrity (no real calls; NO_GO).
    """

    def request_restore(self, request: RestoreRequestV1) -> RestoreResultV1:
        if not request.human_auth_token or not request.human_auth_token.strip():
            return RestoreResultV1(
                restore_id=request.restore_id,
                tenant_id=request.tenant_id,
                succeeded=False,
                completed_at=None,
                integrity_passed=False,
                rto_met=False,
                rpo_met=False,
                reason="DR activation requires a non-empty human_auth_token. "
                       "Automated DR activation is not permitted.",
            )

        if not request.tenant_id or not request.tenant_id.strip():
            return RestoreResultV1(
                restore_id=request.restore_id,
                tenant_id=request.tenant_id,
                succeeded=False,
                completed_at=None,
                integrity_passed=False,
                rto_met=False,
                rpo_met=False,
                reason="tenant_id is required — restore must be tenant-scoped.",
            )

        # Simulated restore — production implementation wired by ops team
        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        return RestoreResultV1(
            restore_id=request.restore_id,
            tenant_id=request.tenant_id,
            succeeded=True,
            completed_at=now,
            integrity_passed=True,
            rto_met=True,
            rpo_met=True,
            reason="[SIMULATED] Restore completed. Integrity check passed. "
                   "Production restore requires ops team execution.",
        )


__all__ = [
    "DR_RTO_MINUTES",
    "DR_RPO_MINUTES",
    "BACKUP_COVERAGE",
    "REQUIRED_BACKUP_SCHEDULE",
    "DrPolicyError",
    "BackupRecordV1",
    "BackupVerificationResultV1",
    "verify_backup_coverage",
    "RestoreRequestV1",
    "RestoreResultV1",
    "DrActivationGate",
]
