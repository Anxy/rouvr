"""
Implementation of the validation contract defined in :mod:`hyeve_core.core_libs`.

The validator combines JSON Schema validation with pack-driven rules.  It is
intentionally lightweight; complex business workflows can hook into the
``pre_validate``/``post_validate`` extensions exposed via the pack bundle.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from jsonschema import Draft7Validator  # type: ignore[import-untyped]

from hyeve_core.core_libs.interfaces import ErrorCatalog, ValidationService
from hyeve_core.core_libs.types import (
    PackBundle,
    ResolvedError,
    ValidationResult,
    Violation,
)


class DefaultValidationService(ValidationService):
    """Concrete validation service used by generated Hyeve services."""

    def __init__(
        self,
        *,
        error_catalog: ErrorCatalog | None = None,
        default_locale: str = "en",
    ):
        self._error_catalog = error_catalog
        self._default_locale = default_locale

    # ------------------------------------------------------------------ #
    # ValidationService interface
    # ------------------------------------------------------------------ #
    def validate(
        self,
        payload: dict,
        bundle: PackBundle,
        *,
        locale: str | None = None,
    ) -> ValidationResult:
        violations: list[Violation] = []

        # 1) JSON schema validation
        if bundle.schema:
            validator = Draft7Validator(bundle.schema)
            for error in validator.iter_errors(payload):
                path = ".".join(str(item) for item in error.absolute_path) or "$"
                violations.append(
                    Violation(
                        code=error.schema.get("code", "VAL.SCHEMA_ERROR"),
                        path=path,
                        params={"message": error.message},
                    )
                )

        # 2) Field-level rules from pack metadata
        shared_rules = bundle.validation_rules.get("_shared", {})
        for field, rules in shared_rules.items():
            self._apply_field_rules(
                field,
                rules,
                payload,
                violations,
            )

        # 3) Required fields per operation
        operation = bundle.metadata.get("operation") if bundle.metadata else None
        if not operation:
            operation = "update" if payload.get("id") else "create"
        self._apply_operation_rules(bundle.validation_rules, operation, payload, violations)

        if violations and self._error_catalog:
            resolved = self._resolve_errors(violations, bundle, locale)
            return ValidationResult.failure(violations, resolved)

        if violations:
            return ValidationResult.failure(violations)

        return ValidationResult.success()

    def resolve_violations(
        self,
        violations: Iterable[Violation],
        bundle: PackBundle,
        *,
        locale: str | None = None,
    ) -> ValidationResult:
        violations = tuple(violations)
        if not violations:
            return ValidationResult.success()

        resolved = self._resolve_errors(violations, bundle, locale)
        return ValidationResult.failure(violations, resolved)

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #
    def _apply_field_rules(
        self,
        field: str,
        rules: dict[str, Any],
        payload: dict,
        violations: list[Violation],
    ) -> None:
        value = payload.get(field)
        path = field

        # Required
        if rules.get("required") and value is None:
            violations.append(
                Violation(
                    code="VAL.REQUIRED",
                    path=path,
                    params={"field": field},
                )
            )
            return

        if value is None:
            return

        # Type checks
        expected_type = rules.get("type")
        if expected_type:
            if expected_type == "string" and not isinstance(value, str):
                violations.append(Violation("VAL.TYPE_STRING", path, {"expected": "string", "field": field}))
            elif expected_type == "integer" and not isinstance(value, int):
                violations.append(Violation("VAL.TYPE_INTEGER", path, {"expected": "integer", "field": field}))
            elif expected_type == "boolean" and not isinstance(value, bool):
                violations.append(Violation("VAL.TYPE_BOOLEAN", path, {"expected": "boolean", "field": field}))

        # Length constraints
        min_length = rules.get("min_length")
        if isinstance(value, str) and min_length is not None and len(value) < int(min_length):
            violations.append(Violation("VAL.MIN_LENGTH", path, {"min": int(min_length), "field": field}))

        max_length = rules.get("max_length")
        if isinstance(value, str) and max_length is not None and len(value) > int(max_length):
            violations.append(Violation("VAL.MAX_LENGTH", path, {"max": int(max_length), "field": field}))

        # Pattern
        pattern = rules.get("pattern")
        if isinstance(value, str) and pattern:
            if not re.fullmatch(pattern, value):
                violations.append(Violation("VAL.PATTERN", path, {"pattern": pattern, "field": field}))

        # Enum
        enum_values = rules.get("enum")
        if enum_values and value not in enum_values:
            violations.append(Violation("VAL.ENUM_INVALID", path, {"allowed": enum_values, "field": field}))

        # Numeric bounds
        ge = rules["minimum"] if "minimum" in rules else rules.get("ge")
        if ge is not None and isinstance(value, (int, float)) and value < float(ge):
            violations.append(Violation("VAL.MIN_VALUE", path, {"min": float(ge), "field": field}))

        le = rules["maximum"] if "maximum" in rules else rules.get("le")
        if le is not None and isinstance(value, (int, float)) and value > float(le):
            violations.append(Violation("VAL.MAX_VALUE", path, {"max": float(le), "field": field}))

    def _apply_operation_rules(
        self,
        rules: dict[str, dict[str, Any]],
        operation: str,
        payload: dict,
        violations: list[Violation],
    ) -> None:
        op_rules = rules.get(operation, {})
        required_fields = op_rules.get("required_fields", [])
        for field in required_fields:
            if payload.get(field) in (None, "", []):
                violations.append(
                    Violation(
                        code="VAL.REQUIRED",
                        path=field,
                        params={"field": field, "operation": operation},
                    )
                )

    def _resolve_errors(
        self,
        violations: Iterable[Violation],
        bundle: PackBundle,
        locale: str | None,
    ) -> tuple[ResolvedError, ...]:
        if not self._error_catalog:
            raise RuntimeError("Error catalog not configured on validation service")

        resolved_errors: list[ResolvedError] = []
        effective_locale = locale or self._default_locale
        for violation in violations:
            resolved_errors.append(
                self._error_catalog.resolve(
                    violation.code,
                    params=violation.params,
                    bundle=bundle,
                    locale=effective_locale,
                )
            )
        return tuple(resolved_errors)


GuardError: type[Exception] | None
IdGuards: type[Any] | None
TenantGuards: type[Any] | None
Validator: type[Any] | None

try:
    from hyeve_core.hyeve_core.validation.service import (
        GuardError as _GuardError,
    )
    from hyeve_core.hyeve_core.validation.service import (
        IdGuards as _IdGuards,
    )
    from hyeve_core.hyeve_core.validation.service import (
        TenantGuards as _TenantGuards,
    )
    from hyeve_core.hyeve_core.validation.service import (
        Validator as _Validator,
    )
except Exception:  # pragma: no cover - fallback for older package layouts
    GuardError = None
    IdGuards = None
    TenantGuards = None
    Validator = None
else:
    GuardError = _GuardError
    IdGuards = _IdGuards
    TenantGuards = _TenantGuards
    Validator = _Validator


__all__ = [
    "DefaultValidationService",
    "GuardError",
    "IdGuards",
    "TenantGuards",
    "Validator",
]
