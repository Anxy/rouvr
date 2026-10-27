"""CodeIntel v2 package (Wave 4, advisory-only).

Lead-merged: Agent1 registry/schemas + Agent2 index jobs + Agent3
snapshots/freshness + Agent4 citations/file_meta.

NOTE (known duplication, deliberate): Agent4's citations.py carries local
stub Protocols named CodeIntelSnapshot/Freshness for parallel-build
isolation. Package-level canonical exports are Agent3's snapshots/freshness;
Agent4's stubs stay module-scoped and are NOT re-exported here. Unify in a
follow-up once Agent4's client binds the real store.
"""

from backend.codeintel_v2.citations import (
    Citation,
    citation_to_dict,
)
from backend.codeintel_v2.file_meta import FileMeta, SymbolMeta
from backend.codeintel_v2.freshness import (
    check_freshness,
)
from backend.codeintel_v2.index_jobs import (
    DEFAULT_ERROR_CLASSES,
    ILLEGAL_TRANSITION,
    INDEX_FAILED,
    INDEXING_DISABLED,
    INVALID_COMMIT,
    INVALID_REPO,
    REPO_CHECK_FAILED,
    CodeIntelIndexJob,
    IllegalTransitionError,
    JobRecord,
    JobStatus,
    create_job,
    fail,
    get_job_record,
    indexing_enabled,
    reset_store,
    transition,
)
from backend.codeintel_v2.registry import (
    DuplicateRepoError,
    reset_codeintel_registry,
)
from backend.codeintel_v2.schemas import (
    CodeIntelSnapshot as SchemaSnapshot,
    Freshness,
    RegisterRepoRequest,
    RegisterRepoResponse,
    evaluate_freshness,
)
from backend.codeintel_v2.snapshots import (
    CodeIntelSnapshot,
    SnapshotStore,
    codeintel_posture,
)

__all__ = [
    "DEFAULT_ERROR_CLASSES",
    "ILLEGAL_TRANSITION",
    "INDEX_FAILED",
    "INDEXING_DISABLED",
    "INVALID_COMMIT",
    "INVALID_REPO",
    "REPO_CHECK_FAILED",
    "CodeIntelIndexJob",
    "CodeIntelSnapshot",
    "Citation",
    "DuplicateRepoError",
    "Freshness",
    "IllegalTransitionError",
    "JobRecord",
    "JobStatus",
    "RegisterRepoRequest",
    "RegisterRepoResponse",
    "SchemaSnapshot",
    "SnapshotStore",
    "FileMeta",
    "SymbolMeta",
    "check_freshness",
    "citation_to_dict",
    "codeintel_posture",
    "create_job",
    "evaluate_freshness",
    "fail",
    "get_job_record",
    "indexing_enabled",
    "reset_store",
    "reset_codeintel_registry",
    "transition",
]
