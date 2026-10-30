"""rouvr/errors.py — closed error taxonomy + retry mapping (PROPOSAL, mirrors doc C-00)."""
from __future__ import annotations

from datetime import timedelta
from enum import Enum

from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError


class Code(str, Enum):
    THROTTLE = "E_THROTTLE"
    DOWNSTREAM = "E_DOWNSTREAM"
    TRANSPORT = "E_TRANSPORT"
    EVIDENCE = "E_EVIDENCE"
    MOVED = "E_MOVED"
    CONTRACT = "E_CONTRACT"
    CONTRACT_MOVED = "E_CONTRACT_MOVED"
    AUTHZ = "E_AUTHZ"
    SELF_REVIEW = "E_SELF_REVIEW"
    SCOPE = "E_SCOPE"
    INELIGIBLE = "E_INELIGIBLE"
    BAD_REQUEST = "E_BAD_REQUEST"
    CANCELLED = "E_CANCELLED"


RETRYABLE = {Code.THROTTLE, Code.DOWNSTREAM, Code.TRANSPORT}
NON_RETRYABLE = [c.value for c in Code if c not in RETRYABLE]


class RouvrError(Exception):
    def __init__(self, code: Code, message: str, *, details: dict | None = None) -> None:
        super().__init__(f"{code.value}: {message}")
        self.code, self.message, self.details = code, message, details or {}

    @property
    def retryable(self) -> bool:
        return self.code in RETRYABLE

    def to_temporal(self) -> ApplicationError:
        return ApplicationError(self.message, self.details, type=self.code.value,
                                non_retryable=not self.retryable)


def from_http(status: int, body: dict | None) -> RouvrError:
    """Map a WorkHub HTTP response to a Rouvr code. Unknown 4xx => BAD_REQUEST (non-retryable)."""
    body = body or {}
    wh = str(body.get("code") or body.get("error") or "")
    wh_u = wh.upper()
    msg = str(body.get("message") or wh or f"http {status}")
    if status == 429:
        return RouvrError(Code.THROTTLE, msg)
    if status >= 500:
        return RouvrError(Code.DOWNSTREAM, msg)
    if status in (401, 403):
        return RouvrError(Code.AUTHZ, msg)
    if status == 409:
        if "CONTRACT" in wh_u:
            return RouvrError(Code.CONTRACT_MOVED, msg)
        return RouvrError(Code.MOVED, msg)  # record_version / lease epoch CAS lost
    if status == 422 and "LEASE_" in wh_u:
        return RouvrError(Code.MOVED, msg)  # fencing lost: expired/held/conflict
    if status == 422 and "EVIDENCE" in wh_u:
        return RouvrError(Code.EVIDENCE, msg)
    if status == 422 and "SELF" in wh_u:
        return RouvrError(Code.SELF_REVIEW, msg)
    if status == 400 and wh_u == "KEY.REQUIRED":
        return RouvrError(Code.CONTRACT, "Idempotency-Key missing (client bug)")
    return RouvrError(Code.BAD_REQUEST, msg, details={"status": status, "workhub_code": wh})


def retry_policy_for(kind: str) -> RetryPolicy:
    """kind: 'workhub' | 'muse' | 'test' | 'verify'."""
    max_attempts = {"workhub": 8, "muse": 3, "test": 3, "verify": 3}[kind]
    return RetryPolicy(initial_interval=timedelta(seconds=2), backoff_coefficient=2.0,
                       maximum_interval=timedelta(seconds=60), maximum_attempts=max_attempts,
                       non_retryable_error_types=NON_RETRYABLE)
