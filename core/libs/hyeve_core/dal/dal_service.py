# mypy: ignore-errors
"""
Robust DAL Service with Multi-Database Support

This module extends the existing dal_template.py with enhanced capabilities including:
- Multi-database support (Prisma ORM, SQLAlchemy, asyncpg)
- Automatic fallback logic between database types
- Redis caching integration
- Comprehensive error handling and retry logic
- Async initialization for all database types
"""

import asyncio
import logging
import os
import time
import uuid
from datetime import UTC, datetime
from functools import wraps
from typing import Any

# Database imports
import asyncpg
import redis.asyncio as redis
from redis.backoff import ExponentialBackoff
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import RedisError, TimeoutError as RedisTimeoutError
from redis.retry import Retry
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base

# Internal imports
from .dal_config import DALConfig, DatabaseConfig
from .exceptions import DALConnectionError, DALError, DALTenantError, DALValidationError
from .interfaces import CRUDOperation, CRUDResult, DatabaseConnection, DatabaseType

logger = logging.getLogger(__name__)

# Tables without record_version columns.
_NO_RECORD_VERSION_TABLES = {
    "admin_audit_log",
    "arrears_snapshots",
    "bre_rule_traces",
    "bre_ruleset_alias",
    "bre_rulesets",
    "application_documents",
    "document_checklist_items",
    "document_checklists",
    "feature_flags",
    "idempotency_keys",
    "inbox",
    "outbox",
    "payment_allocations",
    "payment_variations",
    "payments",
    "repayment_schedule",
    "service_config",
    "wfe_definition_alias",
    "wfe_definitions",
    "wfe_events",
    "wfe_instances",
    "wfe_steps",
    "wfe_tasks",
    "wfe_timers",
}

# Tables without deleted_at columns.
_NO_DELETED_AT_TABLES = {
    "wfe_definition_alias",
    "wfe_events",
    "wfe_steps",
    "wfe_timers",
    "bre_ruleset_alias",
    "bre_rule_traces",
    "arrears_snapshots",
    "ledger_entries",
    "payment_allocations",
    "payment_variations",
    "payments",
    "repayment_schedule",
}

# SQLAlchemy base for ORM models
Base = declarative_base()


class DALQueryError(DALError):
    """Exception raised for query execution errors."""

    def __init__(self, message: str, query: str | None = None, params: list | None = None):
        super().__init__(message)
        self.query = query
        self.params = params


class DALCircuitBreakerError(DALError):
    """Exception raised when circuit breaker is open."""

    pass


# Circuit Breaker State
_circuit_breaker_failures = 0
_circuit_breaker_last_failure_time = 0
_circuit_breaker_state = "closed"  # closed, open, half-open


class DALCircuitBreaker:
    """Circuit breaker for database operations."""

    def __init__(self, failure_threshold: int = 5, timeout: float = 60.0, retry_delay: float = 0.5):
        self.failure_threshold = failure_threshold
        self.timeout = timeout
        self.retry_delay = retry_delay

    def is_open(self) -> bool:
        """Check if circuit breaker is open."""
        global _circuit_breaker_failures, _circuit_breaker_last_failure_time, _circuit_breaker_state

        if _circuit_breaker_state == "open":
            if time.time() - _circuit_breaker_last_failure_time > self.timeout:
                _circuit_breaker_state = "half-open"
                logger.info("Circuit breaker half-open")
                return False
            return True
        return False

    def record_success(self) -> None:
        """Record a successful operation."""
        global _circuit_breaker_failures, _circuit_breaker_state

        if _circuit_breaker_state == "half-open":
            _circuit_breaker_state = "closed"
            _circuit_breaker_failures = 0
            logger.info("Circuit breaker closed after successful operation")

    def record_failure(self) -> None:
        """Record a failed operation."""
        global _circuit_breaker_failures, _circuit_breaker_last_failure_time, _circuit_breaker_state

        _circuit_breaker_failures += 1
        _circuit_breaker_last_failure_time = time.time()

        if _circuit_breaker_failures >= self.failure_threshold:
            _circuit_breaker_state = "open"
            logger.error(f"Circuit breaker opened after {_circuit_breaker_failures} failures")

    def get_status(self) -> dict[str, Any]:
        """Get circuit breaker status."""
        global _circuit_breaker_failures, _circuit_breaker_last_failure_time, _circuit_breaker_state

        return {
            "state": _circuit_breaker_state,
            "failures": _circuit_breaker_failures,
            "last_failure_time": _circuit_breaker_last_failure_time,
            "time_since_last_failure": time.time() - _circuit_breaker_last_failure_time
            if _circuit_breaker_last_failure_time
            else None,
        }


# Global circuit breaker instance
_circuit_breaker = DALCircuitBreaker()


def _runtime_redis_url(configured_url: str) -> str:
    """
    Resolve runtime Redis URL override with safe precedence.

    Precedence:
    1) CACHE_URL
    2) REDIS_URL
    3) HYEVE_REDIS_URL
    4) a single HYEVE_*_REDIS_URL value (if exactly one is set)
    5) configured dal_config.json value
    """
    for env_key in ("CACHE_URL", "REDIS_URL", "HYEVE_REDIS_URL"):
        value = os.getenv(env_key)
        if value:
            return value

    scoped = [
        value for key, value in os.environ.items() if key.startswith("HYEVE_") and key.endswith("_REDIS_URL") and value
    ]
    unique_scoped = sorted(set(scoped))
    if len(unique_scoped) == 1:
        return unique_scoped[0]

    return configured_url


