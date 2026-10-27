# mypy: ignore-errors
"""
Tenant Isolation and Soft Delete Validation

Provides comprehensive validation and enforcement for multi-tenant data isolation
and soft delete operations across all DAL components.
"""

from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError

from .dal_models import (
    BaseDALModel,
    CRUDOperation,
    TenantIsolationModel,
    ValidationResult,
    validate_tenant_id,
    validate_ulid,
)
from .exceptions import DALTenantError, DALValidationError


class TenantValidator:
    """
    Validator for tenant isolation and soft delete operations.

    Ensures all database operations respect multi-tenant boundaries
    and properly handle soft delete semantics.
    """

    def __init__(self, strict_mode: bool = True):
        """
        Initialize tenant validator.

        Args:
            strict_mode: If True, validation failures raise exceptions.
                        If False, validation failures return error results.
        """
        self.strict_mode = strict_mode
        self._tenant_cache: set[str] = set()

    def validate_tenant_access(
        self, tenant_id: str, user_id: str, resource_type: str, operation: str, resource_id: str | None = None
    ) -> ValidationResult:
        """
        Validate tenant access for a specific operation.

        Args:
            tenant_id: Tenant identifier
            user_id: User performing the operation
            resource_type: Type of resource being accessed
            operation: Operation being performed
            resource_id: Specific resource identifier (optional)

        Returns:
            ValidationResult with validation status and details
        """
        result = ValidationResult(is_valid=True)

        try:
            # Validate tenant isolation model
            isolation_model = TenantIsolationModel(
                tenant_id=tenant_id,
                user_id=user_id,
                resource_type=resource_type,
                resource_id=resource_id,
                operation=operation,
            )

            # Additional business logic validation
            if not self._is_tenant_active(tenant_id):
                result.add_error(f"Tenant {tenant_id} is not active or does not exist")

            if not self._user_belongs_to_tenant(user_id, tenant_id):
                result.add_error(f"User {user_id} does not belong to tenant {tenant_id}")

            if result.is_valid:
                result.validated_data = isolation_model.model_dump()

        except ValidationError as e:
            for error in e.errors():
                result.add_error(f"Validation error: {error['msg']}")
        except Exception as e:
            result.add_error(f"Unexpected validation error: {str(e)}")

        if not result.is_valid and self.strict_mode:
            raise DALTenantError(f"Tenant validation failed: {'; '.join(result.errors)}")

        return result

    def validate_crud_operation(self, operation: CRUDOperation) -> ValidationResult:
        """
        Validate CRUD operation for tenant isolation compliance.

        Args:
            operation: CRUD operation to validate

        Returns:
            ValidationResult with validation status and details
        """
        result = ValidationResult(is_valid=True)

        try:
            # Validate basic tenant access
            access_result = self.validate_tenant_access(
                tenant_id=operation.tenant_id,
                user_id=operation.user_id,
                resource_type=operation.table_name,
                operation=operation.operation_type,
            )

            if not access_result.is_valid:
                result.errors.extend(access_result.errors)
                result.warnings.extend(access_result.warnings)
                result.is_valid = False

            # Validate operation-specific tenant isolation
            if operation.data:
                data_result = self._validate_data_tenant_isolation(operation.data, operation.tenant_id)
                if not data_result.is_valid:
                    result.errors.extend(data_result.errors)
                    result.is_valid = False

            if operation.filters:
                filter_result = self._validate_filter_tenant_isolation(operation.filters, operation.tenant_id)
                if not filter_result.is_valid:
                    result.errors.extend(filter_result.errors)
                    result.is_valid = False

            # Ensure tenant_id is included in filters for read/update/delete operations
            if operation.operation_type in ["read", "update", "delete"]:
                if not operation.filters or "tenant_id" not in operation.filters:
                    result.add_error("Read/Update/Delete operations must include tenant_id in filters")
                elif operation.filters.get("tenant_id") != operation.tenant_id:
                    result.add_error("Filter tenant_id must match operation tenant_id")

        except Exception as e:
            result.add_error(f"CRUD operation validation error: {str(e)}")

        if not result.is_valid and self.strict_mode:
            raise DALTenantError(f"CRUD operation validation failed: {'; '.join(result.errors)}")

        return result

    def enforce_tenant_isolation(self, operation: CRUDOperation) -> CRUDOperation:
        """
        Enforce tenant isolation by modifying operation filters and data.

        Args:
            operation: CRUD operation to enforce isolation on

        Returns:
            Modified CRUD operation with tenant isolation enforced
        """
        # First, enforce the tenant isolation by adding missing tenant_id
        # Ensure tenant_id is in data for create/update operations
        if operation.operation_type in ["create", "update"] and operation.data:
            operation.data["tenant_id"] = operation.tenant_id

        # Ensure tenant_id is in filters for all operations that use filters
        if operation.operation_type in ["read", "update", "delete"]:
            if operation.filters is None:
                operation.filters = {}
            operation.filters["tenant_id"] = operation.tenant_id

        # Now validate the operation after enforcement
        validation_result = self.validate_crud_operation(operation)
        if not validation_result.is_valid:
            if self.strict_mode:
                raise DALTenantError(
                    f"Operation still invalid after enforcement: {'; '.join(validation_result.errors)}"
                )

        return operation

    def validate_soft_delete_operation(
        self, operation: CRUDOperation, include_deleted: bool = False
    ) -> ValidationResult:
        """
        Validate soft delete operation compliance.

        Args:
            operation: CRUD operation to validate
            include_deleted: Whether to include soft-deleted records

        Returns:
            ValidationResult with validation status and details
        """
        result = ValidationResult(is_valid=True)

        try:
            # For read operations, ensure soft delete filtering
            if operation.operation_type == "read" and not include_deleted:
                if not operation.filters:
                    operation.filters = {}

                # Check if deleted_at filter is properly set
                if "deleted_at" not in operation.filters:
                    result.add_warning("Adding deleted_at filter to exclude soft-deleted records")
                    operation.filters["deleted_at"] = None
                elif operation.filters.get("deleted_at") is not None:
                    result.add_warning("Operation may include soft-deleted records")

            # For delete operations, ensure it's a soft delete
            if operation.operation_type == "delete":
                if not operation.data:
                    operation.data = {}

                # Convert hard delete to soft delete
                operation.data.update(
                    {"deleted_at": datetime.now(UTC), "updated_by": operation.user_id, "updated_at": datetime.now(UTC)}
                )

                # Change operation type to update for soft delete
                operation.operation_type = "update"
                result.add_warning("Converted hard delete to soft delete")

        except Exception as e:
            result.add_error(f"Soft delete validation error: {str(e)}")

        return result

    def get_tenant_isolation_filter(self, tenant_id: str, include_deleted: bool = False) -> dict[str, Any]:
        """
        Get standard tenant isolation filter.

        Args:
            tenant_id: Tenant identifier
            include_deleted: Whether to include soft-deleted records

        Returns:
            Filter dictionary for tenant isolation
        """
        if not validate_tenant_id(tenant_id):
            raise DALValidationError(f"Invalid tenant ID: {tenant_id}")

        filter_dict = {"tenant_id": tenant_id}

        if not include_deleted:
            filter_dict["deleted_at"] = None

        return filter_dict

    def validate_data_model(self, data: dict[str, Any], model_class: type) -> ValidationResult:
        """
        Validate data against a Pydantic model with tenant isolation checks.

        Args:
            data: Data to validate
            model_class: Pydantic model class to validate against

        Returns:
            ValidationResult with validation status and details
        """
        result = ValidationResult(is_valid=True)

        try:
            # Validate against Pydantic model
            if issubclass(model_class, BaseDALModel):
                validated_model = model_class(**data)
                result.validated_data = validated_model.model_dump()

                # Additional tenant isolation checks
                if hasattr(validated_model, "tenant_id"):
                    if not validate_tenant_id(validated_model.tenant_id):
                        result.add_error(f"Invalid tenant_id in model: {validated_model.tenant_id}")

                # Check for soft delete consistency
                if hasattr(validated_model, "deleted_at") and validated_model.deleted_at:
                    if hasattr(validated_model, "updated_at"):
                        if validated_model.deleted_at < validated_model.updated_at:
                            result.add_error("deleted_at cannot be before updated_at")
            else:
                # For non-DAL models, just validate structure
                validated_model = model_class(**data)
                result.validated_data = validated_model.model_dump()

        except ValidationError as e:
            for error in e.errors():
                result.add_error(
                    f"Model validation error: {error['msg']} at {'.'.join(str(loc) for loc in error['loc'])}"
                )
        except Exception as e:
            result.add_error(f"Unexpected model validation error: {str(e)}")

        if not result.is_valid and self.strict_mode:
            raise DALValidationError(f"Model validation failed: {'; '.join(result.errors)}")

        return result

    def _validate_data_tenant_isolation(self, data: dict[str, Any], expected_tenant_id: str) -> ValidationResult:
        """Validate tenant isolation in data payload."""
        result = ValidationResult(is_valid=True)

        if "tenant_id" in data:
            if data["tenant_id"] != expected_tenant_id:
                result.add_error(
                    f"Data tenant_id {data['tenant_id']} does not match operation tenant_id {expected_tenant_id}"
                )

        return result

    def _validate_filter_tenant_isolation(self, filters: dict[str, Any], expected_tenant_id: str) -> ValidationResult:
        """Validate tenant isolation in filter payload."""
        result = ValidationResult(is_valid=True)

        if "tenant_id" in filters:
            if filters["tenant_id"] != expected_tenant_id:
                result.add_error(
                    f"Filter tenant_id {filters['tenant_id']} does not match operation tenant_id {expected_tenant_id}"
                )

        return result

    def _is_tenant_active(self, tenant_id: str) -> bool:
        """Check if tenant is active (placeholder for actual implementation)."""
        # In a real implementation, this would check against a tenant registry
        # For now, we'll assume all valid tenant IDs are active
        return validate_tenant_id(tenant_id)

    def _user_belongs_to_tenant(self, user_id: str, tenant_id: str) -> bool:
        """Check if user belongs to tenant (placeholder for actual implementation)."""
        # In a real implementation, this would check user-tenant relationships
        # For now, we'll assume all valid users belong to their specified tenant
        return validate_ulid(user_id) and validate_tenant_id(tenant_id)


