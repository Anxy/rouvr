"""Credentialed owner-side WorkHub assignment/lease preflight for Muse spawn.

Lane-ops #23 M3: immediately before the credential-free Muse adapter turns
a bound request into a session turn, the owner re-reads canonical WorkHub
assignment/lease state and compares the complete authorized tuple
available in the accepted activation/binding.

Boundary: credentials/JWTs and raw WorkHub responses never leave this
module. Callers hand in the accepted binding dict and get back only the
verified non-secret tuple (or a refusal). The adapter sends the
store-fresh record binding — never caller input — and binds the returned
proof to the exact request before spawn.

Freshness is per effect: every call performs live reads and nothing is
cached, so a moved assignment cannot ride a previous success. There is no
proof artifact to replay and no background scheduler involved.
"""

from __future__ import annotations

import math
import time
from datetime import UTC, datetime
from typing import Any, Protocol

from backend.session_factory.workhub_binding import (
    BindingRefused,
    BindingTransport,
    verify_binding,
)


class MusePreflightVerifier(Protocol):
    """Owner-side preflight handle consumed by the credential-free adapter."""

    async def verify_assignment_lease(
        self, binding: dict[str, Any]
    ) -> dict[str, Any]:
        """Re-read live owner state and return the verified non-secret tuple.

        Raises the maintained ``BindingRefused`` (stable BINDING_* code)
        when live state is missing or disagrees, or the transport's own
        error when the owner read is unavailable.
        """
        ...


# Route-digest liveness: the maintained compare pins authority/plan
# digests and contract movement; the route digest is additionally
# compared whenever the owner assignment read carries it.
_ROUTE_DIGEST_KEYS = ("route_digest",)

# Lease-expiry liveness (fail closed): expiry is a required acceptance
# field, but none of the maintained assignment-binding reads expose an
# expiry timestamp today (sibling PG release leases do; the supported
# WorkHub assignment read does not). Until the deployed owner API carries
# authoritative lease expiry, its absence — like a malformed value —
# refuses as unproven rather than silently passing. Deployment/API
# dependency: the WorkHub assignment-binding response (or its maintained
# transport mapping) must surface the authoritative lease-expiry
# timestamp under the documented key below.
_LEASE_EXPIRY_KEY = "lease_expires_at"

def _as_epoch_seconds(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        candidate = float(value)
        # NaN/Infinity are not timestamps: NaN comparisons are always
        # False, so a NaN expiry would silently pass the liveness check.
        return candidate if math.isfinite(candidate) else None
    text = str(value).strip()
    if not text:
        return None
    try:
        candidate = float(text)
    except ValueError:
        pass
    else:
        # Textual NaN/Infinity ("nan", "+inf", ...) parse as floats but
        # prove no deadline; refuse them exactly like a missing value.
        return candidate if math.isfinite(candidate) else None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.timestamp()


def _refuse(code: str) -> None:
    raise BindingRefused(code)


class _OwnerReadCapture:
    """Pass-through transport wrapper capturing one coherent owner read.

    The maintained proof performs its own assignment-binding read; this
    wrapper records exactly that payload so the route/expiry liveness
    below observes the SAME snapshot instead of a second independent
    read. No second authority client, no caching across effects — the
    wrapper is per-call and holds no credentials.
    """

    def __init__(self, inner: BindingTransport) -> None:
        self._inner = inner
        self.captured_assignment: dict[str, Any] | None = None

    async def get_task_by_number(
        self, *, project_id: str, task_number: int
    ) -> dict[str, Any] | None:
        return await self._inner.get_task_by_number(
            project_id=project_id, task_number=task_number
        )

    async def get_assignment_binding(
        self, *, project_id: str, task_number: int
    ) -> dict[str, Any] | None:
        payload = await self._inner.get_assignment_binding(
            project_id=project_id, task_number=task_number
        )
        self.captured_assignment = payload
        return payload

    async def list_task_evidence(
        self, *, project_id: str, task_id: str
    ) -> list[dict[str, Any]]:
        return await self._inner.list_task_evidence(
            project_id=project_id, task_id=task_id
        )


async def verify_muse_assignment_lease(
    binding: dict[str, Any], *, transport: BindingTransport
) -> dict[str, Any]:
    """Full maintained tuple compare plus liveness extras. Fail closed."""
    # Raises BindingRefused unless every binding field is live-equal.
    capture = _OwnerReadCapture(transport)
    await verify_binding(binding, transport=capture)  # type: ignore[arg-type]
    live = capture.captured_assignment
    if not isinstance(live, dict) or not live:
        _refuse("BINDING_ASSIGNMENT_MISSING")
    assert isinstance(live, dict)
    for key in _ROUTE_DIGEST_KEYS:
        carried = live.get(key)
        if carried is None:
            continue
        if str(carried) != str(binding.get(key) or ""):
            _refuse("BINDING_ROUTE_FIELD_MISMATCH")
    raw_expiry = live.get(_LEASE_EXPIRY_KEY)
    expires_at = _as_epoch_seconds(raw_expiry)
    if expires_at is None:
        _refuse("BINDING_LEASE_EXPIRY_UNPROVEN")
    assert expires_at is not None
    if expires_at <= time.time():
        _refuse("BINDING_LEASE_EXPIRED")
    # verify_binding proved every binding field live-equal above, so the
    # proof echoes the binding values for the adapter to bind to the
    # exact request/record.
    return {
        "assignment_id": str(binding["assignment_id"]),
        "assignment_revision": int(binding["assignment_revision"]),
        "workhub_attempt_id": str(binding["workhub_attempt_id"]),
        "lease_epoch": int(binding["lease_epoch"]),
        "lease_holder": str(binding["lease_holder"]),
        "executor_sub": str(binding["executor_sub"]),
    }


class WorkHubPreflightVerifier:
    """Production preflight over the maintained binding transport.

    The transport holds the credentials; this object only routes the
    accepted binding through live verification. One live read set per
    call — no caching, so every effect re-proves freshness.
    """

    def __init__(self, transport: BindingTransport) -> None:
        if transport is None:
            raise ValueError("MUSE_PREFLIGHT_TRANSPORT_MISSING")
        self._transport = transport

    async def verify_assignment_lease(
        self, binding: dict[str, Any]
    ) -> dict[str, Any]:
        if not isinstance(binding, dict) or not binding:
            _refuse("BINDING_MISSING")
        return await verify_muse_assignment_lease(
            dict(binding), transport=self._transport
        )
