#!/usr/bin/env python3
"""Waker execution-packet assembler: canonical JSON + sha256 digest packets.

Verify-before-use semantics: a consumer must call :func:`verify` (or
:func:`verify_before_use`) and refuse the packet on any failure before
acting on its contents. No network, no secrets, no side effects.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

DIGEST_FIELD = "payload_digest"
SCHEMA = "waker-dispatch-v1"


def canonical_bytes(packet_without_digest: dict) -> bytes:
    """Canonical encoding: keys sorted, separators (',', ':')."""
    return json.dumps(packet_without_digest, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def digest_of(packet_without_digest: dict) -> str:
    """Hex sha256 over the canonical encoding (digest field excluded)."""
    return hashlib.sha256(canonical_bytes(packet_without_digest)).hexdigest()


def assemble(fields: dict) -> dict:
    """Return a copy of `fields` with a fresh `payload_digest` attached.

    Raises ValueError if `fields` is not a dict. Any pre-existing digest
    value is recomputed, never trusted.
    """
    if not isinstance(fields, dict):
        raise ValueError("packet fields must be a dict")
    body = {k: v for k, v in fields.items() if k != DIGEST_FIELD}
    packet = dict(body)
    packet[DIGEST_FIELD] = digest_of(body)
    return packet


def verify(packet: dict) -> tuple[bool, str]:
    """Recompute the digest and compare. Returns (ok, reason)."""
    if not isinstance(packet, dict):
        return False, "not-a-dict"
    claimed = packet.get(DIGEST_FIELD)
    if not isinstance(claimed, str) or not claimed:
        return False, "missing-digest"
    body = {k: v for k, v in packet.items() if k != DIGEST_FIELD}
    try:
        expected = digest_of(body)
    except (TypeError, ValueError):
        return False, "unencodable-body"
    if claimed != expected:
        return False, "digest-mismatch"
    return True, "ok"


def parse_issued_at(value) -> datetime | None:
    """Parse an ISO-8601 `issued_at` value; None when absent/unparseable."""
    if not isinstance(value, str) or not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def verify_before_use(packet: dict, *, expected_binding: dict | None = None,
                      max_age_s: float = 3600,
                      future_skew_s: float = 300,
                      now: datetime | None = None) -> tuple[bool, str]:
    """Full gate: digest validity + freshness + binding.

    - digest must verify via :func:`verify`;
    - `issued_at` must be present, parseable, not older than `max_age_s`,
      and not more than `future_skew_s` in the future;
    - every key in `expected_binding` must equal the packet's value.
    Refuses (False) on any failure: fail closed.
    """
    ok, reason = verify(packet)
    if not ok:
        return False, reason
    issued = parse_issued_at(packet.get("issued_at"))
    if issued is None:
        return False, "missing-issued-at"
    at = now or datetime.now(timezone.utc)
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    age = (at - issued).total_seconds()
    if age < -future_skew_s:
        return False, "issued-in-future"
    if age > max_age_s:
        return False, "stale-packet"
    for key, want in (expected_binding or {}).items():
        if packet.get(key) != want:
            return False, f"binding-mismatch:{key}"
    return True, "ok"


def utcnow_str() -> str:
    """Current UTC time in `issued_at` format."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
