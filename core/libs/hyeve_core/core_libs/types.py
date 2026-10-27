"""
Shared dataclasses and enums for Hyeve core libraries.

These lightweight types are safe to import in generator code, FastAPI services,
and validation/error libraries.  They intentionally avoid pulling in heavy
dependencies so that interface modules remain importable in any environment.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class PackLayer(Enum):
    """Ordered layering used when resolving packs."""

    GLOBAL = "global"
    PRODUCT = "product"
    INDUSTRY = "industry"
    CLIENT = "client"
    TENANT = "tenant"
    ENVIRONMENT = "environment"
    LOCALE = "locale"


@dataclass(frozen=True)
class PackContext:
    """
    Context used to resolve a pack bundle.

    service, entity, and version identify the logical model.  The remaining
    fields determine the layered overrides that must be applied before
    validation, error resolution, or workflow execution.
    """

    service: str
    entity: str
    version: str
    tenant_id: str | None = None
    client_id: str | None = None
    industry: str | None = None
    environment: str | None = None
    locale: str | None = None
    actor_id: str | None = None
    correlation_id: str | None = None


@dataclass(frozen=True)
class PackSource:
    """Metadata describing a resolved layer."""

    layer: PackLayer
    source: str  # Filesystem path, repo URI, etc.
    version: str | None = None
    hash: str | None = None


@dataclass
class PackBundle:
    """
    Materialised pack contents for a specific context.

    The bundle is the single artefact consumed by downstream libraries.  It
    carries JSON schema information, validation rules, messages, workflows, and
    auxiliary metadata such as HTTP hints and catalog versioning.
    """

    schema: dict[str, Any]
    validation_rules: dict[str, Any]
    messages: dict[str, dict[str, Any]]
    workflows: dict[str, Any] = field(default_factory=dict)
    http_hints: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    version: str | None = None
    sources: Sequence[PackSource] = field(default_factory=tuple)


class ValidationSeverity(Enum):
    """Optional severity marker for validation violations."""

    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass(frozen=True)
class Violation:
    """
    Validation rule violation reported by the validator.

    - `code` is a stable identifier (e.g., ``VAL.REQUIRED``).
    - `path` is the dotted field path (e.g., ``"user.email"``).
    - `params` carry dynamic data for message templating.
    """

    code: str
    path: str
    params: dict[str, Any] = field(default_factory=dict)
    severity: ValidationSeverity = ValidationSeverity.ERROR


@dataclass(frozen=True)
class ResolvedError:
    """
    Result of resolving an error code via the catalog.

    `message` should be ready to render to the caller, while `http_status_hint`
    communicates the recommended HTTP status for transport layers.
    """

    code: str
    message: str
    http_status_hint: int
    locale: str
    params: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ValidationResult:
    """
    Combined validation outcome.

    `violations` contains the raw rule failures.  `resolved_errors` should hold
    the localisation results produced by the error catalog.  Some callers may
    choose to populate only the raw violations and perform error resolution
    later in the request pipeline.
    """

    is_valid: bool
    violations: tuple[Violation, ...] = field(default_factory=tuple)
    resolved_errors: tuple[ResolvedError, ...] = field(default_factory=tuple)

    @staticmethod
    def success() -> ValidationResult:
        return ValidationResult(is_valid=True)

    @staticmethod
    def failure(
        violations: Sequence[Violation],
        resolved_errors: Sequence[ResolvedError] = (),
    ) -> ValidationResult:
        return ValidationResult(
            is_valid=False,
            violations=tuple(violations),
            resolved_errors=tuple(resolved_errors),
        )