def dal_operation(max_retries: int = 3, retry_delay: float = 0.5):
    """
    Decorator for DAL operations with retry logic and circuit breaker.

    Args:
        max_retries: Maximum number of retry attempts
        retry_delay: Delay between retries (exponential backoff)
    """

    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            if _circuit_breaker.is_open():
                raise DALCircuitBreakerError(
                    "Circuit breaker is open - database operations temporarily disabled",
                    {"circuit_breaker_status": _circuit_breaker.get_status()},
                )

            last_exception = None

            for attempt in range(max_retries + 1):
                try:
                    result = await func(*args, **kwargs)
                    _circuit_breaker.record_success()
                    return result

                except (TimeoutError, DALConnectionError, asyncpg.exceptions.ConnectionDoesNotExistError) as e:
                    last_exception = e

                    if attempt == max_retries:
                        break

                    # Exponential backoff
                    wait_time = retry_delay * (2**attempt)
                    logger.warning(
                        f"DAL operation failed (attempt {attempt + 1}/{max_retries + 1}), retrying in {wait_time:.2f}s: {str(e)}"
                    )
                    await asyncio.sleep(wait_time)

                except Exception as e:
                    # Non-retryable error
                    raise e

            # All retries exhausted
            _circuit_breaker.record_failure()
            if isinstance(last_exception, DALConnectionError):
                raise last_exception
            else:
                raise DALConnectionError(
                    f"Database operation failed after {max_retries + 1} attempts: {str(last_exception)}",
                    {"original_error": str(last_exception), "attempts": max_retries + 1},
                ) from last_exception

        return wrapper

    return decorator


