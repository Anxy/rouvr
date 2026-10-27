"""
DAL Exception Classes

Custom exception hierarchy for the DAL library providing detailed error context
and proper error handling for different failure scenarios.
"""

from typing import Any


class DALError(Exception):
    """Base exception for all DAL operations."""

    def __init__(self, message: str, context: dict[str, Any] | None = None):
        super().__init__(message)
        self.message = message
        self.context = context or {}

    def __str__(self) -> str:
        if self.context:
            return f"{self.message} | Context: {self.context}"
        return self.message


class DALConnectionError(DALError):
    """Database connection errors."""

    def __init__(self, message: str, db_type: str, connection_string: str | None = None):
        context = {"db_type": db_type}
        if connection_string:
            # Mask sensitive connection details
            masked_conn = connection_string.split("@")[-1] if "@" in connection_string else "masked"
            context["connection"] = masked_conn
        super().__init__(message, context)


class DALValidationError(DALError):
    """Data validation errors."""

    def __init__(self, message: str, field_errors: dict[str, str] | None = None, model_name: str | None = None):
        context: dict[str, Any] = {}
        if field_errors:
            context["field_errors"] = field_errors
        if model_name:
            context["model"] = model_name
        super().__init__(message, context)


class DALCacheError(DALError):
    """Cache operation errors."""

    def __init__(self, message: str, cache_key: str | None = None, operation: str | None = None):
        context: dict[str, Any] = {}
        if cache_key:
            context["cache_key"] = cache_key
        if operation:
            context["operation"] = operation
        super().__init__(message, context)


class DALTenantError(DALError):
    """Tenant isolation errors."""

    def __init__(self, message: str, tenant_id: str | None = None, resource: str | None = None):
        context: dict[str, Any] = {}
        if tenant_id:
            context["tenant_id"] = tenant_id
        if resource:
            context["resource"] = resource
        super().__init__(message, context)


class DALGenerationError(DALError):
    """Code generation errors."""

    def __init__(self, message: str, generator_type: str | None = None, metadata_path: str | None = None):
        context: dict[str, Any] = {}
        if generator_type:
            context["generator"] = generator_type
        if metadata_path:
            context["metadata_path"] = metadata_path
        super().__init__(message, context)


class DALConfigurationError(DALError):
    """Configuration loading and validation errors."""

    def __init__(self, message: str, config_path: str | None = None, config_section: str | None = None):
        context: dict[str, Any] = {}
        if config_path:
            context["config_path"] = config_path
        if config_section:
            context["config_section"] = config_section
        super().__init__(message, context)


class DALWorkflowError(DALError):
    """Workflow integration errors."""

    def __init__(self, message: str, packet_id: str | None = None, workflow_type: str | None = None):
        context: dict[str, Any] = {}
        if packet_id:
            context["packet_id"] = packet_id
        if workflow_type:
            context["workflow_type"] = workflow_type
        super().__init__(message, context)


class DALMigrationError(DALError):
    """Database migration errors."""

    def __init__(self, message: str, migration_name: str | None = None, service_dir: str | None = None):
        context: dict[str, Any] = {}
        if migration_name:
            context["migration"] = migration_name
        if service_dir:
            context["service_dir"] = service_dir
        super().__init__(message, context)


class DALPerformanceError(DALError):
    """Performance-related errors and warnings."""

    def __init__(self, message: str, operation_time: float | None = None, threshold: float | None = None):
        context: dict[str, Any] = {}
        if operation_time:
            context["operation_time"] = operation_time
        if threshold:
            context["threshold"] = threshold
        super().__init__(message, context)


class DALRetryableError(DALError):
    """Errors that can be retried with backoff."""

    def __init__(self, message: str, retry_count: int = 0, max_retries: int = 3):
        context = {"retry_count": retry_count, "max_retries": max_retries}
        super().__init__(message, context)
        self.retry_count = retry_count
        self.max_retries = max_retries

    def can_retry(self) -> bool:
        """Check if operation can be retried."""
        return self.retry_count < self.max_retries

    def increment_retry(self) -> "DALRetryableError":
        """Create new instance with incremented retry count."""
        return DALRetryableError(self.message, self.retry_count + 1, self.max_retries)
