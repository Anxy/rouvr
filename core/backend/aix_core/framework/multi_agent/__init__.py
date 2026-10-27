"""AIX Framework Multi-Agent Runtime."""

from backend.aix_core.framework.multi_agent.conflict import ConflictDetector, ScopeCollision
from backend.aix_core.framework.multi_agent.delegation import (
    DelegationAuthorityError,
    DelegationManager,
)
from backend.aix_core.framework.multi_agent.handoff import (
    HandoffAuthorizationError,
    MultiAgentHandoffProtocol,
)
from backend.aix_core.framework.multi_agent.models import (
    ActionObservationV1,
    MultiAgentHandoffRequest,
    MultiAgentHandoffResult,
    StepContextCapsuleV1,
)
from backend.aix_core.framework.multi_agent.protocol import MultiAgentProtocol, MultiAgentSession
from backend.aix_core.framework.multi_agent.sync import (
    ScopeLock,
    ScopeLockConflictError,
    ScopeLockManager,
)

__all__ = [
    "ActionObservationV1",
    "ConflictDetector",
    "DelegationAuthorityError",
    "DelegationManager",
    "HandoffAuthorizationError",
    "MultiAgentHandoffProtocol",
    "MultiAgentHandoffRequest",
    "MultiAgentHandoffResult",
    "MultiAgentProtocol",
    "MultiAgentSession",
    "ScopeCollision",
    "ScopeLock",
    "ScopeLockConflictError",
    "ScopeLockManager",
    "StepContextCapsuleV1",
]
