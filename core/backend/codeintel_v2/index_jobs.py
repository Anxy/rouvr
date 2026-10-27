"""Index-job model and lifecycle for the CodeIntel v2 advisory lane.

Scope (Wave 4 Agent2): job model (pydantic), creation bound to a commit_sha,
a strict queued -> running -> done|failed transition table, and classified
failures. Invalid repo_id values become classified failures, never exceptions.

Frozen spec: ``CodeIntelIndexJob`` carries exactly four fields —
``repo_id``, ``commit_sha``, ``status``, ``error_class``. Transition history
lives on the companion :class:`JobRecord` (job_id + ordered transitions),
never on the model itself.

Repo existence is an injectable callable (``repo_exists(repo_id) -> bool``).
The default stub is fail-closed (unknown repos are treated as absent) and is
documented for the merge lead, who wires the real Agent1 registry check.
This module never imports Agent1 code.

The ``AIX_CODEINTEL`` flag (``backend.hyeve_aix_service.config``) is imported
lazily and honored: :func:`indexing_enabled` is True only in ``advisory``
posture. ``off`` means no indexing work is performed. (``on`` is rejected at
startup validation by the config model itself.)
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from typing import Callable, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Status + classified error vocabulary
# ---------------------------------------------------------------------------

JobStatus = Literal["queued", "running", "done", "failed"]

STATUS_VALUES: tuple[str, str, str, str] = ("queued", "running", "done", "failed")

# Legal edges only: queued -> running -> done | failed.
_ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "queued": frozenset({"running"}),
    "running": frozenset({"done", "failed"}),
    "done": frozenset(),
    "failed": frozenset(),
}

# Classified failure codes (error_class values). Small closed vocabulary so
# failures are machine-readable; never free-form exception text.
INVALID_REPO = "INVALID_REPO"
INVALID_COMMIT = "INVALID_COMMIT"
ILLEGAL_TRANSITION = "ILLEGAL_TRANSITION"
REPO_CHECK_FAILED = "REPO_CHECK_FAILED"
INDEXING_DISABLED = "INDEXING_DISABLED"
INDEX_FAILED = "INDEX_FAILED"

DEFAULT_ERROR_CLASSES: tuple[str, ...] = (
    INVALID_REPO,
    INVALID_COMMIT,
    ILLEGAL_TRANSITION,
    REPO_CHECK_FAILED,
    INDEXING_DISABLED,
    INDEX_FAILED,
)


class IllegalTransitionError(ValueError):
    """Raised for illegal lifecycle transitions. Classified, not a leak."""

    def __init__(self, job_status: str, to_status: str) -> None:
        super().__init__(
            f"{ILLEGAL_TRANSITION}: cannot transition from "
            f"{job_status!r} to {to_status!r}"
        )
        self.code = ILLEGAL_TRANSITION
        self.job_status = job_status
        self.to_status = to_status


class CodeIntelIndexJob(BaseModel):
    """Frozen spec: exactly these four fields (extra fields forbidden)."""

    model_config = ConfigDict(extra="forbid")

    repo_id: str = Field(description="Repository id the index job is bound to.")
    commit_sha: str = Field(description="Exact commit the index job is bound to.")
    status: JobStatus = Field(description="Lifecycle state: queued|running|done|failed.")
    error_class: str | None = Field(
        default=None,
        description="Classified failure code when status is failed, else None.",
    )


@dataclass
class JobRecord:
    """Stored envelope: stable id + job + ordered transition history."""

    job_id: str
    job: CodeIntelIndexJob
    transitions: list[str] = field(default_factory=list)


class RepoExists(Protocol):
    """Injectable repo-existence check. The merge lead wires the real one."""

    def __call__(self, repo_id: str) -> bool: ...


RepoExistsFn = Callable[[str], bool]


def _default_repo_exists(repo_id: str) -> bool:
    """Fail-closed stub: without the wired registry, no repo is known.

    Documented for the merge lead: replace via ``create_job(..., repo_exists=...)``
    or :func:`configure_repo_exists`. Agent1's registry check plugs in here.
    """
    _ = repo_id
    return False


_repo_exists: RepoExistsFn = _default_repo_exists

_store_lock = threading.Lock()
_JOBS: dict[str, JobRecord] = {}


def configure_repo_exists(fn: RepoExistsFn) -> None:
    """Install the real repo-existence check (merge lead wiring point)."""
    global _repo_exists
    _repo_exists = fn


def reset_store() -> None:
    """Clear the in-memory job store (tests / ephemeral use)."""
    with _store_lock:
        _JOBS.clear()


def _codeintel_posture() -> str:
    """Read AIX_CODEINTEL lazily (mirrors retrieval_policy_service pattern)."""
    try:
        from backend.hyeve_aix_service.config import settings as _settings

        return str(getattr(_settings, "AIX_CODEINTEL", "off") or "off").strip().lower()
    except Exception:
        return "off"


def indexing_enabled() -> bool:
    """True only in advisory posture: the only posture that performs work."""
    return _codeintel_posture() == "advisory"


def _new_record(job: CodeIntelIndexJob, first_state: str) -> JobRecord:
    record = JobRecord(
        job_id=f"idx-{uuid.uuid4().hex[:12]}",
        job=job,
        transitions=[first_state],
    )
    with _store_lock:
        _JOBS[record.job_id] = record
    return record


def _check_repo(repo_id: str, repo_exists: RepoExistsFn | None) -> str | None:
    """Return an error_class for a bad repo_id, or None when the repo is OK.

    Never raises: blank ids, unknown repos, and checker crashes all map to
    classified codes.
    """
    if not repo_id or not repo_id.strip():
        return INVALID_REPO
    checker = repo_exists if repo_exists is not None else _repo_exists
    try:
        known = checker(repo_id)
    except Exception:
        return REPO_CHECK_FAILED
    if not known:
        return INVALID_REPO
    return None


def _check_commit(commit_sha: str) -> str | None:
    """Return an error_class for a bad commit_sha, or None when OK."""
    if not commit_sha or not commit_sha.strip():
        return INVALID_COMMIT
    return None


def create_job(
    repo_id: str,
    commit_sha: str,
    *,
    repo_exists: RepoExistsFn | None = None,
) -> JobRecord:
    """Create an index job bound to (repo_id, commit_sha); initial state queued.

    Bad inputs (blank/unknown repo, blank commit) do not raise: they yield a
    stored record whose job is failed with a classified ``error_class`` and
    transitions ``["queued", "failed"]``.
    """
    repo_id = repo_id or ""
    commit_sha = commit_sha or ""
    error = _check_commit(commit_sha) or _check_repo(repo_id, repo_exists)
    if error is not None:
        job = CodeIntelIndexJob(
            repo_id=repo_id, commit_sha=commit_sha, status="failed", error_class=error
        )
        record = _new_record(job, "queued")
        # Created failed: it was queued, then classified as failed on intake.
        record.transitions.append("failed")
        return record
    job = CodeIntelIndexJob(
        repo_id=repo_id, commit_sha=commit_sha, status="queued", error_class=None
    )
    return _new_record(job, "queued")


def transition(
    job: CodeIntelIndexJob,
    to: str,
    *,
    history: list[str] | None = None,
) -> CodeIntelIndexJob:
    """Move ``job`` to ``to`` along the only legal chain.

    Legal edges: queued -> running, running -> done, running -> failed.
    Anything else (including moves out of terminal done/failed and unknown
    targets) raises :class:`IllegalTransitionError`, which carries the
    ``ILLEGAL_TRANSITION`` classification. On success the new status is
    appended to ``history`` when a list is supplied.
    """
    allowed = _ALLOWED_TRANSITIONS.get(job.status, frozenset())
    if to not in STATUS_VALUES or to not in allowed:
        raise IllegalTransitionError(job.status, to)
    job.status = to  # type: ignore[assignment]
    if to != "failed":
        job.error_class = None
    if history is not None:
        history.append(to)
    return job


def fail(
    job: CodeIntelIndexJob,
    error_class: str,
    *,
    history: list[str] | None = None,
) -> CodeIntelIndexJob:
    """Record a classified failure: move a non-terminal job to failed.

    ``error_class`` must be a non-blank code (use the module vocabulary).
    Failing an already-terminal job raises :class:`IllegalTransitionError`.
    """
    if not error_class or not error_class.strip():
        raise ValueError("error_class must be a non-blank classified code")
    if job.status not in ("queued", "running"):
        raise IllegalTransitionError(job.status, "failed")
    job.status = "failed"
    job.error_class = error_class
    if history is not None:
        history.append("failed")
    return job


def get_job_record(job_id: str) -> JobRecord | None:
    """Fetch a stored record by id (None when unknown)."""
    with _store_lock:
        return _JOBS.get(job_id)
