"""AIX Framework WorkHub Integration."""

from backend.aix_core.framework.integration.workhub.adapter import (
    TaskCloseoutWriter,
    TaskContextReader,
    TaskEvidenceWriter,
    WorkHubTaskContext,
)
from backend.aix_core.framework.integration.workhub.boundary import (
    WorkHubAuthority,
    WorkHubAuthorityDecision,
    WorkHubBoundary,
    WorkHubOperation,
)
from backend.aix_core.framework.integration.workhub.client import (
    WorkHubAuthorityDenied,
    WorkHubTaskClient,
    workhub_task_client,
)
from backend.aix_core.framework.integration.workhub.dispatcher import (
    DispatchReceipt,
    NoOpOutboxDispatcher,
    OutboxDispatcher,
    OutboxEvent,
    WorkHubOutboxDispatcher,
)

__all__ = [
    "DispatchReceipt",
    "NoOpOutboxDispatcher",
    "OutboxDispatcher",
    "OutboxEvent",
    "TaskCloseoutWriter",
    "TaskContextReader",
    "TaskEvidenceWriter",
    "WorkHubAuthority",
    "WorkHubAuthorityDecision",
    "WorkHubAuthorityDenied",
    "WorkHubBoundary",
    "WorkHubOperation",
    "WorkHubOutboxDispatcher",
    "WorkHubTaskClient",
    "WorkHubTaskContext",
    "workhub_task_client",
]