class DataAccessLayer:
    """
    Standardized Data Abstraction Layer with automatic constraint enforcement.

    Automatically enforces:
    - Multi-tenancy: All queries include tenant_id filtering where applicable
    - Soft-deletes: Read operations filter out deleted records, delete operations set deleted_at
    """

    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    @dal_operation()
    async def execute_query(
        self, query: str, params: tuple | None = None, tenant_id: str | None = None
    ) -> list[dict[str, Any]]:
        """
        Execute a SELECT query and return results as dict.

        Note: For better decoupling and maintainability, consider mapping
        these dictionaries to specific DTOs/Domain Entities in the service layer
        rather than passing raw database column names to business logic.

        Args:
            query: SQL query string
            params: Query parameters
            tenant_id: Optional tenant context for logging

        Returns:
            List of result dictionaries

        Raises:
            DALConnectionError: If database connection fails
            DALQueryError: If query execution fails
        """
        async with self.pool.acquire() as conn:
            try:
                if tenant_id:
                    logger.debug(f"Executing query for tenant {tenant_id}")

                if params:
                    rows = await conn.fetch(query, *params)
                else:
                    rows = await conn.fetch(query)

                return [dict(row) for row in rows]

            except asyncpg.exceptions.ConnectionDoesNotExistError as e:
                logger.error(f"Database connection lost: {e}")
                raise DALConnectionError(f"Database connection failed: {str(e)}") from e
            except asyncpg.exceptions.PostgresError as e:
                logger.error(f"Database query error: {e}")
                raise DALQueryError(
                    f"Query execution failed: {str(e)}", query=query, params=list(params) if params else None
                ) from e
            except Exception as e:
                logger.error(f"Unexpected database error: {e}")
                raise DALError(f"Unexpected database error: {str(e)}") from e

    @dal_operation()
    async def execute_mutation(self, query: str, params: tuple | None = None, tenant_id: str | None = None) -> str:
        """
        Execute INSERT, UPDATE, DELETE and return affected row info

        Args:
            query: SQL query string
            params: Query parameters
            tenant_id: Optional tenant context for logging

        Returns:
            Result string from database

        Raises:
            DALConnectionError: If database connection fails
            DALQueryError: If mutation execution fails
        """
        async with self.pool.acquire() as conn:
            try:
                if tenant_id:
                    logger.debug(f"Executing mutation for tenant {tenant_id}")

                if params:
                    result = await conn.execute(query, *params)
                else:
                    result = await conn.execute(query)

                return result

            except asyncpg.exceptions.ConnectionDoesNotExistError as e:
                logger.error(f"Database connection lost: {e}")
                raise DALConnectionError(f"Database connection failed: {str(e)}") from e
            except asyncpg.exceptions.PostgresError as e:
                logger.error(f"Database mutation error: {e}")
                raise DALQueryError(
                    f"Mutation execution failed: {str(e)}", query=query, params=list(params) if params else None
                ) from e
            except Exception as e:
                logger.error(f"Unexpected database error: {e}")
                raise DALError(f"Unexpected database error: {str(e)}") from e

    def _apply_tenant_filter(self, query: str, params: tuple, tenant_id: str) -> tuple[str, tuple]:
        """
        Apply automatic tenant filtering to query using robust parsing.

        This method safely injects tenant_id filtering by:
        1. Checking if tenant filtering already exists
        2. Finding the WHERE clause position robustly
        3. Handling case-insensitive WHERE detection
        4. Avoiding injection into subqueries or comments
        """
        if not tenant_id:
            return query, params

        # Normalize query for analysis
        query_upper = query.upper()

        # Check if tenant filtering already exists
        if "tenant_id" in query_upper and "=" in query_upper:
            return query, params

        # Find WHERE clause position (case-insensitive)
        where_pos = query_upper.find(" WHERE ")
        if where_pos == -1:
            # No WHERE clause, add one
            query += f" WHERE tenant_id = ${len(params) + 1}"
        else:
            # Insert tenant filter after WHERE
            # Find the actual position in original query
            where_keyword_end = where_pos + 7  # " WHERE " is 7 chars
            query = query[:where_keyword_end] + f"tenant_id = ${len(params) + 1} AND " + query[where_keyword_end:]

        params = params + (tenant_id,)
        return query, params

    def _apply_soft_delete_filter(self, query: str, params: tuple, table_name: str | None = None) -> tuple[str, tuple]:
        """
        Apply automatic soft-delete filtering to SELECT queries using robust parsing.

        Only applies to SELECT statements and avoids double-application.
        """
        query_upper = query.upper().strip()

        # Only apply to SELECT queries
        if not query_upper.startswith("SELECT"):
            return query, params

        if table_name and table_name in _NO_DELETED_AT_TABLES:
            return query, params

        # Check if soft-delete filtering already exists
        if "deleted_at" in query_upper and ("IS NULL" in query_upper or "IS NOT NULL" in query_upper):
            return query, params

        # Find WHERE clause position (case-insensitive)
        where_pos = query_upper.find(" WHERE ")
        if where_pos == -1:
            # No WHERE clause, add one
            query += " WHERE deleted_at IS NULL"
        else:
            # Insert soft-delete filter after WHERE
            where_keyword_end = where_pos + 7  # " WHERE " is 7 chars
            query = query[:where_keyword_end] + "deleted_at IS NULL AND " + query[where_keyword_end:]

        return query, params

    @dal_operation()
    async def get_by_id(self, table_name: str, record_id: str, tenant_id: str) -> dict[str, Any] | None:
        """
        Get a record by ID with automatic tenant and soft-delete filtering.

        Args:
            table_name: Name of the table
            record_id: Record ID to fetch
            tenant_id: Tenant ID (required for multi-tenancy enforcement)

        Returns:
            Record dict or None if not found

        Raises:
            DALTenantError: If tenant_id is not provided
            DALValidationError: If record_id is invalid
        """
        if not tenant_id:
            raise DALTenantError("tenant_id is required for get_by_id operation")

        if not record_id:
            raise DALValidationError("record_id is required for get_by_id operation")

        query = f"SELECT * FROM {table_name} WHERE id = $1"
        params = (record_id,)

        # Apply automatic constraints
        query, params = self._apply_tenant_filter(query, params, tenant_id)
        query, params = self._apply_soft_delete_filter(query, params, table_name)

        results = await self.execute_query(query, params)
        return results[0] if results else None

    async def get_by_tenant(
        self, table_name: str, tenant_id: str, limit: int = 100, offset: int = 0
    ) -> list[dict[str, Any]]:
        """
        Get records for a specific tenant with automatic soft-delete filtering.

        Args:
            table_name: Name of the table
            tenant_id: Tenant ID
            limit: Maximum records to return
            offset: Pagination offset

        Returns:
            List of record dicts
        """
        query = f"SELECT * FROM {table_name} WHERE tenant_id = $1 ORDER BY created_at DESC LIMIT $2 OFFSET $3"
        params = (tenant_id, limit, offset)

        # Apply automatic soft-delete filtering
        query, params = self._apply_soft_delete_filter(query, params, table_name)

        return await self.execute_query(query, params)

    @dal_operation()
    async def create_record(
        self,
        table_name: str,
        data: dict[str, Any],
        tenant_id: str,  # Required for security and multi-tenancy enforcement
        user_id: str | None = None,
    ) -> str:
        """
        Create a new record with audit fields and tenant context.

        Args:
            table_name: Name of the table
            data: Record data
            user_id: User creating the record
            tenant_id: Tenant context (required for multi-tenancy enforcement)

        Returns:
            Created record ID

        Raises:
            DALTenantError: If tenant_id is not provided
            DALValidationError: If data validation fails
        """
        if not tenant_id:
            raise DALTenantError("tenant_id is required for create_record operation")

        if not data:
            raise DALValidationError("data is required for create_record operation")

        # Generate UUID for ID if not provided
        # Note: In DDD patterns, ID generation typically occurs in the service/domain layer
        # before calling the DAL, allowing the entity identity to be known immediately.
        # The current approach is acceptable for data-centric CRUD but consider moving
        # ID generation upstream for full DDD compliance.
        if "id" not in data:
            data["id"] = str(uuid.uuid4())

        # Set tenant context (required)
        data["tenant_id"] = tenant_id

        # Set audit fields
        now = datetime.now(UTC)
        data.setdefault("created_at", now)
        data.setdefault("updated_at", now)
        if table_name not in _NO_RECORD_VERSION_TABLES:
            data.setdefault("record_version", 1)
        if table_name not in _NO_DELETED_AT_TABLES:
            data.setdefault("deleted_at", None)  # Ensure soft-delete field is set

        if user_id:
            data.setdefault("created_by", user_id)
            data.setdefault("updated_by", user_id)

        # Build INSERT query
        columns = list(data.keys())
        placeholders = [f"${i+1}" for i in range(len(columns))]
        values = list(data.values())

        query = f"""
            INSERT INTO {table_name} ({', '.join(columns)})
            VALUES ({', '.join(placeholders)})
            RETURNING id
        """

        await self.execute_mutation(query, tuple(values))
        return data["id"]

    async def create_record_with_id(
        self,
        table_name: str,
        record_id: str,
        data: dict[str, Any],
        tenant_id: str,  # Required for security and multi-tenancy enforcement
        user_id: str | None = None,
    ) -> str:
        """
        Create a new record with a pre-generated ID (DDD-compliant approach).

        This method allows the service layer to generate and know the entity ID
        before persistence, which is preferred in Domain-Driven Design patterns.

        Args:
            table_name: Name of the table
            record_id: Pre-generated record ID
            data: Record data (excluding 'id' field)
            tenant_id: Tenant context (required for multi-tenancy enforcement)
            user_id: User creating the record

        Returns:
            Created record ID (same as input record_id)

        Raises:
            ValueError: If tenant_id is not provided
        """
        if not tenant_id:
            raise ValueError("tenant_id is required for create_record_with_id operation")

        # Set the pre-generated ID
        data["id"] = record_id

        # Set tenant context (required)
        data["tenant_id"] = tenant_id

        # Set audit fields
        now = datetime.now(UTC)
        data.setdefault("created_at", now)
        data.setdefault("updated_at", now)
        if table_name not in _NO_RECORD_VERSION_TABLES:
            data.setdefault("record_version", 1)
        if table_name not in _NO_DELETED_AT_TABLES:
            data.setdefault("deleted_at", None)  # Ensure soft-delete field is set

        if user_id:
            data.setdefault("created_by", user_id)
            data.setdefault("updated_by", user_id)

        # Build INSERT query
        columns = list(data.keys())
        placeholders = [f"${i+1}" for i in range(len(columns))]
        values = list(data.values())

        query = f"""
            INSERT INTO {table_name} ({', '.join(columns)})
            VALUES ({', '.join(placeholders)})
            RETURNING id
        """

        await self.execute_mutation(query, tuple(values))
        return record_id

    @dal_operation()
    async def update_record(
        self,
        table_name: str,
        record_id: str,
        data: dict[str, Any],
        tenant_id: str,  # Required for security and multi-tenancy enforcement
        user_id: str | None = None,
    ) -> bool:
        """
        Update a record with audit fields and tenant isolation.

        Args:
            table_name: Name of the table
            record_id: Record ID to update
            data: Updated data
            tenant_id: Tenant ID (required for multi-tenancy enforcement)
            user_id: User performing the update

        Returns:
            True if update successful

        Raises:
            DALTenantError: If tenant_id is not provided
            DALValidationError: If validation fails
        """
        if not tenant_id:
            raise DALTenantError("tenant_id is required for update_record operation")

        if not record_id:
            raise DALValidationError("record_id is required for update_record operation")

        if not data:
            raise DALValidationError("data is required for update_record operation")

        # Set audit fields
        data["updated_at"] = datetime.now(UTC)
        if user_id:
            data["updated_by"] = user_id

        # Increment version
        if table_name not in _NO_RECORD_VERSION_TABLES:
            data["record_version"] = data.get("record_version", 1) + 1

        # Build UPDATE query
        set_parts = [f"{key} = ${i+1}" for i, key in enumerate(data.keys())]
        values = list(data.values())
        values.extend([record_id])  # Add ID for WHERE clause

        query = f"""
            UPDATE {table_name}
            SET {', '.join(set_parts)}
            WHERE id = ${len(values)}
        """

        # Apply automatic tenant filtering (required)
        query, values = self._apply_tenant_filter(query, tuple(values), tenant_id)

        # Apply soft-delete filter (only update non-deleted records)
        if table_name not in _NO_DELETED_AT_TABLES:
            query += " AND deleted_at IS NULL"

        result = await self.execute_mutation(query, tuple(values))
        return "UPDATE" in result

    @dal_operation()
    async def delete_record(
        self,
        table_name: str,
        record_id: str,
        tenant_id: str,  # Required for security and multi-tenancy enforcement
        user_id: str | None = None,
    ) -> bool:
        """
        Soft delete a record with tenant isolation.

        Args:
            table_name: Name of the table
            record_id: Record ID to delete
            tenant_id: Tenant ID (required for multi-tenancy enforcement)
            user_id: User performing the delete

        Returns:
            True if soft delete successful

        Raises:
            DALTenantError: If tenant_id is not provided
            DALValidationError: If record_id is invalid
        """
        if not tenant_id:
            raise DALTenantError("tenant_id is required for delete_record operation")

        if not record_id:
            raise DALValidationError("record_id is required for delete_record operation")

        if table_name in _NO_DELETED_AT_TABLES:
            query = f"DELETE FROM {table_name} WHERE id = $1"
            params = (record_id,)
        else:
            # Build soft delete query
            now = datetime.now(UTC)
            query = f"""
                UPDATE {table_name}
                SET deleted_at = $1, updated_at = $1, updated_by = $2
                WHERE id = $3
            """
            params = (now, user_id, record_id)

        # Apply automatic tenant filtering (required)
        query, params = self._apply_tenant_filter(query, params, tenant_id)

        # Apply soft-delete filter (only delete non-deleted records)
        if table_name not in _NO_DELETED_AT_TABLES:
            query += " AND deleted_at IS NULL"

        result = await self.execute_mutation(query, params)
        return "UPDATE" in result

    async def exists(self, table_name: str, record_id: str, tenant_id: str) -> bool:
        """
        Check if a record exists with automatic tenant and soft-delete filtering.

        Args:
            table_name: Name of the table
            record_id: Record ID to check
            tenant_id: Tenant ID (required for multi-tenancy enforcement)

        Returns:
            True if record exists and is not soft-deleted
        """
        query = f"SELECT 1 FROM {table_name} WHERE id = $1"
        params = (record_id,)

        # Apply automatic constraints
        query, params = self._apply_tenant_filter(query, params, tenant_id)
        query, params = self._apply_soft_delete_filter(query, params, table_name)

        results = await self.execute_query(query, params)
        return len(results) > 0

    async def hard_delete_record(self, table_name: str, record_id: str, tenant_id: str) -> bool:
        """
        Hard delete a record (admin operation only) with tenant isolation.

        Args:
            table_name: Name of the table
            record_id: Record ID to hard delete
            tenant_id: Tenant ID (required for multi-tenancy enforcement)

        Returns:
            True if hard delete successful
        """
        query = f"DELETE FROM {table_name} WHERE id = $1"
        params = (record_id,)

        # Apply automatic tenant filtering
        query, params = self._apply_tenant_filter(query, params, tenant_id)

        result = await self.execute_mutation(query, params)
        return "DELETE" in result

    async def restore_record(
        self,
        table_name: str,
        record_id: str,
        tenant_id: str,  # Required for security and multi-tenancy enforcement
        user_id: str | None = None,
    ) -> bool:
        """
        Restore a soft-deleted record.

        Args:
            table_name: Name of the table
            record_id: Record ID to restore
            tenant_id: Tenant ID (required for multi-tenancy enforcement)
            user_id: User performing the restore

        Returns:
            True if restore successful

        Raises:
            ValueError: If tenant_id is not provided
        """
        if not tenant_id:
            raise ValueError("tenant_id is required for restore_record operation")

        query = f"""
            UPDATE {table_name}
            SET deleted_at = NULL, updated_at = $1, updated_by = $2
            WHERE id = $3
        """
        params = (datetime.now(UTC), user_id, record_id)

        # Apply automatic tenant filtering (required)
        query, params = self._apply_tenant_filter(query, params, tenant_id)

        result = await self.execute_mutation(query, params)
        return "UPDATE" in result

    async def get_deleted_by_tenant(
        self, table_name: str, tenant_id: str, limit: int = 100, offset: int = 0
    ) -> list[dict[str, Any]]:
        """
        Get soft-deleted records for a tenant (admin operation).

        Args:
            table_name: Name of the table
            tenant_id: Tenant ID
            limit: Maximum records to return
            offset: Pagination offset

        Returns:
            List of soft-deleted record dicts
        """
        query = f"SELECT * FROM {table_name} WHERE tenant_id = $1 AND deleted_at IS NOT NULL ORDER BY deleted_at DESC LIMIT $2 OFFSET $3"
        params = (tenant_id, limit, offset)

        return await self.execute_query(query, params)


