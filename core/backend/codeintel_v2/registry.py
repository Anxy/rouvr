"""CodeIntel v2 repo registry (Wave 4 Agent1).

SQLite-backed store over the ``CodeIntelRepo`` SQLAlchemy model. Tenant+URL
uniqueness is enforced both by an application-level check and by the
``uq_codeintel_repos_tenant_url`` unique constraint (fail closed on races).
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime

from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.models.codeintel_v2_models import CodeIntelBase, CodeIntelRepo

CODEINTEL_V2_DATABASE_URL_ENV = "CODEINTEL_V2_DATABASE_URL"


class DuplicateRepoError(Exception):
    """Raised when tenant_id + repo_url is already registered."""

    def __init__(self, tenant_id: str, repo_url: str) -> None:
        super().__init__(f"repo already registered for tenant '{tenant_id}': {repo_url}")
        self.tenant_id = tenant_id
        self.repo_url = repo_url


_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def _database_url() -> str:
    return (os.environ.get(CODEINTEL_V2_DATABASE_URL_ENV) or "").strip() or "sqlite://"


def get_engine() -> Engine:
    """Return the shared registry engine (in-memory SQLite by default)."""
    global _engine, _session_factory
    if _engine is None:
        url = _database_url()
        if url == "sqlite://":
            _engine = create_engine(
                "sqlite://",
                connect_args={"check_same_thread": False},
                poolclass=StaticPool,
            )
        else:
            _engine = create_engine(url)
        CodeIntelBase.metadata.create_all(_engine)
        _session_factory = sessionmaker(bind=_engine, expire_on_commit=False)
    assert _session_factory is not None
    return _engine


def _sessions() -> sessionmaker[Session]:
    get_engine()
    assert _session_factory is not None
    return _session_factory


def reset_codeintel_registry() -> None:
    """Drop and recreate registry tables (test isolation helper)."""
    engine = get_engine()
    CodeIntelBase.metadata.drop_all(engine)
    CodeIntelBase.metadata.create_all(engine)


def make_repo_id(tenant_id: str) -> str:
    """Tenant-scoped repo id: globally unique, prefixed by tenant."""
    return f"{tenant_id}:{uuid.uuid4().hex[:12]}"


def register_repo(*, tenant_id: str, project_id: str | None, repo_url: str) -> CodeIntelRepo:
    """Register a repo; raise DuplicateRepoError on tenant+url conflict."""
    tenant_id = tenant_id.strip()
    repo_url = repo_url.strip()
    if not tenant_id or not repo_url:
        raise ValueError("tenant_id and repo_url are required")
    factory = _sessions()
    with factory() as session:
        existing = session.execute(
            select(CodeIntelRepo).where(
                CodeIntelRepo.tenant_id == tenant_id,
                CodeIntelRepo.repo_url == repo_url,
            )
        ).scalar_one_or_none()
        if existing is not None:
            raise DuplicateRepoError(tenant_id, repo_url)
        row = CodeIntelRepo(
            id=make_repo_id(tenant_id),
            tenant_id=tenant_id,
            project_id=(project_id.strip() if project_id and project_id.strip() else None),
            repo_url=repo_url,
            registered_at=datetime.now(UTC),
        )
        session.add(row)
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            raise DuplicateRepoError(tenant_id, repo_url) from exc
        session.refresh(row)
        return row


def get_repo(*, repo_id: str, tenant_id: str) -> CodeIntelRepo | None:
    """Fetch one repo scoped to its tenant (None when absent or foreign tenant)."""
    factory = _sessions()
    with factory() as session:
        return session.execute(
            select(CodeIntelRepo).where(
                CodeIntelRepo.id == repo_id,
                CodeIntelRepo.tenant_id == tenant_id,
            )
        ).scalar_one_or_none()


def repo_id_exists(*, repo_id: str) -> bool:
    """Unscoped existence check for the index-job wiring point.

    Never raises: blank ids and store failures read as absent (fail closed).
    Tenant scoping stays at the router, which pairs this with :func:`get_repo`.
    """
    if not repo_id or not repo_id.strip():
        return False
    try:
        factory = _sessions()
        with factory() as session:
            return (
                session.execute(
                    select(CodeIntelRepo.id).where(CodeIntelRepo.id == repo_id)
                ).scalar_one_or_none()
                is not None
            )
    except Exception:
        return False


def count_repos(*, tenant_id: str | None = None) -> int:
    """Count registered repos, optionally filtered by tenant."""
    from sqlalchemy import func

    factory = _sessions()
    with factory() as session:
        stmt = select(func.count()).select_from(CodeIntelRepo)
        if tenant_id is not None:
            stmt = stmt.where(CodeIntelRepo.tenant_id == tenant_id)
        return int(session.execute(stmt).scalar_one())


__all__ = [
    "DuplicateRepoError",
    "count_repos",
    "get_engine",
    "get_repo",
    "make_repo_id",
    "register_repo",
    "repo_id_exists",
    "reset_codeintel_registry",
]
