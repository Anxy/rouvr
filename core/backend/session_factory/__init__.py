"""Session Factory skeleton (Wave 15 Phase 2, PR bundle — NOT mounted).

Not imported by ``backend.main``. Do not mount without Lead approval.
No schema change: sessions reuse the existing ``aix_runs`` table;
ephemeral state lives in ``aix-redis`` only.
"""

from backend.session_factory.capsule import (
    CAPSULE_TTL_SECONDS,
    RECENT_LIST_MAX,
    build_capsule,
    build_run_record,
    capsule_key,
    recent_list_key,
)
from backend.session_factory.bundle import build_task_bundle, workspace_path
from backend.session_factory.chain import (
    build_attempt_record,
    validate_chain,
)
from backend.session_factory.guards import (
    OwnershipLost,
    ResourceEscape,
    assert_ownership,
    canonical_resource,
    guarded_write,
    should_retry,
)
from backend.session_factory.locks import (
    acquire_lock,
    check_owner,
    ready_units,
    release_lock,
)
from backend.session_factory.resolve import (
    configure_supported_models,
    reset_supported_models,
    resolve_assignment,
)
from backend.session_factory.events import EventLog
from backend.session_factory.knowledge import (
    capsule_to_artifact,
    knowledge_status,
    tenant_check,
)
from backend.session_factory.ops import (
    attempt_summary,
    queue_snapshot,
    worker_snapshot,
)
from backend.session_factory.personas import can_act
from backend.session_factory.retention import (
    compact_capsule,
    tier_for_age,
)
from backend.session_factory.schedule import admit, gate_status
from backend.session_factory.verdict import record_verdict
from backend.session_factory.stores import (
    RunStore,
    configure_redis,
    configure_run_store,
)

__all__ = [
    "CAPSULE_TTL_SECONDS",
    "RECENT_LIST_MAX",
    "RunStore",
    "acquire_lock",
    "build_attempt_record",
    "build_capsule",
    "build_run_record",
    "build_task_bundle",
    "capsule_key",
    "check_owner",
    "configure_redis",
    "configure_run_store",
    "OwnershipLost",
    "ResourceEscape",
    "assert_ownership",
    "canonical_resource",
    "admit",
    "capsule_to_artifact",
    "EventLog",
    "can_act",
    "attempt_summary",
    "compact_capsule",
    "configure_supported_models",
    "gate_status",
    "guarded_write",
    "knowledge_status",
    "queue_snapshot",
    "ready_units",
    "recent_list_key",
    "record_verdict",
    "tenant_check",
    "tier_for_age",
    "worker_snapshot",
    "release_lock",
    "reset_supported_models",
    "should_retry",
    "resolve_assignment",
    "validate_chain",
    "workspace_path",
]