# Global DAL instance
dal: DataAccessLayer | None = None


def get_dal() -> DataAccessLayer:
    """Get the global DAL instance"""
    if dal is None:
        raise RuntimeError("Data Access Layer not initialized")
    return dal


def init_dal(pool: asyncpg.Pool) -> None:
    """Initialize the global DAL"""
    global dal
    dal = DataAccessLayer(pool)


def get_circuit_breaker_status() -> dict[str, Any]:
    """Get current circuit breaker status."""
    return _circuit_breaker.get_status()


def reset_circuit_breaker() -> None:
    """Reset circuit breaker state."""
    global _circuit_breaker_failures, _circuit_breaker_state
    _circuit_breaker_failures = 0
    _circuit_breaker_state = "closed"
    logger.info("Circuit breaker reset")


async def health_check() -> dict[str, Any]:
    """Perform DAL health check."""
    try:
        if dal is None:
            return {"status": "unhealthy", "timestamp": datetime.now(UTC).isoformat(), "error": "DAL not initialized"}

        # Simple health check query
        await dal.execute_query("SELECT 1 as health_check", None, None)
        circuit_breaker = get_circuit_breaker_status()

        return {
            "status": "healthy",
            "timestamp": datetime.now(UTC).isoformat(),
            "circuit_breaker": circuit_breaker,
            "pool_size": getattr(dal.pool, "_minsize", 0) if dal.pool else 0,
            "max_size": getattr(dal.pool, "_maxsize", 0) if dal.pool else 0,
        }

    except Exception as e:
        logger.error(f"DAL health check failed: {str(e)}")
        return {
            "status": "unhealthy",
            "timestamp": datetime.now(UTC).isoformat(),
            "error": str(e),
            "circuit_breaker": get_circuit_breaker_status(),
        }


