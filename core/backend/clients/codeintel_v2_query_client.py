"""Strict typed async CodeIntel v2 query client (Agent 4, Wave 4).

Advisory-only lane: results are candidates. This module never selects a
file or symbol for the caller, never widens results to nearby files, and
never fills in missing fields by guessing. Malformed items are skipped;
an empty result set is returned as-is.

Posture is honored from ``settings.AIX_CODEINTEL`` (imported, never
redeclared) unless an explicit per-instance ``posture`` override is given.
``AIX_CODEINTEL=off`` performs zero HTTP calls: the disabled marker is
returned before any I/O happens.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

import httpx

from backend.codeintel_v2.citations import (
    Citation,
    citation_to_dict,
    coerce_freshness,
)
from backend.hyeve_aix_service.config import settings

POSTURE_OFF = "off"
POSTURE_ADVISORY = "advisory"

ERROR_DISABLED = "CODEINTEL_V2_DISABLED"
ERROR_POSTURE_REJECTED = "CODEINTEL_V2_POSTURE_REJECTED"
ERROR_TOKEN_MISSING = "CODEINTEL_V2_TOKEN_MISSING"
ERROR_NO_BASE_URL = "CODEINTEL_V2_NO_BASE_URL"
ERROR_TIMEOUT = "CODEINTEL_V2_TIMEOUT"
ERROR_CONNECTION = "CODEINTEL_V2_CONNECTION"
ERROR_PAYLOAD_SHAPE = "CODEINTEL_V2_PAYLOAD_SHAPE"

QUERY_PATH = "/api/v1/query"


def http_status_error(status_code: int) -> str:
    """Classify an HTTP failure status without leaking response bodies."""
    return f"CODEINTEL_V2_HTTP_{int(status_code)}"


class CitationSet(list["Citation"]):
    """A candidate citation list carrying advisory response markers.

    A plain ``list`` subclass, so ``(citations, error)`` callers keep list
    semantics, while ``advisory_degraded`` / ``posture`` and
    ``to_evidence()`` expose the advisory response envelope.
    """

    def __init__(
        self,
        iterable: Any = (),
        *,
        advisory_degraded: bool = False,
        posture: str = POSTURE_ADVISORY,
    ) -> None:
        super().__init__(iterable)
        self.advisory_degraded: bool = bool(advisory_degraded)
        self.posture: str = posture

    def to_evidence(self) -> dict[str, Any]:
        return {
            "posture": self.posture,
            "candidate_only": True,
            "advisory_degraded": self.advisory_degraded,
            "citation_count": len(self),
            "citations": [citation_to_dict(item) for item in self],
        }


def _empty(posture: str, *, advisory_degraded: bool = False) -> CitationSet:
    return CitationSet((), advisory_degraded=advisory_degraded, posture=posture)


def _payload_is_stale(payload: Mapping[str, Any]) -> bool:
    freshness = payload.get("freshness")
    if isinstance(freshness, Mapping):
        return str(freshness.get("freshness") or "").strip().lower() == "stale"
    return False


def _build_citation(item: Any) -> Citation | None:
    """Build one ``Citation`` from a payload item; ``None`` when unusable.

    Items that fail schema validation are skipped — never repaired.
    """
    if not isinstance(item, Mapping):
        return None
    try:
        line_range = item.get("line_range")
        if line_range is None and (
            item.get("start_line") is not None or item.get("end_line") is not None
        ):
            end_line = item.get("end_line", item.get("start_line"))
            line_range = [item.get("start_line"), end_line]
        return Citation(
            path=item["path"] if "path" in item else item.get("file_path"),
            line_range=line_range,  # type: ignore[arg-type]
            snapshot_id=item.get("snapshot_id"),
            commit_sha=item.get("commit_sha"),
            confidence=item["confidence"],
            source=str(item.get("source") or "codeintel_v2"),
            freshness=coerce_freshness(item.get("freshness")),
        )
    except (ValueError, TypeError, KeyError, AttributeError):
        return None


@dataclass(slots=True)
class CodeIntelV2QueryClient:
    """Strict typed async client for CodeIntel v2 candidate queries."""

    base_url: str | None = None
    token: str | None = None
    timeout_seconds: float | None = None
    posture: str | None = None
    transport: httpx.AsyncBaseTransport | None = field(default=None, repr=False)

    def resolve_posture(self) -> str:
        if self.posture is not None:
            return str(self.posture or "").strip().lower()
        return str(getattr(settings, "AIX_CODEINTEL", "off") or "off").strip().lower()

    def resolve_base_url(self) -> str:
        raw = self.base_url
        if raw is None:
            raw = str(getattr(settings, "CODEINTEL_API_URL", "") or "")
        return str(raw or "").strip().rstrip("/")

    def resolve_token(self) -> str | None:
        if self.token is not None:
            return str(self.token or "") or None
        configured = getattr(settings, "CODEINTEL_API_TOKEN", None)
        return str(configured or "") or None

    def resolve_timeout(self) -> float:
        if self.timeout_seconds is not None:
            return float(self.timeout_seconds)
        return float(getattr(settings, "CODEINTEL_API_TIMEOUT_SECONDS", 10) or 10)

    async def query(
        self, entity: str | Mapping[str, Any], *, limit: int = 5
    ) -> tuple[CitationSet, str | None]:
        """Query candidate citations for ``entity``.

        Returns ``(citations, error)`` where ``error`` is a short classified
        code (never a raw traceback). ``citations`` is a ``CitationSet``
        whose ``advisory_degraded`` flag and ``to_evidence()`` envelope mark
        stale results as degraded.
        """
        posture = self.resolve_posture()
        if posture == POSTURE_OFF:
            return _empty(POSTURE_OFF), ERROR_DISABLED
        if posture != POSTURE_ADVISORY:
            # Wave 0 authority freeze: only "off" and "advisory" may run.
            return _empty(posture), ERROR_POSTURE_REJECTED

        token = self.resolve_token()
        if not token:
            return _empty(posture), ERROR_TOKEN_MISSING
        base_url = self.resolve_base_url()
        if not base_url:
            return _empty(posture), ERROR_NO_BASE_URL

        body: dict[str, Any] = {
            "entity": dict(entity) if isinstance(entity, Mapping) else entity,
            "limit": max(1, min(int(limit), 50)),
        }
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
        }
        try:
            async with httpx.AsyncClient(
                timeout=self.resolve_timeout(), transport=self.transport
            ) as http:
                response = await http.post(
                    f"{base_url}{QUERY_PATH}", json=body, headers=headers
                )
        except httpx.TimeoutException:
            return _empty(posture), ERROR_TIMEOUT
        except httpx.TransportError:
            return _empty(posture), ERROR_CONNECTION
        except httpx.RequestError:
            return _empty(posture), ERROR_CONNECTION

        if response.status_code >= 400:
            return _empty(posture), http_status_error(response.status_code)

        try:
            payload = response.json()
        except ValueError:
            return _empty(posture), ERROR_PAYLOAD_SHAPE
        if not isinstance(payload, Mapping):
            return _empty(posture), ERROR_PAYLOAD_SHAPE
        items = payload.get("citations")
        if not isinstance(items, list):
            return _empty(posture), ERROR_PAYLOAD_SHAPE

        kept: list[Citation] = []
        for item in items:
            citation = _build_citation(item)
            if citation is not None:
                kept.append(citation)
        degraded = _payload_is_stale(payload) or any(
            citation.is_stale for citation in kept
        )
        result = CitationSet(kept, advisory_degraded=degraded, posture=posture)
        return result, None


__all__ = [
    "ERROR_CONNECTION",
    "ERROR_DISABLED",
    "ERROR_NO_BASE_URL",
    "ERROR_PAYLOAD_SHAPE",
    "ERROR_POSTURE_REJECTED",
    "ERROR_TIMEOUT",
    "ERROR_TOKEN_MISSING",
    "CitationSet",
    "CodeIntelV2QueryClient",
    "http_status_error",
]
