"""AIX Model Registry & Role Routing subsystem."""

from backend.aix_core.runtime.model_registry.model_profile import (
    ModelProfileV1,
    ProviderKind,
)
from backend.aix_core.runtime.model_registry.role_policy import (
    AgentRoleKind,
    ComplexityKind,
    RiskKind,
    RoleModelPolicyV1,
)
from backend.aix_core.runtime.model_registry.registry import (
    ModelRegistry,
    ModelRoute,
    get_model_registry,
)
from backend.aix_core.runtime.model_registry.independence import (
    IndependenceViolation,
    ModelIndependenceGuard,
    ModelIndependenceResult,
    SelfVerificationError,
    extract_model_family,
    log_model_provenance_to_ledger,
)

__all__ = [
    "ModelProfileV1",
    "ProviderKind",
    "AgentRoleKind",
    "ComplexityKind",
    "RiskKind",
    "RoleModelPolicyV1",
    "ModelRegistry",
    "ModelRoute",
    "get_model_registry",
    "ModelIndependenceGuard",
    "ModelIndependenceResult",
    "IndependenceViolation",
    "SelfVerificationError",
    "extract_model_family",
    "log_model_provenance_to_ledger",
]