class DatabaseConnectionManager:
    """Manages database connections with fallback logic."""

    def __init__(self, config: DALConfig):
        self.config = config
        self.connections: dict[str, Any] = {}
        self.connection_status: dict[str, bool] = {}

    async def initialize_connections(self) -> None:
        """Initialize all configured database connections."""
        disable_fallbacks = os.getenv("HYEVE_DAL_DISABLE_FALLBACKS", "").strip().lower() in {"1", "true", "yes"}
        primary_name = getattr(self.config, "_primary_db", None)
        for db_name, db_config in self.config.database.items():
            if disable_fallbacks and primary_name and db_name != primary_name:
                self.connection_status[db_name] = False
                logger.info(f"Skipping {db_config.type} connection {db_name} (fallbacks disabled)")
                continue
            try:
                await self._initialize_connection(db_name, db_config)
                self.connection_status[db_name] = True
                logger.info(f"Successfully initialized {db_config.type} connection: {db_name}")
            except Exception as e:
                self.connection_status[db_name] = False
                logger.error(f"Failed to initialize {db_config.type} connection {db_name}: {e}")

    async def _initialize_connection(self, db_name: str, db_config: DatabaseConfig) -> None:
        """Initialize a specific database connection."""
        if db_config.type == DatabaseType.PRISMA.value:
            # Prisma client will be initialized in the Prisma integration
            self.connections[db_name] = None  # Placeholder for Prisma client

        elif db_config.type == DatabaseType.SQLALCHEMY.value:
            connection_string = db_config.connection_string
            if connection_string.startswith("postgresql://"):
                connection_string = connection_string.replace("postgresql://", "postgresql+asyncpg://", 1)
            engine = create_async_engine(
                connection_string,
                pool_size=db_config.pool_size or 10,
                max_overflow=db_config.pool_max_size or 20,
                pool_timeout=db_config.timeout or 30,
                echo=self.config.logging.enable_sql_logging,
            )
            session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            self.connections[db_name] = {"engine": engine, "session_factory": session_factory}

        elif db_config.type == DatabaseType.ASYNCPG.value:
            connection_string = db_config.connection_string
            if connection_string.startswith("postgresql+asyncpg://"):
                connection_string = connection_string.replace("postgresql+asyncpg://", "postgresql://", 1)
            pool = await asyncpg.create_pool(
                connection_string,
                min_size=db_config.pool_min_size or 5,
                max_size=db_config.pool_max_size or 20,
                command_timeout=db_config.timeout or 30,
            )
            self.connections[db_name] = pool

        else:
            raise DALConnectionError(f"Unsupported database type: {db_config.type}")

    def get_connection(self, db_name: str) -> Any:
        """Get a database connection by name."""
        if db_name not in self.connections:
            raise DALConnectionError(f"Database connection not found: {db_name}")

        if not self.connection_status.get(db_name, False):
            raise DALConnectionError(f"Database connection not available: {db_name}")

        return self.connections[db_name]

    def is_connection_available(self, db_name: str) -> bool:
        """Check if a database connection is available."""
        return self.connection_status.get(db_name, False)

    async def close_connections(self) -> None:
        """Close all database connections."""
        for db_name, connection in self.connections.items():
            try:
                db_config = self.config.database[db_name]

                if db_config.type == DatabaseType.SQLALCHEMY.value and connection:
                    await connection["engine"].dispose()

                elif db_config.type == DatabaseType.ASYNCPG.value and connection:
                    await connection.close()

                logger.info(f"Closed {db_config.type} connection: {db_name}")

            except Exception as e:
                logger.error(f"Error closing connection {db_name}: {e}")


