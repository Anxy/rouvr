"""
Abstract interfaces for Hyeve core libraries.

Concrete packages (packs, validation, errors) must implement these contracts.
Generators and services can rely on them for type checking without importing
heavy implementations or invoking I/O at import time.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable
from typing import Protocol, runtime_checkable

from .types import (
    PackBundle,
    PackContext,
    ResolvedError,
    ValidationResult,
    Violation,
)


class PackResolutionError(RuntimeError):
    """Raised when pack resolution fails."""


class ValidationLibraryError(RuntimeError):
    """Raised when the validation library encounters a fatal error."""


class ErrorCatalogLookupError(RuntimeError):
    """Raised when an error code cannot be resolved."""


class PackResolver(ABC):
    """Interface for resolving pack bundles."""

    @abstractmethod
    def get(self, context: PackContext) -> PackBundle:
        """
        Resolve and return a pack bundle for the given context.

        Implementations must apply the layered merge semantics defined in
        ``hyeve_meta`` and may cache results internally.
        """

    @abstractmethod
    def warm_cache(self, contexts: Iterable[PackContext]) -> None:
        """Prime caches for the supplied contexts (optional optimisation)."""


class ValidationService(ABC):
    """Interface for the validation engine."""

    @abstractmethod
    def validate(
        self,
        payload: dict,
        bundle: PackBundle,
        *,
        locale: str | None = None,
    ) -> ValidationResult:
        """
        Validate payload against the supplied bundle.

        Implementations must perform schema-level, field-level, and
        business-rule validation.  They should not raise on user errors—those
        must be reported via ``ValidationResult``.
        """

    @abstractmethod
    def resolve_violations(
        self,
        violations: Iterable[Violation],
        bundle: PackBundle,
        *,
        locale: str | None = None,
    ) -> ValidationResult:
        """
        Convenience method to translate violations into resolved errors.

        Implementations may delegate to ``ErrorCatalog``.  The default contract
        allows services to resolve messages separately from validation.
        """


@runtime_checkable
class ErrorCatalog(Protocol):
    """Protocol for resolving error codes into presentation-ready messages."""

    def resolve(
        self,
        code: str,
        *,
        params: dict | None = None,
        bundle: PackBundle | None = None,
        locale: str | None = None,
    ) -> ResolvedError:
        """
        Resolve an error code to a concrete message and HTTP hint.

        ``bundle`` is optional for global lookup but should be provided when a
        context-specific override is expected.
        """


__all__ = [
    "PackResolver",
    "ValidationService",
    "ErrorCatalog",
    "PackResolutionError",
    "ValidationLibraryError",
    "ErrorCatalogLookupError",
]
