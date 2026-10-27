"""C001: production reachability inventory and disposition classifier.

Authority: AIX_MASTER_ROADMAP_PHASE_EXECUTION.md P0 / Capsule C001.
Source of truth for classification is live production callers, not package presence.
"""

from backend.aix_core.control_plane_reachability.schema import (
    DISPOSITIONS,
    RUNTIME_STATUSES,
    ReachabilityComponent,
    ReachabilityManifest,
)
from backend.aix_core.control_plane_reachability.generator import (
    generate_reachability_manifest,
    validate_manifest,
)

__all__ = [
    "DISPOSITIONS",
    "RUNTIME_STATUSES",
    "ReachabilityComponent",
    "ReachabilityManifest",
    "generate_reachability_manifest",
    "validate_manifest",
]
