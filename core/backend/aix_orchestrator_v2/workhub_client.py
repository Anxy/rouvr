"""Backward-compatibility shim for WorkHubTaskClient.

Sprint F8-P3: Canonical client consolidated into backend.aix_core.framework.integration.workhub.client.
This shim re-exports all symbols to preserve legacy caller compatibility.
"""

from backend.aix_core.framework.integration.workhub.client import (
    WorkHubTaskClient,
    _as_dict,
    _as_items,
    _tenant_from_service_token,
    workhub_task_client,
)

__all__ = [
    "WorkHubTaskClient",
    "_as_dict",
    "_as_items",
    "_tenant_from_service_token",
    "workhub_task_client",
]
