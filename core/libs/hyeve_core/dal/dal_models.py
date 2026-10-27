"""
DAL Data Models

Pydantic models for data validation, CRUD operations, and tenant isolation.
Implements ULID generation, tenant isolation, and comprehensive validation.
"""

import re
from datetime import UTC, datetime
from typing import Any

import ulid  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator

from .interfaces import CRUDOperationType


def generate_ulid() -> str:
    """Generate a new ULID string."""
    return str(ulid.new())


def validate_ulid(ulid_str: str) -> bool:
    """Validate ULID format."""
    try:
        ulid.from_str(ulid_str)
        return True
    except (ValueError, TypeError):
        return False


def validate_tenant_id(tenant_id: str) -> bool:
    """Validate tenant ID format (alphanumeric, hyphens, underscores)."""
    if not tenant_id or len(tenant_id) < 3 or len(tenant_id) > 50:
        return False
    return bool(re.match(r"^[a-zA-Z0-9_-]+$", tenant_id))


class BaseDALModel(BaseModel):
    """
    Base model for all DAL entities.

    Provides common fields for all entities including tenant isolation,
    soft deletes, audit trail information, and ULID generation.
    """

    id: str = Field(default_factory=generate_ulid, description="ULID identifier")
    tenant_id: str = Field(..., description="Tenant identifier for multi-tenant isolation")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC), description="Creation timestamp")
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC), description="Last update timestamp")
    created_by: str = Field(..., description="User ID who created the record")
    updated_by: str = Field(..., description="User ID who last updated the record")
    record_version: int = Field(default=1, ge=1, description="Record version for optimistic locking")
    deleted_at: datetime | None = Field(default=None, description="Soft delete timestamp")

    @field_validator("id")
    @classmethod
    def validate_id_format(cls, v):
        """Validate ULID format for ID field."""
        if not validate_ulid(v):
            raise ValueError(f"Invalid ULID format: {v}")
        return v

    @field_validator("tenant_id")
    @classmethod
    def validate_tenant_id_format(cls, v):
        """Validate tenant ID format."""
        if not validate_tenant_id(v):
            raise ValueError(
                f"Invalid tenant ID format: {v}. Must be 3-50 alphanumeric characters, hyphens, or underscores."
            )
        return v

    @field_validator("created_by", "updated_by")
    @classmethod
    def validate_user_ids(cls, v):
        """Validate user ID format (should be ULID)."""
        if not validate_ulid(v):
            raise ValueError(f"Invalid user ID format: {v}. Must be a valid ULID.")
        return v

    @model_validator(mode="after")
    def validate_timestamps(self):
        """Ensure timestamp consistency."""
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot be before created_at")

        if self.deleted_at is not None and self.deleted_at < self.created_at:
            raise ValueError("deleted_at cannot be before created_at")

        return self

    def is_deleted(self) -> bool:
        """Check if the record is soft deleted."""
        return self.deleted_at is not None

    def mark_deleted(self, deleted_by: str) -> None:
        """Mark the record as soft deleted."""
        self.deleted_at = datetime.now(UTC)
        self.updated_by = deleted_by
        self.updated_at = datetime.now(UTC)
        self.record_version += 1

    def update_version(self, updated_by: str) -> None:
        """Update the record version and timestamps."""
        self.updated_by = updated_by
        self.updated_at = datetime.now(UTC)
        self.record_version += 1

    @field_serializer("created_at", "updated_at", "deleted_at")
    def serialize_datetime(self, value: datetime | None):
        return value.isoformat() if value else None

    model_config = ConfigDict(
        validate_assignment=True,
        use_enum_values=True,
    )


class CRUDOperation(BaseModel):
    """
    Model for CRUD operation requests.

    Encapsulates all information needed to perform a CRUD operation
    including validation, tenant isolation, and audit information.
    """

    operation_type: CRUDOperationType = Field(..., description="Type of CRUD operation")
    table_name: str = Field(..., min_length=1, max_length=100, description="Target table name")
    data: dict[str, Any] | None = Field(default=None, description="Operation data payload")
    tenant_id: str = Field(..., description="Tenant identifier for isolation")
    user_id: str = Field(..., description="User performing the operation")
    filters: dict[str, Any] | None = Field(default=None, description="Query filters for read/update/delete")
    options: dict[str, Any] | None = Field(default=None, description="Additional operation options")

    @field_validator("table_name")
    @classmethod
    def validate_table_name(cls, v):
        """Validate table name format."""
        if not re.match(r"^[a-zA-Z][a-zA-Z0-9_]*$", v):
            raise ValueError(
                f"Invalid table name: {v}. Must start with letter and contain only alphanumeric characters and underscores."
            )
        return v

    @field_validator("tenant_id")
    @classmethod
    def validate_tenant_id_format(cls, v):
        """Validate tenant ID format."""
        if not validate_tenant_id(v):
            raise ValueError(f"Invalid tenant ID format: {v}")
        return v

    @field_validator("user_id")
    @classmethod
    def validate_user_id_format(cls, v):
        """Validate user ID format."""
        if not validate_ulid(v):
            raise ValueError(f"Invalid user ID format: {v}. Must be a valid ULID.")
        return v

    @model_validator(mode="after")
    def validate_operation_data(self):
        """Validate operation data based on operation type."""
        if self.operation_type == CRUDOperationType.CREATE:
            if not self.data:
                raise ValueError("CREATE operations require data payload")
        elif self.operation_type == CRUDOperationType.UPDATE:
            if not self.data:
                raise ValueError("UPDATE operations require data payload")
            if not self.filters:
                raise ValueError("UPDATE operations require filters to identify records")
        elif self.operation_type == CRUDOperationType.DELETE:
            if not self.filters:
                raise ValueError("DELETE operations require filters to identify records")

        return self

    def add_audit_fields(self) -> None:
        """Add audit fields to the operation data."""
        if self.data is None:
            self.data = {}

        if self.operation_type == CRUDOperationType.CREATE:
            self.data.update(
                {
                    "id": generate_ulid(),
                    "tenant_id": self.tenant_id,
                    "created_by": self.user_id,
                    "updated_by": self.user_id,
                    "created_at": datetime.now(UTC),
                    "updated_at": datetime.now(UTC),
                    "record_version": 1,
                }
            )
        elif self.operation_type == CRUDOperationType.UPDATE:
            self.data.update({"updated_by": self.user_id, "updated_at": datetime.now(UTC)})
            # Increment version if not explicitly set
            if "record_version" not in self.data:
                self.data["record_version"] = 1  # Will be incremented in the service

    model_config = ConfigDict(validate_assignment=True, use_enum_values=True)


