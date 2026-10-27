"""
Hyeve Core Validation Module
===========================

Centralized validation system for Hyeve microservices.
Provides schema validation, business rules, and field validation utilities.
"""

import json
import logging
import re
from pathlib import Path
from typing import Any

import jsonschema  # type: ignore[import-untyped]

logger = logging.getLogger(__name__)


class ValidationError(Exception):
    """Custom validation error with detailed information."""

    def __init__(self, field: str, code: str, message: str, details: dict[str, Any] | None = None):
        self.field = field
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(self.message)


class ValidationResult:
    """Result of a validation operation."""

    def __init__(self, is_valid: bool, errors: list[ValidationError] | None = None):
        self.is_valid = is_valid
        self.errors = errors or []

    def add_error(self, error: ValidationError):
        """Add a validation error."""
        self.errors.append(error)
        self.is_valid = False

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "is_valid": self.is_valid,
            "errors": [
                {"field": e.field, "code": e.code, "message": e.message, "details": e.details} for e in self.errors
            ],
        }


class ValidationService:
    """
    Centralized validation service with dynamic schema loading and caching.

    Features:
    - Dynamic schema loading from configurable locations
    - JSON Schema validation with caching
    - Custom business rules
    - Localized error messages
    - Performance optimization
    """

    def __init__(
        self,
        schema_base_path: Path | None = None,
        messages_path: Path | None = None,
        cache_max_size: int = 1000,
        default_language: str = "en",
    ):
        """
        Initialize validation service with dynamic schema loading.

        Args:
            schema_base_path: Base directory for schema files (e.g., /packs)
            messages_path: Path to localized error messages file
            cache_max_size: Maximum number of cached validation results
            default_language: Default language for error messages
        """
        self.schema_base_path = schema_base_path or Path("./packs")  # Default to ./packs
        self.messages_path = messages_path or Path(__file__).parent / "messages.json"

        # Schema cache - maps entity names to their schemas
        self._schema_cache: dict[str, dict[str, Any]] = {}
        self._schema_timestamps: dict[str, float] = {}  # Track file modification times

        # Validation result cache
        self._validation_cache: dict[str, ValidationResult] = {}
        self._cache_max_size = cache_max_size

        # Load messages
        self.messages = self._load_messages()
        self.default_language = default_language

        logger.info(f"ValidationService initialized with schema path: {self.schema_base_path}")

    def _load_messages(self) -> dict[str, Any]:
        """Load localized error messages from JSON file."""
        try:
            with open(self.messages_path, encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load validation messages: {str(e)}")
            return {}

    def _load_entity_schema(self, entity: str) -> dict[str, Any] | None:
        """
        Load schema for a specific entity from the schema directory.

        Args:
            entity: Entity name (e.g., 'users', 'organizations')

        Returns:
            Schema dictionary or None if not found
        """
        schema_file = self.schema_base_path / f"{entity}.json"

        # Check if file exists and if it has been modified
        if not schema_file.exists():
            logger.debug(f"Schema file not found: {schema_file}")
            return None

        # Check modification time for cache invalidation
        try:
            mtime = schema_file.stat().st_mtime
            if entity in self._schema_timestamps and self._schema_timestamps[entity] >= mtime:
                # Return cached schema
                return self._schema_cache.get(entity)
        except OSError as e:
            logger.warning(f"Could not check schema file timestamp: {e}")
            return None

        # Load and cache the schema
        try:
            with open(schema_file, encoding="utf-8") as f:
                schema = json.load(f)

            # Cache the schema and timestamp
            self._schema_cache[entity] = schema
            self._schema_timestamps[entity] = mtime

            logger.debug(f"Loaded schema for entity '{entity}' from {schema_file}")
            return schema

        except Exception as e:
            logger.error(f"Failed to load schema for entity '{entity}': {e}")
            return None

    def _get_message(self, code: str, language: str | None = None, **kwargs) -> str:
        """Get localized error message."""
        lang = language or self.default_language
        messages = self.messages.get(lang, self.messages.get("en", {}))

        # Navigate to the message
        keys = code.split(".")
        message = messages
        for key in keys:
            message = message.get(key, {})

        if isinstance(message, str):
            return message.format(**kwargs)
        else:
            return f"Unknown error: {code}"

    def _get_cache_key(self, entity: str, data: dict[str, Any]) -> str:
        """Generate cache key for validation result."""
        data_str = json.dumps(data, sort_keys=True, default=str)
        key_data = f"{entity}:{data_str}"
        return str(hash(key_data))  # Convert hash to string

    def _get_entity_schema(self, entity: str) -> dict[str, Any] | None:
        """Get JSON schema for an entity with caching."""
        return self._load_entity_schema(entity)

    def validate(
        self, entity: str, data: dict[str, Any], language: str | None = None, skip_cache: bool = False
    ) -> ValidationResult:
        """
        Validate data against entity schema and business rules.

        Args:
            entity: Entity name (e.g., 'users', 'organizations')
            data: Data to validate
            language: Language for error messages
            skip_cache: Skip cache lookup

        Returns:
            ValidationResult with validation status and errors
        """
        # Check cache first
        if not skip_cache:
            cache_key = self._get_cache_key(entity, data)
            if cache_key in self._validation_cache:
                return self._validation_cache[cache_key]

        result = ValidationResult(True)

        # Get entity schema
        schema = self._get_entity_schema(entity)
        if not schema:
            result.add_error(
                ValidationError(
                    field="entity",
                    code="validation.unknown_entity",
                    message=self._get_message("validation.invalid_value", language, field="entity"),
                )
            )
            return result

        # JSON Schema validation
        try:
            jsonschema.validate(data, schema, format_checker=jsonschema.Draft7Validator.FORMAT_CHECKER)
        except jsonschema.ValidationError as e:
            field = ".".join(str(p) for p in e.absolute_path) if e.absolute_path else "root"
            result.add_error(
                ValidationError(
                    field=field,
                    code="validation.schema_error",
                    message=self._get_message("validation.invalid_format", language, field=field),
                    details={"schema_error": str(e)},
                )
            )

        # Custom business rules validation
        self._validate_business_rules(entity, data, result, language)

        # Cache result if valid and not skipping cache
        if result.is_valid and not skip_cache and len(self._validation_cache) < self._cache_max_size:
            cache_key = self._get_cache_key(entity, data)
            self._validation_cache[cache_key] = result

        return result

    def _validate_business_rules(
        self, entity: str, data: dict[str, Any], result: ValidationResult, language: str | None
    ):
        """Apply custom business rules validation."""
        # Load validation rules from schema
        schema = self._load_entity_schema(entity) or {}
        rules = schema.get("validation_rules", {})

        if entity == "users":
            self._validate_user_rules(data, rules, result, language)
        elif entity == "organizations":
            self._validate_organization_rules(data, rules, result, language)

    def _validate_user_rules(
        self, data: dict[str, Any], rules: dict[str, Any], result: ValidationResult, language: str | None
    ):
        """Validate user-specific business rules."""
        # Email domain validation
        email = data.get("email", "")
        if email:
            domain = email.split("@")[-1] if "@" in email else ""
            allowed_domains = rules.get("email_domains", {}).get("allowed", [])
            blocked_domains = rules.get("email_domains", {}).get("blocked", [])

            if allowed_domains and domain not in allowed_domains:
                result.add_error(
                    ValidationError(
                        field="email",
                        code="business.invalid_domain",
                        message=self._get_message("validation.invalid_value", language, field="email"),
                        details={"allowed_domains": allowed_domains},
                    )
                )

            if domain in blocked_domains:
                result.add_error(
                    ValidationError(
                        field="email",
                        code="business.blocked_domain",
                        message=self._get_message("validation.invalid_value", language, field="email"),
                        details={"blocked_domain": domain},
                    )
                )

    def _validate_organization_rules(
        self, data: dict[str, Any], rules: dict[str, Any], result: ValidationResult, language: str | None
    ):
        """Validate organization-specific business rules."""
        # Add organization-specific validations here
        pass

    def validate_field(self, entity: str, field: str, value: Any, language: str | None = None) -> ValidationResult:
        """
        Validate a single field.

        Args:
            entity: Entity name
            field: Field name
            value: Field value
            language: Language for error messages

        Returns:
            ValidationResult
        """
        result = ValidationResult(True)

        # Get entity schema
        schema = self._get_entity_schema(entity)
        if not schema:
            result.add_error(
                ValidationError(
                    field="entity",
                    code="validation.unknown_entity",
                    message=self._get_message("validation.invalid_value", language, field="entity"),
                )
            )
            return result

        # Extract field schema
        field_schema = schema.get("properties", {}).get(field)
        if not field_schema:
            result.add_error(
                ValidationError(
                    field=field,
                    code="validation.unknown_field",
                    message=self._get_message("validation.invalid_value", language, field=field),
                )
            )
            return result

        # Validate field against its schema
        try:
            jsonschema.validate(value, field_schema, format_checker=jsonschema.Draft7Validator.FORMAT_CHECKER)
        except jsonschema.ValidationError as e:
            result.add_error(
                ValidationError(
                    field=field,
                    code="validation.schema_error",
                    message=self._get_message("validation.invalid_format", language, field=field),
                    details={"schema_error": str(e)},
                )
            )

        return result

    def clear_cache(self):
        """Clear validation cache."""
        self._validation_cache.clear()
        self._schema_cache.clear()

    def get_supported_entities(self) -> list[str]:
        """Get list of supported entities by scanning schema directory."""
        entities = []
        if self.schema_base_path.exists():
            for schema_file in self.schema_base_path.glob("*.json"):
                if schema_file.is_file():
                    entity_name = schema_file.stem
                    entities.append(entity_name)
        return entities

    def get_entity_schema(self, entity: str) -> dict[str, Any] | None:
        """Get schema for a specific entity."""
        return self._get_entity_schema(entity)


class ValidationUtils:
    """Validation utility functions."""

    @staticmethod
    def is_valid_identifier(name: str) -> bool:
        """Check if a string is a valid Python/TypeScript identifier."""
        if not name or not name[0].isalpha() and name[0] != "_":
            return False
        return all(c.isalnum() or c == "_" for c in name)

    @staticmethod
    def is_valid_table_name(name: str) -> bool:
        """Check if a string is a valid table name."""
        if not name or len(name) > 63:  # PostgreSQL limit
            return False
        return all(c.isalnum() or c in ("_", "-") for c in name)

    @staticmethod
    def sanitize_identifier(name: str) -> str:
        """Sanitize a string to be a valid identifier."""
        if not name:
            return "unnamed"

        # Replace invalid characters with underscore
        sanitized = "".join(c if c.isalnum() else "_" for c in name)

        # Ensure it starts with letter or underscore
        if sanitized and sanitized[0].isdigit():
            sanitized = "_" + sanitized

        return sanitized or "unnamed"


# Field validation utilities
EMAIL_REGEX = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
PHONE_REGEX = r"^\+?[\d\s\-\(\)]+$"


def extract_max_length(column_type: str) -> int | None:
    """Extract maximum length from VARCHAR/CHAR definitions."""
    match = re.search(r"(?:var)?char\((\d+)\)", column_type, re.IGNORECASE)
    if match:
        return int(match.group(1))
    return None


def build_field_validation(
    table_name: str,
    column_info: Any,
    *,
    field_title: str | None = None,
) -> dict[str, Any]:
    """
    Build validation metadata for a given column.

    Returns a dictionary containing constraint values and message keys.
    """
    # Handle both ColumnInfo objects and dict-like objects
    if hasattr(column_info, "name"):
        name = column_info.name
        col_type = column_info.type
        nullable = column_info.nullable
        default = getattr(column_info, "default", None)
        enum_values = getattr(column_info, "enum_values", None)
    else:
        # Assume dict-like
        name = column_info.get("name", "")
        col_type = column_info.get("type", "")
        nullable = column_info.get("nullable", True)
        default = column_info.get("default")
        enum_values = column_info.get("enum_values")

    title = field_title or name.replace("_", " ").title()
    rules: dict[str, Any] = {}

    # Required constraint
    if not nullable and default is None:
        rules["required"] = True

    # String type validations
    if isinstance(col_type, str) and col_type.upper() in {"TEXT", "VARCHAR", "CHAR"}:
        max_length = extract_max_length(col_type)
        if max_length:
            rules["maxLength"] = max_length

        if rules.get("required"):
            rules["minLength"] = 1

        lowered = name.lower()
        if "email" in lowered:
            rules["pattern"] = EMAIL_REGEX
            rules["format"] = "email"
        elif "phone" in lowered or "contact_number" in lowered:
            rules["pattern"] = PHONE_REGEX

    # Numeric validations (e.g., GPA)
    lowered_name = name.lower()
    if "gpa" in lowered_name:
        rules["minimum"] = 0.0
        rules["maximum"] = 4.0

    # Enum validation
    if enum_values:
        rules["enum"] = enum_values

    return rules if rules else {}


DefaultValidationService: type[Any] | None
IdGuards: type[Any] | None
TenantGuards: type[Any] | None
Validator: type[Any] | None

try:
    from hyeve_core.hyeve_core.validation.service import (
        DefaultValidationService as _DefaultValidationService,
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
    DefaultValidationService = None
    IdGuards = None
    TenantGuards = None
    Validator = None
else:
    DefaultValidationService = _DefaultValidationService
    IdGuards = _IdGuards
    TenantGuards = _TenantGuards
    Validator = _Validator


__all__ = [
    "ValidationError",
    "ValidationResult",
    "ValidationService",
    "ValidationUtils",
    "extract_max_length",
    "build_field_validation",
    "DefaultValidationService",
    "Validator",
    "IdGuards",
    "TenantGuards",
]
