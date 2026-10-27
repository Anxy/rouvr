"""
Base interfaces and abstract classes for DAL components.

This module defines the core interfaces that all DAL components must implement,
ensuring consistent behavior across different database technologies and implementations.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from .dal_config import DALConfig

class DatabaseType(Enum):
    """Supported database types."""
    PRISMA = "prisma"
    SQLALCHEMY = "sqlalchemy"
    ASYNCPG = "asyncpg"


class CRUDOperationType(Enum):
    """CRUD operation types."""
    CREATE = "create"
    READ = "read"
    UPDATE = "update"
    DELETE = "delete"


class DatabaseConnection:
    """Database connection wrapper with type information."""

    def __init__(self, connection: Any, db_type: DatabaseType, config: Any):
        self.connection = connection
        self.db_type = db_type
        self.config = config

    async def connect(self) -> None:
        """Establish database connection."""
        # Connection is already established in the constructor
        pass

    async def disconnect(self) -> None:
        """Close database connection."""
        # Connection cleanup is handled by the connection manager
        pass

    async def is_connected(self) -> bool:
        """Check if connection is active."""
        return self.connection is not None

    async def execute_query(self, query: str, params: dict[str, Any] | None = None) -> Any:
        """Execute a database query."""
        # Implementation depends on the database type
        raise NotImplementedError("Query execution is handled by specific database integrations")


class CacheManager(ABC):
    """Abstract base class for cache managers."""

    @abstractmethod
    async def get(self, key: str, tenant_id: str) -> Any | None:
        """Get cached value with tenant isolation."""
        pass

    @abstractmethod
    async def set(self, key: str, value: Any, tenant_id: str, ttl: int | None = None) -> None:
        """Set cached value with tenant isolation."""
        pass

    @abstractmethod
    async def delete(self, key: str, tenant_id: str) -> None:
        """Delete cached value."""
        pass

    @abstractmethod
    async def invalidate_pattern(self, pattern: str, tenant_id: str) -> None:
        """Invalidate cache entries matching pattern."""
        pass


class DataValidator(ABC):
    """Abstract base class for data validators."""

    @abstractmethod
    def validate_create(self, data: dict[str, Any], model_name: str) -> dict[str, Any]:
        """Validate data for create operations."""
        pass

    @abstractmethod
    def validate_update(self, data: dict[str, Any], model_name: str) -> dict[str, Any]:
        """Validate data for update operations."""
        pass

    @abstractmethod
    def validate_tenant_isolation(self, data: dict[str, Any], tenant_id: str) -> bool:
        """Validate tenant isolation requirements."""
        pass


class WorkflowIntegration(ABC):
    """Abstract base class for workflow integrations."""

    @abstractmethod
    async def save_packet(self, packet_data: dict[str, Any], validation_hash: str) -> str:
        """Save workflow packet with validation."""
        pass

    @abstractmethod
    async def get_rules(self, rule_type: str, context: dict[str, Any]) -> list[dict[str, Any]]:
        """Retrieve workflow rules based on context."""
        pass


class DALServiceInterface(ABC):
    """Abstract base class for DAL services."""

    @abstractmethod
    async def initialize(self) -> None:
        """Initialize the DAL service and all connections."""
        pass

    @abstractmethod
    async def shutdown(self) -> None:
        """Shutdown the DAL service and cleanup resources."""
        pass

    @abstractmethod
    async def get_connection(self, preferred_type: DatabaseType = DatabaseType.PRISMA) -> DatabaseConnection:
        """Get database connection with fallback logic."""
        pass

    @abstractmethod
    async def execute_crud(self, operation: CRUDOperation) -> CRUDResult:
        """Execute CRUD operation with caching and validation."""
        pass

    @abstractmethod
    async def health_check(self) -> dict[str, Any]:
        """Perform health check on all components."""
        pass


class ConfigurationLoader(ABC):
    """Abstract base class for configuration loaders."""

    @abstractmethod
    def load_config(self, config_path: str) -> DALConfig:
        """Load configuration from file."""
        pass

    @abstractmethod
    def validate_config(self, config: DALConfig) -> bool:
        """Validate configuration structure and values."""
        pass

    @abstractmethod
    def get_connection_string(self, db_type: DatabaseType) -> str:
        """Get connection string for specified database type."""
        pass


class SchemaGenerator(ABC):
    """Abstract base class for schema generators."""

    @abstractmethod
    def generate_prisma_schema(self, metadata: dict[str, Any], output_path: str) -> str:
        """Generate Prisma schema from metadata."""
        pass

    @abstractmethod
    def generate_crud_operations(self, metadata: dict[str, Any], output_path: str) -> str:
        """Generate CRUD operations from metadata."""
        pass

    @abstractmethod
    def validate_metadata(self, metadata: dict[str, Any]) -> bool:
        """Validate metadata structure."""
        pass


class MigrationManager(ABC):
    """Abstract base class for migration managers."""

    @abstractmethod
    async def run_migrations(self, service_dir: str) -> None:
        """Run database migrations."""
        pass

    @abstractmethod
    async def generate_migration(self, service_dir: str, migration_name: str) -> str:
        """Generate new migration file."""
        pass

    @abstractmethod
    async def rollback_migration(self, service_dir: str, migration_id: str) -> None:
        """Rollback specific migration."""
        pass


class CRUDOperation(BaseModel):
    """Model for CRUD operation requests."""

    operation_type: Literal["create", "read", "update", "delete"]
    table_name: str
    data: dict[str, Any] | None = None
    tenant_id: str
    user_id: str | None = None
    filters: dict[str, Any] | None = None
    options: dict[str, Any] | None = None

    model_config = ConfigDict(validate_assignment=True)


class CRUDResult(BaseModel):
    """Model for CRUD operation results."""

    success: bool
    data: Any | None = None
    error: str | None = None
    operation_type: str
    from_cache: bool = False
    execution_time: float | None = None
    affected_rows: int | None = None

    model_config = ConfigDict(validate_assignment=True)