class CRUDResult(BaseModel):
    """
    Model for CRUD operation results.

    Standardized response format for all CRUD operations including
    success status, data, metadata, and performance information.
    """

    success: bool = Field(..., description="Operation success status")
    operation_type: CRUDOperationType = Field(..., description="Type of operation performed")
    table_name: str = Field(..., description="Target table name")
    data: dict[str, Any] | list[dict[str, Any]] | None = Field(default=None, description="Result data")
    affected_rows: int = Field(default=0, ge=0, description="Number of rows affected")
    execution_time_ms: float = Field(default=0.0, ge=0.0, description="Execution time in milliseconds")
    cache_hit: bool = Field(default=False, description="Whether result came from cache")
    error_message: str | None = Field(default=None, description="Error message if operation failed")
    error_code: str | None = Field(default=None, description="Error code for programmatic handling")
    metadata: dict[str, Any] | None = Field(default=None, description="Additional operation metadata")

    @model_validator(mode="after")
    def validate_result_consistency(self):
        """Validate result consistency."""
        # Validate data consistency with success status
        if self.success and self.data is None and self.operation_type == CRUDOperationType.READ:
            # READ operations should return data or empty list when successful
            self.data = []

        # Validate error fields consistency
        if not self.success and not self.error_message:
            raise ValueError("Failed operations must include error_message")

        if self.success and (self.error_message or self.error_code):
            raise ValueError("Successful operations should not include error fields")

        return self

    def add_performance_metadata(self, db_type: str, cache_enabled: bool, query_complexity: str = "simple") -> None:
        """Add performance-related metadata."""
        if self.metadata is None:
            self.metadata = {}

        self.metadata.update(
            {
                "database_type": db_type,
                "cache_enabled": cache_enabled,
                "query_complexity": query_complexity,
                "timestamp": datetime.now(UTC).isoformat(),
            }
        )

    model_config = ConfigDict(validate_assignment=True, use_enum_values=True)


class TenantIsolationModel(BaseModel):
    """
    Model for tenant isolation validation and enforcement.

    Ensures all operations respect multi-tenant boundaries and
    provides utilities for tenant-aware data filtering.
    """

    tenant_id: str = Field(..., description="Tenant identifier")
    user_id: str = Field(..., description="User identifier")
    resource_type: str = Field(..., description="Type of resource being accessed")
    resource_id: str | None = Field(default=None, description="Specific resource identifier")
    operation: str = Field(..., description="Operation being performed")

    @field_validator("tenant_id")
    @classmethod
    def validate_tenant_id_format(cls, v):
        """Validate tenant ID format."""
        if not validate_tenant_id(v):
            raise ValueError(f"Invalid tenant ID format: {v}")
        return v

    @field_validator("user_id", "resource_id")
    @classmethod
    def validate_ulid_fields(cls, v):
        """Validate ULID format for ID fields."""
        if v is not None and not validate_ulid(v):
            raise ValueError(f"Invalid ULID format: {v}")
        return v

    @field_validator("resource_type", "operation")
    @classmethod
    def validate_string_fields(cls, v):
        """Validate string field formats."""
        if not v or not re.match(r"^[a-zA-Z][a-zA-Z0-9_]*$", v):
            raise ValueError(
                f"Invalid format: {v}. Must start with letter and contain only alphanumeric characters and underscores."
            )
        return v

    def get_tenant_filter(self) -> dict[str, Any]:
        """Get filter for tenant isolation."""
        return {"tenant_id": self.tenant_id}

    def get_soft_delete_filter(self) -> dict[str, Any]:
        """Get filter to exclude soft deleted records."""
        return {"deleted_at": None}

    def get_combined_filter(self) -> dict[str, Any]:
        """Get combined filter for tenant isolation and soft delete exclusion."""
        return {"tenant_id": self.tenant_id, "deleted_at": None}

    model_config = ConfigDict(validate_assignment=True)


class ValidationResult(BaseModel):
    """
    Model for validation results.

    Provides detailed information about validation success/failure
    and specific validation errors.
    """

    is_valid: bool = Field(..., description="Overall validation status")
    errors: list[str] = Field(default_factory=list, description="List of validation errors")
    warnings: list[str] = Field(default_factory=list, description="List of validation warnings")
    validated_data: dict[str, Any] | None = Field(default=None, description="Validated and cleaned data")

    def add_error(self, error: str) -> None:
        """Add a validation error."""
        self.errors.append(error)
        self.is_valid = False

    def add_warning(self, warning: str) -> None:
        """Add a validation warning."""
        self.warnings.append(warning)

    def has_errors(self) -> bool:
        """Check if there are validation errors."""
        return len(self.errors) > 0

    def has_warnings(self) -> bool:
        """Check if there are validation warnings."""
        return len(self.warnings) > 0

    model_config = ConfigDict(validate_assignment=True)
