"""AIX Framework Scale — long-running WorkGraph persistence & multi-lane dispatch."""

from backend.aix_core.framework.scale.batch_scheduler import (
    BatchScheduler,
    DispatchBatch,
    ExecutionLanePool,
    select_next_step_via_scale,
)
from backend.aix_core.framework.scale.failure_domain import FailureDomain, FailureDomainManager
from backend.aix_core.framework.scale.hibernation import (
    CycleHibernationManager,
    CycleHibernationRecord,
    WakeupTrigger,
)
from backend.aix_core.framework.scale.runtime_bridge import (
    ScaleProjection,
    open_scale_session,
    project_work_graph_from_plan,
    scale_enabled,
)
from backend.aix_core.framework.scale.schema import (
    ensure_aix_work_schema,
    ensure_sqlite_work_schema,
)
from backend.aix_core.framework.scale.slo import ScaleSloReport
from backend.aix_core.framework.scale.work_graph_store import (
    DurableWorkGraphStore,
    InMemoryWorkGraphStore,
    StoredWorkUnitRecord,
    WorkGraphSnapshot,
    WorkGraphStore,
)

__all__ = [
    "BatchScheduler",
    "CycleHibernationManager",
    "CycleHibernationRecord",
    "DispatchBatch",
    "DurableWorkGraphStore",
    "ExecutionLanePool",
    "FailureDomain",
    "FailureDomainManager",
    "InMemoryWorkGraphStore",
    "ScaleProjection",
    "ScaleSloReport",
    "StoredWorkUnitRecord",
    "WakeupTrigger",
    "WorkGraphSnapshot",
    "WorkGraphStore",
    "ensure_aix_work_schema",
    "ensure_sqlite_work_schema",
    "open_scale_session",
    "project_work_graph_from_plan",
    "scale_enabled",
    "select_next_step_via_scale",
]