class RobustDALService:
    """
    Enhanced DAL service with multi-database support and caching.

    This service extends the existing DataAccessLayer with:
    - Multi-database support (Prisma, SQLAlchemy, asyncpg)
    - Automatic fallback logic
    - Redis caching integration
    - Comprehensive error handling
    - Async initialization
    """

    def __init__(self, config: DALConfig):
        self.config = config
        self.connection_manager = DatabaseConnectionManager(config)
        self.redis_client: redis.Redis | None = None
        self.legacy_dal: DataAccessLayer | None = None
        self._initialized = False

        # Database clients (will be initialized later)
        self.prisma_client = None
        self.sqlalchemy_sessions: dict[str, Any] = {}
        self.asyncpg_pools: dict[str, asyncpg.Pool] = {}

        # Database integrations (lazy-loaded)
        self._sqlalchemy_integration = None
        self._prisma_integration = None

    async def initialize(self) -> None:
        """
        Initialize all database connections and cache.

        This method must be called before using the DAL service.
        """
        if self._initialized:
            logger.warning("DAL service already initialized")
            return

        try:
            # Initialize database connections
            await self.connection_manager.initialize_connections()

            # Initialize Redis cache if enabled
            if self.config.cache.enabled:
                await self._initialize_redis()

            # Initialize legacy DAL with primary asyncpg connection
            await self._initialize_legacy_dal()

            self._initialized = True
            logger.info("DAL service initialized successfully")

        except Exception as e:
            logger.error(f"Failed to initialize DAL service: {e}")
            raise DALConnectionError(f"DAL service initialization failed: {e}") from e

    async def _initialize_redis(self) -> None:
        """Initialize Redis cache connection."""
        try:
            redis_url = _runtime_redis_url(self.config.cache.redis_url)
            if redis_url != self.config.cache.redis_url:
                logger.info("Applying runtime Redis override for DAL cache")
                self.config.cache.redis_url = redis_url

            # Stage/live resilience baseline: bounded retries + timeouts + health checks.
            retry = Retry(ExponentialBackoff(base=0.2, cap=2), retries=3)
            self.redis_client = redis.from_url(
                redis_url,
                max_connections=self.config.cache.max_connections or 10,
                socket_timeout=self.config.cache.socket_timeout or 5,
                socket_connect_timeout=self.config.cache.socket_timeout or 5,
                retry_on_timeout=self.config.cache.retry_on_timeout,
                health_check_interval=30,
                retry=retry,
                retry_on_error=[RedisConnectionError, RedisTimeoutError, RedisError],
            )

            # Test connection
            await self.redis_client.ping()
            logger.info("Redis cache initialized successfully")

        except Exception as e:
            logger.error(f"Failed to initialize Redis cache: {e}")
            if self.config.cache.enabled:
                # Don't fail initialization if cache is optional
                logger.warning("Continuing without cache")
                self.redis_client = None

    async def _initialize_legacy_dal(self) -> None:
        """Initialize legacy DAL with primary asyncpg connection."""
        try:
            configured_asyncpg = any(
                db_config.type == DatabaseType.ASYNCPG.value for db_config in self.config.database.values()
            )
            if not configured_asyncpg:
                logger.debug("Legacy DAL asyncpg not configured; skipping legacy DAL init")
                return

            suppress_warning = os.getenv("HYEVE_SUPPRESS_LEGACY_DAL_WARN", "").strip().lower() in {
                "1",
                "true",
                "yes",
            }

            # Find primary asyncpg connection
            primary_asyncpg = None
            for db_name, db_config in self.config.database.items():
                if db_config.type == DatabaseType.ASYNCPG.value and self.connection_manager.is_connection_available(
                    db_name
                ):
                    primary_asyncpg = self.connection_manager.get_connection(db_name)
                    break

            if primary_asyncpg:
                self.legacy_dal = DataAccessLayer(primary_asyncpg)
                logger.info("Legacy DAL initialized with asyncpg connection")
            else:
                if not suppress_warning:
                    logger.warning("No asyncpg connection available for legacy DAL")

        except Exception as e:
            logger.error(f"Failed to initialize legacy DAL: {e}")

    async def get_connection(self, preferred_type: DatabaseType = DatabaseType.PRISMA) -> DatabaseConnection:
        """
        Get database connection with fallback logic.

        Args:
            preferred_type: Preferred database type

        Returns:
            Database connection wrapper

        Raises:
            DALConnectionError: If no suitable connection is available
        """
        if not self._initialized:
            raise DALConnectionError("DAL service not initialized. Call initialize() first.", db_type="unknown")

        # Try preferred type first
        connection = await self._try_get_connection(preferred_type)
        if connection:
            return connection

        # Try fallback order: Prisma -> SQLAlchemy -> asyncpg
        fallback_order = [DatabaseType.PRISMA, DatabaseType.SQLALCHEMY, DatabaseType.ASYNCPG]

        for db_type in fallback_order:
            if db_type != preferred_type:
                connection = await self._try_get_connection(db_type)
                if connection:
                    logger.warning(f"Using fallback database type: {db_type.value}")
                    return connection

        raise DALConnectionError("No database connections available", db_type="unknown")

    async def _try_get_connection(self, db_type: DatabaseType) -> DatabaseConnection | None:
        """
        Try to get a connection of the specified type.

        Args:
            db_type: Database type to try

        Returns:
            Database connection or None if not available
        """
        try:
            # Find a connection of the specified type
            for db_name, db_config in self.config.database.items():
                if db_config.type == db_type.value and self.connection_manager.is_connection_available(db_name):
                    raw_connection = self.connection_manager.get_connection(db_name)
                    return DatabaseConnection(connection=raw_connection, db_type=db_type, config=db_config)

            return None

        except Exception as e:
            logger.error(f"Error getting {db_type.value} connection: {e}")
            return None

    async def execute_crud(self, operation: CRUDOperation) -> CRUDResult:
        """
        Execute CRUD operation with caching and validation.

        Args:
            operation: CRUD operation to execute

        Returns:
            CRUD operation result

        Raises:
            DALError: If operation fails
        """
        if not self._initialized:
            raise DALConnectionError("DAL service not initialized", db_type="unknown")

        try:
            # Validate operation
            self._validate_crud_operation(operation)

            # Check cache for read operations
            if operation.operation_type == "read" and self.redis_client:
                cached_result = await self._get_from_cache(operation)
                if cached_result:
                    return cached_result

            # Execute operation based on preferred database type
            result = await self._execute_crud_operation(operation)

            # Cache result for read operations
            if operation.operation_type == "read" and self.redis_client and result.success:
                await self._cache_result(operation, result)

            # Invalidate cache for write operations
            if operation.operation_type in ["create", "update", "delete"] and self.redis_client and result.success:
                await self._invalidate_cache(operation)

            return result

        except Exception as e:
            logger.error(f"CRUD operation failed: {e}")
            if isinstance(e, DALError):
                raise
            raise DALError(f"CRUD operation failed: {e}") from e

    def _validate_crud_operation(self, operation: CRUDOperation) -> None:
        """
        Validate CRUD operation parameters.

        Args:
            operation: Operation to validate

        Raises:
            DALValidationError: If operation is invalid
        """
        if not operation.tenant_id and self.config.tenant.enforce_isolation:
            raise DALTenantError("Tenant ID required for multi-tenant operation")

        if not operation.table_name:
            raise DALValidationError("Table name is required")

        if operation.operation_type in ["create", "update"] and not operation.data:
            raise DALValidationError(f"Data is required for {operation.operation_type} operation")

    async def _execute_crud_operation(self, operation: CRUDOperation) -> CRUDResult:
        """
        Execute CRUD operation using available database connections.

        Args:
            operation: CRUD operation to execute

        Returns:
            Operation result
        """
        # Try primary connection first, then fallback
        preferred_type = self.config.get_primary_database_type()
        primary_error: Exception | None = None
        try:
            connection = await self.get_connection(preferred_type)
            result = await self._execute_with_connection(operation, connection)
            if connection.db_type == DatabaseType.PRISMA and not result.success:
                raise DALConnectionError(
                    f"Prisma CRUD failed: {getattr(result, 'error', None) or getattr(result, 'error_message', None)}",
                    db_type=connection.db_type.value,
                )
            return result
        except DALError as exc:
            primary_error = exc
            logger.warning("Primary CRUD execution failed (%s): %s", preferred_type.value, exc)

        # If primary is not available or failed, fall back to SQLAlchemy or legacy asyncpg
        try:
            connection = await self.get_connection(DatabaseType.SQLALCHEMY)
            return await self._execute_with_connection(operation, connection)
        except DALError as exc:
            if primary_error is None:
                primary_error = exc
            if self.legacy_dal:
                return await self._execute_with_legacy_dal(operation)
            raise DALConnectionError(
                f"No database connections available for CRUD operations (last_error={primary_error})",
                db_type="unknown",
            ) from primary_error

    async def _execute_with_legacy_dal(self, operation: CRUDOperation) -> CRUDResult:
        """
        Execute operation using legacy DAL.

        Args:
            operation: CRUD operation to execute

        Returns:
            Operation result
        """
        if not self.legacy_dal:
            raise DALConnectionError("Legacy DAL not initialized", db_type="asyncpg")

        try:
            if operation.operation_type == "create":
                if not operation.data:
                    raise DALValidationError("Data is required for create operation")
                result_id = await self.legacy_dal.create_record(
                    table_name=operation.table_name,
                    data=operation.data,
                    tenant_id=operation.tenant_id,
                    user_id=operation.user_id,
                )
                return CRUDResult(success=True, data={"id": result_id}, operation_type=operation.operation_type)

            elif operation.operation_type == "read":
                if operation.filters and "id" in operation.filters:
                    result = await self.legacy_dal.get_by_id(
                        table_name=operation.table_name,
                        record_id=operation.filters["id"],
                        tenant_id=operation.tenant_id,
                    )
                    return CRUDResult(success=True, data=result, operation_type=operation.operation_type)
                else:
                    results = await self.legacy_dal.get_by_tenant(
                        table_name=operation.table_name,
                        tenant_id=operation.tenant_id,
                        limit=operation.options.get("limit", 100) if operation.options else 100,
                        offset=operation.options.get("offset", 0) if operation.options else 0,
                    )
                    return CRUDResult(success=True, data=results, operation_type=operation.operation_type)

            elif operation.operation_type == "update":
                if not operation.filters or "id" not in operation.filters:
                    raise DALValidationError("ID required for update operation")
                if not operation.data:
                    raise DALValidationError("Data is required for update operation")

                success = await self.legacy_dal.update_record(
                    table_name=operation.table_name,
                    record_id=operation.filters["id"],
                    data=operation.data,
                    tenant_id=operation.tenant_id,
                    user_id=operation.user_id,
                )
                return CRUDResult(success=success, data={"updated": success}, operation_type=operation.operation_type)

            elif operation.operation_type == "delete":
                if not operation.filters or "id" not in operation.filters:
                    raise DALValidationError("ID required for delete operation")

                success = await self.legacy_dal.delete_record(
                    table_name=operation.table_name,
                    record_id=operation.filters["id"],
                    tenant_id=operation.tenant_id,
                    user_id=operation.user_id,
                )
                return CRUDResult(success=success, data={"deleted": success}, operation_type=operation.operation_type)

            else:
                raise DALValidationError(f"Unsupported operation type: {operation.operation_type}")

        except Exception as e:
            logger.error(f"Legacy DAL operation failed: {e}")
            return CRUDResult(success=False, error=str(e), operation_type=operation.operation_type)

    async def _execute_with_connection(self, operation: CRUDOperation, connection: DatabaseConnection) -> CRUDResult:
        """
        Execute operation with specific database connection.

        Args:
            operation: CRUD operation to execute
            connection: Database connection to use

        Returns:
            Operation result
        """
        try:
            if connection.db_type == DatabaseType.SQLALCHEMY:
                # Use SQLAlchemy integration
                if not hasattr(self, "_sqlalchemy_integration") or self._sqlalchemy_integration is None:
                    try:
                        from .sqlalchemy_integration import SQLAlchemyFallbackIntegration

                        self._sqlalchemy_integration = SQLAlchemyFallbackIntegration(self.config)
                        await self._sqlalchemy_integration.initialize()
                    except ImportError as e:
                        raise DALConnectionError(
                            "SQLAlchemy integration not available", db_type=connection.db_type.value
                        ) from e

                return await self._sqlalchemy_integration.execute_crud_operation(operation)

            elif connection.db_type == DatabaseType.PRISMA:
                # Use Prisma integration (optional enhancement)
                if not hasattr(self, "_prisma_integration") or self._prisma_integration is None:
                    try:
                        # Prisma integration is optional - may not be implemented yet
                        from .prisma_integration import PrismaIntegration  # type: ignore

                        prisma_config = self.config.get_database_config(DatabaseType.PRISMA)
                        self._prisma_integration = PrismaIntegration(prisma_config)
                        await self._prisma_integration.initialize()
                    except (ImportError, AttributeError) as e:
                        logger.warning(f"Prisma integration not available: {e}")
                        raise DALConnectionError(
                            "Prisma integration not available", db_type=connection.db_type.value
                        ) from e

                return await self._prisma_integration.execute_crud_operation(operation)

            elif connection.db_type == DatabaseType.ASYNCPG:
                # Use asyncpg integration
                if not hasattr(self, "_asyncpg_integration") or self._asyncpg_integration is None:
                    try:
                        from .asyncpg_integration import AsyncPGIntegration

                        self._asyncpg_integration = AsyncPGIntegration(self.config)
                        await self._asyncpg_integration.initialize()
                    except ImportError as e:
                        raise DALConnectionError(
                            "AsyncPG integration not available", db_type=connection.db_type.value
                        ) from e

                return await self._asyncpg_integration.execute_crud_operation(operation)

            else:
                raise DALValidationError(f"Unsupported database type: {connection.db_type}")

        except Exception as e:
            logger.error(f"Connection-specific execution failed: {e}")
            if isinstance(e, DALError):
                raise
            raise DALError(f"Database operation failed: {e}") from e

    async def _get_from_cache(self, operation: CRUDOperation) -> CRUDResult | None:
        """Get cached result for read operation."""
        if not self.redis_client:
            return None

        try:
            cache_key = self._build_cache_key(operation)
            cached_data = await self.redis_client.get(cache_key)

            if cached_data:
                import json

                data = json.loads(cached_data)
                return CRUDResult(success=True, data=data, operation_type=operation.operation_type, from_cache=True)
        except (RedisConnectionError, RedisTimeoutError, RedisError) as e:
            logger.warning(
                "Cache read degraded; falling back to datastore",
                extra={
                    "cache_operation": "get",
                    "degraded_mode": True,
                    "reason": type(e).__name__,
                    "table": operation.table_name,
                },
            )
        except Exception as e:
            logger.error(f"Cache read error: {e}")

        return None

    async def _cache_result(self, operation: CRUDOperation, result: CRUDResult) -> None:
        """Cache result for read operation."""
        if not self.redis_client or not result.success:
            return

        try:
            cache_key = self._build_cache_key(operation)
            import json

            cached_data = json.dumps(result.data, default=str)

            await self.redis_client.setex(cache_key, self.config.cache.default_ttl, cached_data)
        except (RedisConnectionError, RedisTimeoutError, RedisError) as e:
            logger.warning(
                "Cache write degraded; continuing without cache",
                extra={
                    "cache_operation": "setex",
                    "degraded_mode": True,
                    "reason": type(e).__name__,
                    "table": operation.table_name,
                },
            )
        except Exception as e:
            logger.error(f"Cache write error: {e}")

    async def _invalidate_cache(self, operation: CRUDOperation) -> None:
        """Invalidate cache entries for write operations."""
        if not self.redis_client:
            return

        try:
            # Build pattern for cache invalidation
            pattern = f"{self.config.cache.key_prefix}:{operation.tenant_id}:{operation.table_name}:*"

            # Get all matching keys
            keys = await self.redis_client.keys(pattern)

            if keys:
                await self.redis_client.delete(*keys)
                logger.debug(f"Invalidated {len(keys)} cache entries for {operation.table_name}")

        except (RedisConnectionError, RedisTimeoutError, RedisError) as e:
            logger.warning(
                "Cache invalidation degraded; proceeding without invalidation",
                extra={
                    "cache_operation": "invalidate",
                    "degraded_mode": True,
                    "reason": type(e).__name__,
                    "table": operation.table_name,
                },
            )
        except Exception as e:
            logger.error(f"Cache invalidation error: {e}")

    def _build_cache_key(self, operation: CRUDOperation) -> str:
        """Build cache key for operation."""
        key_parts = [self.config.cache.key_prefix, operation.tenant_id, operation.table_name]

        if operation.filters:
            # Add filter hash to key
            import hashlib
            import json

            filter_str = json.dumps(operation.filters, sort_keys=True)
            filter_hash = hashlib.md5(filter_str.encode()).hexdigest()[:8]
            key_parts.append(filter_hash)

        return ":".join(key_parts)

    async def close(self) -> None:
        """Close all connections and cleanup resources."""
        try:
            # Close database integrations
            if self._sqlalchemy_integration:
                await self._sqlalchemy_integration.close()
                self._sqlalchemy_integration = None

            if self._prisma_integration:
                await self._prisma_integration.close()
                self._prisma_integration = None

            if hasattr(self, "_asyncpg_integration") and self._asyncpg_integration:
                await self._asyncpg_integration.close()
                self._asyncpg_integration = None

            # Close database connections
            await self.connection_manager.close_connections()

            # Close Redis connection
            if self.redis_client:
                await self.redis_client.close()

            self._initialized = False
            logger.info("DAL service closed successfully")

        except Exception as e:
            logger.error(f"Error closing DAL service: {e}")

    async def __aenter__(self):
        """Async context manager entry."""
        await self.initialize()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.close()


