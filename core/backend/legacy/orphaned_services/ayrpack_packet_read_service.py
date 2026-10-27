from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.clients.ayrpack_client import ayrpack_client


@dataclass
class AyrpackReadOutcome:
    data: dict[str, Any] | None
    source_used: str
    mode: str
    ayrpack_error: str | None


def _is_valid_scope(*, tenant_id: str, project_id: str, packet_id: str) -> bool:
    return bool(str(tenant_id).strip() and str(project_id).strip() and str(packet_id).strip())


async def read_packet(
    *,
    tenant_id: str,
    project_id: str,
    correlation_id: str,
    packet_id: str,
) -> AyrpackReadOutcome:
    if not _is_valid_scope(tenant_id=tenant_id, project_id=project_id, packet_id=packet_id):
        return AyrpackReadOutcome(
            data=None,
            source_used="none",
            mode="invalid_scope",
            ayrpack_error="INVALID_SCOPE",
        )

    ayrpack_data, ayrpack_error = await ayrpack_client.get_packet(
        tenant_id=tenant_id,
        project_id=project_id,
        correlation_id=correlation_id,
        packet_id=packet_id,
    )
    if ayrpack_data is None:
        return AyrpackReadOutcome(
            data=None,
            source_used="none",
            mode="canonical",
            ayrpack_error=ayrpack_error,
        )

    return AyrpackReadOutcome(
        data=ayrpack_data,
        source_used="ayrpack",
        mode="canonical",
        ayrpack_error=None,
    )


__all__ = ["AyrpackReadOutcome", "read_packet"]
