"""Cycle API errors, pagination, filtering, idempotency and concurrency."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime
from typing import Any, Callable, Iterable

from pydantic import BaseModel, ConfigDict, Field

API_QUALITY_VERSION = "aix.objective-cycle.api-quality.v1"


class CycleApiContractError(ValueError):
    def __init__(self, *, http_status: int, code: str, detail: str) -> None:
        self.http_status = http_status
        self.code = code
        self.detail = detail
        super().__init__(code)


class CycleApiError(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    contract_version: str = API_QUALITY_VERSION
    code: str = Field(min_length=1)
    detail: str = Field(min_length=1)
    correlation_id: str = Field(min_length=1)
    retryable: bool = False
    context: dict[str, Any] = Field(default_factory=dict)


class PageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    limit: int = Field(default=50, ge=1, le=100)
    cursor: str | None = None


class PageResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[dict[str, Any], ...]
    next_cursor: str | None
    has_more: bool


def canonical_idempotency_fingerprint(
    *,
    method: str,
    path: str,
    tenant_id: str,
    body: dict[str, Any],
) -> str:
    payload = {
        "method": method.strip().upper(),
        "path": path.strip(),
        "tenant_id": tenant_id.strip(),
        "body": body,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class IdempotencyGuard:
    def __init__(self) -> None:
        self._entries: dict[tuple[str, str], tuple[str, str]] = {}

    def register(
        self,
        *,
        tenant_id: str,
        idempotency_key: str,
        fingerprint: str,
        result_reference: str,
    ) -> tuple[str, bool]:
        tenant = tenant_id.strip()
        key = idempotency_key.strip()
        if not tenant or not key:
            raise CycleApiContractError(
                http_status=400,
                code="IDEMPOTENCY_KEY_REQUIRED",
                detail="Tenant and Idempotency-Key are required for cycle commands.",
            )
        identity = (tenant, key)
        existing = self._entries.get(identity)
        if existing:
            existing_fingerprint, existing_reference = existing
            if existing_fingerprint != fingerprint:
                raise CycleApiContractError(
                    http_status=409,
                    code="IDEMPOTENCY_KEY_PAYLOAD_CONFLICT",
                    detail="The idempotency key is already bound to a different request payload.",
                )
            return existing_reference, True
        self._entries[identity] = (fingerprint, result_reference)
        return result_reference, False


def require_version_precondition(*, if_match: str | None, current_version: int) -> None:
    raw = str(if_match or "").strip()
    if not raw:
        raise CycleApiContractError(
            http_status=428,
            code="VERSION_PRECONDITION_REQUIRED",
            detail="If-Match is required for this cycle command.",
        )
    normalized = raw.removeprefix("W/").strip().strip('"')
    try:
        requested = int(normalized)
    except ValueError as exc:
        raise CycleApiContractError(
            http_status=400,
            code="VERSION_PRECONDITION_INVALID",
            detail="If-Match must contain the expected integer aggregate version.",
        ) from exc
    if requested != current_version:
        raise CycleApiContractError(
            http_status=412,
            code="VERSION_PRECONDITION_FAILED",
            detail="The cycle changed after the client read it.",
        )


def validate_filters(
    filters: dict[str, str | None],
    *,
    allowed_fields: Iterable[str],
) -> dict[str, str]:
    allowed = set(allowed_fields)
    unexpected = set(filters) - allowed
    if unexpected:
        raise CycleApiContractError(
            http_status=400,
            code="FILTER_FIELD_FORBIDDEN",
            detail=f"Unsupported filter fields: {','.join(sorted(unexpected))}",
        )
    return {key: str(value).strip() for key, value in sorted(filters.items()) if value is not None and str(value).strip()}


class TenantCursorCodec:
    def __init__(
        self,
        *,
        secret: bytes,
        max_age_seconds: int = 900,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not secret:
            raise ValueError("cursor secret is required")
        self._secret = bytes(secret)
        self._max_age = max_age_seconds
        self._clock = clock or (lambda: datetime.now(UTC))

    def encode(self, *, tenant_id: str, sort_key: str, last_id: str) -> str:
        payload = {
            "tenant_id": tenant_id,
            "sort_key": sort_key,
            "last_id": last_id,
            "issued_at": int(self._clock().timestamp()),
        }
        encoded_payload = base64.urlsafe_b64encode(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).decode("ascii").rstrip("=")
        signature = hmac.new(self._secret, encoded_payload.encode("ascii"), hashlib.sha256).hexdigest()
        return f"{encoded_payload}.{signature}"

    def decode(self, cursor: str, *, tenant_id: str) -> dict[str, Any]:
        try:
            encoded_payload, signature = cursor.split(".", 1)
            expected = hmac.new(self._secret, encoded_payload.encode("ascii"), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(signature, expected):
                raise ValueError
            padding = "=" * (-len(encoded_payload) % 4)
            payload = json.loads(base64.urlsafe_b64decode(encoded_payload + padding))
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise CycleApiContractError(
                http_status=400,
                code="PAGINATION_CURSOR_INVALID",
                detail="Pagination cursor is malformed or has an invalid signature.",
            ) from exc
        if payload.get("tenant_id") != tenant_id:
            raise CycleApiContractError(
                http_status=403,
                code="PAGINATION_CURSOR_TENANT_MISMATCH",
                detail="Pagination cursor belongs to another tenant.",
            )
        age = int(self._clock().timestamp()) - int(payload.get("issued_at") or 0)
        if age < 0 or age > self._max_age:
            raise CycleApiContractError(
                http_status=400,
                code="PAGINATION_CURSOR_EXPIRED",
                detail="Pagination cursor expired.",
            )
        return payload