class SoftDeleteManager:
    """
    Manager for soft delete operations and queries.

    Provides utilities for handling soft delete semantics consistently
    across all DAL operations.
    """

    def __init__(self, deleted_at_column: str = "deleted_at"):
        """
        Initialize soft delete manager.

        Args:
            deleted_at_column: Name of the soft delete timestamp column
        """
        self.deleted_at_column = deleted_at_column

    def create_soft_delete_filter(self, include_deleted: bool = False) -> dict[str, Any]:
        """
        Create filter for soft delete handling.

        Args:
            include_deleted: Whether to include soft-deleted records

        Returns:
            Filter dictionary for soft delete handling
        """
        if include_deleted:
            return {}  # No filter, include all records
        else:
            return {self.deleted_at_column: None}  # Exclude soft-deleted records

    def create_soft_delete_data(self, user_id: str) -> dict[str, Any]:
        """
        Create data payload for soft delete operation.

        Args:
            user_id: User performing the soft delete

        Returns:
            Data dictionary for soft delete operation
        """
        return {self.deleted_at_column: datetime.now(UTC), "updated_by": user_id, "updated_at": datetime.now(UTC)}

    def is_soft_deleted(self, record: dict[str, Any]) -> bool:
        """
        Check if a record is soft deleted.

        Args:
            record: Record data dictionary

        Returns:
            True if record is soft deleted, False otherwise
        """
        deleted_at = record.get(self.deleted_at_column)
        return deleted_at is not None

    def restore_record_data(self, user_id: str) -> dict[str, Any]:
        """
        Create data payload for restoring a soft-deleted record.

        Args:
            user_id: User performing the restore

        Returns:
            Data dictionary for restore operation
        """
        return {self.deleted_at_column: None, "updated_by": user_id, "updated_at": datetime.now(UTC)}
