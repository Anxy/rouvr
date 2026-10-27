"""CodeIntel v2 SQLAlchemy models (Wave 4 Agent1 — Models + Repo Registration).

Advisory-only system: registration metadata only. Retrieval posture is owned by
the Wave 0 authority freeze flag ``AIX_CODEINTEL`` (off|advisory|on, default off)
in ``backend.hyeve_aix_service.config`` — this module never changes it.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

CODEINTEL_REPOS_TABLE = "codeintel_repos"


class CodeIntelBase(DeclarativeBase):
    """Declarative base for CodeIntel v2 persistence models."""


class CodeIntelRepo(CodeIntelBase):
    """A tenant-scoped repository registration for CodeIntel indexing."""

    __tablename__ = CODEINTEL_REPOS_TABLE
    __table_args__ = (
        UniqueConstraint("tenant_id", "repo_url", name="uq_codeintel_repos_tenant_url"),
    )

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    project_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    repo_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    registered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )


__all__ = ["CODEINTEL_REPOS_TABLE", "CodeIntelBase", "CodeIntelRepo"]