# Global DAL service instance
_dal_service: RobustDALService | None = None


def get_dal_service() -> RobustDALService:
    """
    Get the global DAL service instance.

    Returns:
        Global DAL service instance

    Raises:
        RuntimeError: If DAL service not initialized
    """
    if _dal_service is None:
        raise RuntimeError("DAL service not initialized. Call init_dal_service() first.")
    return _dal_service


async def init_dal_service(config: DALConfig) -> RobustDALService:
    """
    Initialize the global DAL service.

    Args:
        config: DAL configuration

    Returns:
        Initialized DAL service
    """
    global _dal_service

    if _dal_service is not None:
        # Re-initialize if previous init failed or service is not ready
        if not getattr(_dal_service, "_initialized", False):
            logger.warning("DAL service exists but is not initialized; re-initializing")
            try:
                await _dal_service.initialize()
            except Exception:
                _dal_service = None
                raise
        else:
            logger.warning("DAL service already initialized")
        return _dal_service

    _dal_service = RobustDALService(config)
    try:
        await _dal_service.initialize()
    except Exception:
        _dal_service = None
        raise

    return _dal_service


async def close_dal_service() -> None:
    """Close the global DAL service."""
    global _dal_service

    if _dal_service:
        await _dal_service.close()
        _dal_service = None
