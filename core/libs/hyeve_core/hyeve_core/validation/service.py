"""
Implementation of the validation contract defined in :mod:`hyeve_core.core_libs`.

The validator combines JSON Schema validation with pack-driven rules.  It is
intentionally lightweight; complex business workflows can hook into the
``pre_validate``/``post_validate`` extensions exposed via the pack bundle.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime
from typing import Any
from uuid import UUID

from jsonschema import Draft7Validator  # type: ignore[import-untyped]

from ..core_libs.interfaces import ErrorCatalog, ValidationService
from ..core_libs.types import (
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


class GuardError(ValueError):
    """Raised when guard or validation helpers detect invalid state."""


class IdGuards:
    """
    Normalised ID validation helpers for generated services.

    IDs are considered valid when they are a 26-character ULID, a canonical
    UUID string, or a slug composed of ``[A-Za-z0-9_-]`` characters.  The slug
    fallback keeps support for human-readable identifiers (e.g., ``tenant-prod``).
    """

    _ULID_RE = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")
    _SLUG_RE = re.compile(r"^[A-Za-z0-9_-]{3,128}$")

    @classmethod
    def check_ulid_uuid(cls, **identifiers: str | None) -> None:
        """Ensure that every provided identifier matches one of the accepted shapes."""

        for name, raw_value in identifiers.items():
            if raw_value is None:
                continue
            if not isinstance(raw_value, str):
                raise GuardError(f"{name} must be a string")

            value = raw_value.strip()
            if not value:
                raise GuardError(f"{name} cannot be blank")

            if cls._ULID_RE.fullmatch(value):
                continue
            if cls._looks_like_uuid(value):
                continue
            if cls._SLUG_RE.fullmatch(value):
                continue

            raise GuardError(f"{name} must be a ULID, UUID, or slug-safe value")

    @staticmethod
    def _looks_like_uuid(value: str) -> bool:
        try:
            UUID(value)
        except (ValueError, AttributeError, TypeError):
            return False
        return True


class TenantGuards:
    """Tenant-isolation helpers shared across generated services."""

    @staticmethod
    def ensure_same_tenant(request_tenant_id: str, payload_tenant_id: str | None) -> None:
        """
        Enforce tenant isolation between request context and payload data.

        The payload may omit ``tenant_id`` (the service will inject it), but if
        it is present it must match the authenticated tenant.
        """

        if not request_tenant_id:
            raise GuardError("Request tenant_id must be supplied")

        if payload_tenant_id is None or payload_tenant_id == "":
            return

        if payload_tenant_id != request_tenant_id:
            raise GuardError(f"Tenant mismatch detected (got {payload_tenant_id}, expected {request_tenant_id})")


class Validator:
    """
    Lightweight validation engine for pack-driven field rules.

    The generator only needs a subset of the full validation contract exposed
    by :class:`DefaultValidationService`.  This helper keeps those semantics in
    sync with the templates while avoiding jsonschema round-trips for every
    CRUD operation.
    """

    _EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")

    @classmethod
    def validate(
        cls,
        payload: Mapping[str, Any] | None,
        validation_rules: Mapping[str, Any] | None,
    ) -> list[Violation]:
        """Validate payload according to the simplified pack rules."""

        if not validation_rules:
            return []

        data = dict(payload or {})
        violations: list[Violation] = []

        for field, field_rules in validation_rules.items():
            value = data.get(field)
            missing = cls._apply_required(field, field_rules, value, violations)
            if missing:
                continue

            if isinstance(value, str):
                cls._apply_string_rules(field, field_rules, value, violations)
                cls._apply_format_rules(field, field_rules, value, violations)
            elif isinstance(value, (int, float)):
                cls._apply_numeric_rules(field, field_rules, float(value), violations)

        return violations

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _is_missing(value: Any) -> bool:
        if value is None:
            return True
        if isinstance(value, str) and value.strip() == "":
            return True
        return False

    @classmethod
    def _apply_required(
        cls,
        field: str,
        field_rules: Mapping[str, Any],
        value: Any,
        violations: list[Violation],
    ) -> bool:
        required = bool(field_rules.get("required"))
        if not required:
            return False

        if cls._is_missing(value):
            violations.append(cls._build_violation(field, "required", field_rules, {"field": field}))
            return True
        return False

    @classmethod
    def _apply_string_rules(
        cls,
        field: str,
        field_rules: Mapping[str, Any],
        value: str,
        violations: list[Violation],
    ) -> None:
        min_length = field_rules.get("minLength")
        if isinstance(min_length, int) and len(value) < min_length:
            violations.append(
                cls._build_violation(
                    field,
                    "minLength",
                    field_rules,
                    {"field": field, "minLength": min_length, "length": len(value)},
                )
            )

        max_length = field_rules.get("maxLength")
        if isinstance(max_length, int) and len(value) > max_length:
            violations.append(
                cls._build_violation(
                    field,
                    "maxLength",
                    field_rules,
                    {"field": field, "maxLength": max_length, "length": len(value)},
                )
            )

        pattern = field_rules.get("pattern")
        if isinstance(pattern, str):
            regex = re.compile(pattern)
            if not regex.fullmatch(value):
                violations.append(
                    cls._build_violation(
                        field,
                        "pattern",
                        field_rules,
                        {"field": field, "pattern": pattern, "value": value},
                    )
                )

    @classmethod
    def _apply_numeric_rules(
        cls,
        field: str,
        field_rules: Mapping[str, Any],
        value: float,
        violations: list[Violation],
    ) -> None:
        minimum = field_rules.get("minimum")
        if minimum is not None and value < float(minimum):
            violations.append(
                cls._build_violation(
                    field,
                    "minimum",
                    field_rules,
                    {"field": field, "minimum": float(minimum), "value": value},
                )
            )

        maximum = field_rules.get("maximum")
        if maximum is not None and value > float(maximum):
            violations.append(
                cls._build_violation(
                    field,
                    "maximum",
                    field_rules,
                    {"field": field, "maximum": float(maximum), "value": value},
                )
            )

    @classmethod
    def _apply_format_rules(
        cls,
        field: str,
        field_rules: Mapping[str, Any],
        value: str,
        violations: list[Violation],
    ) -> None:
        fmt = field_rules.get("format")
        if not isinstance(fmt, str):
            return

        validators: dict[str, Callable[[str], bool]] = {
            "email": lambda value: bool(cls._EMAIL_RE.fullmatch(value)),
            "date-time": cls._looks_like_datetime,
        }
        if IdGuards is not None:
            validators["uuid"] = IdGuards._looks_like_uuid
            validators["ulid"] = lambda v: bool(IdGuards._ULID_RE.fullmatch(v))
        validator: Callable[[str], bool] | None = validators.get(fmt.lower())

        if validator and not bool(validator(value)):
            violations.append(
                cls._build_violation(
                    field,
                    "format",
                    field_rules,
                    {"field": field, "format": fmt, "value": value},
                )
            )

    @staticmethod
    def _looks_like_datetime(value: str) -> bool:
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return False
        return True

    @staticmethod
    def _build_violation(
        field: str,
        rule_key: str,
        field_rules: Mapping[str, Any],
        params: dict[str, Any],
    ) -> Violation:
        message_keys = field_rules.get("message_keys") or {}
        custom_messages = field_rules.get("customMessages") or {}
        code = str(message_keys.get(rule_key) or f"VAL.{rule_key.upper()}")
        custom_message = custom_messages.get(rule_key)
        payload = dict(params)
        if custom_message:
            payload.setdefault("message", custom_message)
        payload.setdefault("field", field)
        return Violation(code=code, path=field, params=payload)
