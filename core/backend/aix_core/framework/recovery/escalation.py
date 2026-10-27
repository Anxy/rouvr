"""AIX Framework Escalation Hook & Port.

Sprint: F6 Recovery Framework (F6-P4)
Grounded in: admin escalation policies, WorkHub note/update boundaries.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol


def _utc() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _uuid_str() -> str:
    return str(uuid.uuid4())


@dataclass(frozen=True)
class EscalationRequest:
    """Strongly-typed operator escalation request triggered on terminal BLOCKED states."""

    tenant_id: str
    objective_id: str
    cycle_id: str
    reason_code: str
    severity: str = "CRITICAL"
    step_id: str | None = None
    evidence_refs: tuple[str, ...] = ()
    command_id: str = ""
    requested_at: str = field(default_factory=_utc)

    def idempotency_key(self) -> str:
        ident = self.command_id or self.reason_code
        raw = f"{self.tenant_id}|{self.objective_id}|{self.cycle_id}|{self.step_id}|{self.reason_code}|{ident}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "objective_id": self.objective_id,
            "cycle_id": self.cycle_id,
            "step_id": self.step_id,
            "reason_code": self.reason_code,
            "severity": self.severity,
            "evidence_refs": list(self.evidence_refs),
            "command_id": self.command_id,
            "idempotency_key": self.idempotency_key(),
            "requested_at": self.requested_at,
        }


@dataclass(frozen=True)
class EscalationReceipt:
    """Proof of operator notification delivery."""

    receipt_id: str
    escalation_id: str
    status: str = "DELIVERED"
    delivered_at: str = field(default_factory=_utc)
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "receipt_id": self.receipt_id,
            "escalation_id": self.escalation_id,
            "status": self.status,
            "delivered_at": self.delivered_at,
            "details": self.details,
        }


class EscalationHook(Protocol):
    """Abstract protocol for delivering operator escalations."""

    async def notify_operator(self, request: EscalationRequest) -> EscalationReceipt: ...


class NoOpEscalationHook:
    """Inert escalation hook (default when framework flag is disabled)."""

    async def notify_operator(self, request: EscalationRequest) -> EscalationReceipt:
        return EscalationReceipt(
            receipt_id=_uuid_str(),
            escalation_id=request.idempotency_key(),
            status="NOOP_IGNORED",
            details={"message": "Escalation hook disabled; event recorded in-process only."},
        )


class AixEscalationPolicyAdapter:
    """Canonical adapter integrating with backend escalation policy service and WorkHub.

    Fail-closed: never reports DELIVERED unless a configured escalation service
    successfully acknowledges the notification.
    """

    def __init__(self, escalation_service: Any = None) -> None:
        self._service = escalation_service
        self._delivered_receipts: dict[str, EscalationReceipt] = {}

    async def notify_operator(self, request: EscalationRequest) -> EscalationReceipt:
        key = request.idempotency_key()

        if key in self._delivered_receipts:
            return self._delivered_receipts[key]

        if self._service is None:
            return EscalationReceipt(
                receipt_id=_uuid_str(),
                escalation_id=key,
                status="FAILED_NO_SERVICE",
                details={
                    "tenant_id": request.tenant_id,
                    "objective_id": request.objective_id,
                    "cycle_id": request.cycle_id,
                    "reason_code": request.reason_code,
                    "error": "escalation_service not configured",
                },
            )

        deliver = getattr(self._service, "notify_operator", None) or getattr(
            self._service, "escalate", None
        )
        if deliver is None or not callable(deliver):
            return EscalationReceipt(
                receipt_id=_uuid_str(),
                escalation_id=key,
                status="FAILED_NO_SERVICE",
                details={"error": "escalation_service missing notify_operator/escalate"},
            )

        try:
            raw = deliver(request)
            if hasattr(raw, "__await__"):
                raw = await raw  # type: ignore[misc]
        except Exception as exc:  # noqa: BLE001 - fail closed on delivery errors
            return EscalationReceipt(
                receipt_id=_uuid_str(),
                escalation_id=key,
                status="FAILED_DELIVERY",
                details={"error": str(exc)[:500]},
            )

        ok = False
        details: dict[str, Any] = {
            "tenant_id": request.tenant_id,
            "objective_id": request.objective_id,
            "cycle_id": request.cycle_id,
            "reason_code": request.reason_code,
            "severity": request.severity,
        }
        if isinstance(raw, EscalationReceipt):
            if raw.status != "DELIVERED":
                return raw
            receipt = raw
            self._delivered_receipts[key] = receipt
            return receipt
        if isinstance(raw, dict):
            ok = bool(raw.get("ok") or raw.get("delivered") or raw.get("status") == "DELIVERED")
            details["service_response"] = {k: raw[k] for k in list(raw)[:12]}
        elif raw is True:
            ok = True
        elif raw is False or raw is None:
            ok = False
        else:
            ok = bool(getattr(raw, "ok", False) or getattr(raw, "delivered", False))

        status = "DELIVERED" if ok else "FAILED_DELIVERY"
        receipt = EscalationReceipt(
            receipt_id=_uuid_str(),
            escalation_id=key,
            status=status,
            details=details,
        )
        if ok:
            self._delivered_receipts[key] = receipt
        return receipt
