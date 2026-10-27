"""Canonical execution-domain views for the existing AIX runtime.

This package contains models and lossless adapters only.  It deliberately does
not expose an executor, scheduler, workflow engine, or mutation entry point.
"""

from backend.aix_core.framework.execution.adapters import (
    from_attempt_journal,
    from_objective_envelope,
    from_plan_revision,
    from_step_contract,
)
from backend.aix_core.framework.execution.models import (
    Attempt,
    AttemptJournalStatus,
    AttemptOutcome,
    ExecutionLane,
    ExecutionObjectiveRef,
    ExecutionPlan,
    PlanStatus,
    Step,
    StepStatus,
)
from backend.aix_core.framework.execution.muse_execute_adapter import (
    ActivationBindingLookup,
    MuseCapabilityPolicy,
    MuseExecuteAdapter,
    validate_capability_policy,
)
from backend.aix_core.framework.execution.muse_session import (
    APPROVAL_MODES,
    MuseSessionError,
    MuseSessionPort,
    MuseToolFailure,
    MuseTurnParams,
    MuseTurnResult,
    StdioMspSessionPort,
    is_muse_model_id,
    normalize_model_id,
    uuid7,
    write_evidence_file,
)
from backend.aix_core.framework.execution.muse_workspace_snapshot import (
    FileChange,
    SnapshotEntry,
    SnapshotIncomplete,
    WorkspaceSnapshot,
    changes_to_jsonable,
    diff_snapshots,
    snapshot_workspace,
)
from backend.aix_core.framework.execution.port import ExecuteStepRequest, ExecutionPort

__all__ = [
    "APPROVAL_MODES",
    "ActivationBindingLookup",
    "Attempt",
    "AttemptJournalStatus",
    "AttemptOutcome",
    "ExecutionLane",
    "ExecutionObjectiveRef",
    "ExecutionPlan",
    "ExecutionPort",
    "ExecuteStepRequest",
    "FileChange",
    "MuseCapabilityPolicy",
    "MuseExecuteAdapter",
    "MuseSessionError",
    "MuseSessionPort",
    "MuseToolFailure",
    "MuseTurnParams",
    "MuseTurnResult",
    "PlanStatus",
    "SnapshotEntry",
    "SnapshotIncomplete",
    "StdioMspSessionPort",
    "Step",
    "StepStatus",
    "WorkspaceSnapshot",
    "changes_to_jsonable",
    "diff_snapshots",
    "from_attempt_journal",
    "from_objective_envelope",
    "from_plan_revision",
    "from_step_contract",
    "is_muse_model_id",
    "normalize_model_id",
    "snapshot_workspace",
    "uuid7",
    "validate_capability_policy",
    "write_evidence_file",
]
