# mypy: ignore-errors
"""
AsyncPG Raw SQL Integration

This module provides asyncpg integration for performance-critical operations
with connection pool management and monitoring capabilities.
"""

import logging
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import asyncpg
from asyncpg import Pool, Record
from asyncpg.exceptions import PostgresError

from .dal_config import DALConfig, DatabaseConfig
from .exceptions import DALConnectionError, DALError, DALTenantError, DALValidationError
from .interfaces import CRUDOperation, CRUDResult, DatabaseType

logger = logging.getLogger(__name__)


class AsyncPGConnectionPool:
    """
    AsyncPG connection pool manager with monitoring and health checks.
    """

    def __init__(self, config: DatabaseConfig, dal_config: DALConfig):
        self.config = config
        self.dal_config = dal_config
        self.pool: Pool | None = None
        self._pool_stats = {
            "total_connections": 0,
            "active_connections": 0,
            "idle_connections": 0,
            "queries_executed": 0,
            "slow_queries": 0,
            "connection_errors": 0,
            "last_health_check": None,
        }
        self._initialized = False

    async def initialize(self) -> None:
        """Initialize the connection pool."""
        if self._initialized:
            logger.warning("AsyncPG pool already initialized")
            return

        try:
            connection_string = self.config.connection_string
            if connection_string.startswith("postgresql+asyncpg://"):
                connection_string = connection_string.replace("postgresql+asyncpg://", "postgresql://", 1)
            self.pool = await asyncpg.create_pool(
                dsn=connection_string,
                min_size=self.config.pool_min_size or 5,
                max_size=self.config.pool_max_size or 20,
                command_timeout=self.config.timeout or 30,
                server_settings={"application_name": "hyeve_dal_asyncpg", "timezone": "UTC"},
            )

            self._pool_stats["total_connections"] = self.pool.get_size()
            self._initialized = True

            logger.info(f"AsyncPG pool initialized with {self.pool.get_size()} connections")

            # Perform initial health check
            await self.health_check()

        except Exception as e:
            logger.error(f"Failed to initialize AsyncPG pool: {e}")
            raise DALConnectionError(
                f"AsyncPG pool initialization failed: {e}", "asyncpg", self.config.connection_string
            )

    async def close(self) -> None:
        """Close the connection pool."""
        if self.pool:
            await self.pool.close()
            self.pool = None
            self._initialized = False
            logger.info("AsyncPG pool closed")

    @asynccontextmanager
    async def get_connection(self):
        """
        Get a connection from the pool with automatic cleanup.

        Yields:
            asyncpg.Connection: Database connection

        Raises:
            DALConnectionError: If connection cannot be acquired
        """
        if not self._initialized or not self.pool:
            raise DALConnectionError("AsyncPG pool not initialized", "asyncpg")

        connection = None
        start_time = time.time()

        try:
            connection = await self.pool.acquire()
            self._pool_stats["active_connections"] += 1

            yield connection

        except Exception as e:
            self._pool_stats["connection_errors"] += 1
            logger.error(f"Error with AsyncPG connection: {e}")
            raise DALConnectionError(f"Connection error: {e}", "asyncpg")

        finally:
            if connection:
                try:
                    await self.pool.release(connection)
                    self._pool_stats["active_connections"] -= 1
                except Exception as e:
                    logger.error(f"Error releasing connection: {e}")

            # Track connection usage time
            connection_time = time.time() - start_time
            if connection_time > self.dal_config.performance.slow_query_threshold:
                logger.warning(f"Long connection usage: {connection_time:.2f}s")

    async def execute_query(
        self, query: str, params: list | tuple | dict | None = None, fetch_mode: str = "all"
    ) -> list[Record] | Record | str | int:
        """
        Execute a SQL query with performance monitoring.

        Args:
            query: SQL query to execute
            params: Query parameters
            fetch_mode: How to fetch results ("all", "one", "val", "execute")

        Returns:
            Query results based on fetch_mode

        Raises:
            DALError: If query execution fails
        """
        if not self._initialized or not self.pool:
            raise DALConnectionError("AsyncPG pool not initialized", "asyncpg")

        start_time = time.time()

        try:
            async with self.get_connection() as conn:
                # Log query if enabled
                if self.dal_config.logging.enable_sql_logging:
                    logger.debug(f"Executing query: {query[:200]}...")

                # Execute query based on fetch mode
                if fetch_mode == "all":
                    result = await conn.fetch(query, *params if params else [])
                elif fetch_mode == "one":
                    result = await conn.fetchrow(query, *params if params else [])
                elif fetch_mode == "val":
                    result = await conn.fetchval(query, *params if params else [])
                elif fetch_mode == "execute":
                    result = await conn.execute(query, *params if params else [])
                else:
                    raise DALValidationError(f"Invalid fetch_mode: {fetch_mode}")

                # Update statistics
                execution_time = time.time() - start_time
                self._pool_stats["queries_executed"] += 1

                # Track slow queries
                if execution_time > self.dal_config.performance.slow_query_threshold:
                    self._pool_stats["slow_queries"] += 1
                    logger.warning(f"Slow query detected: {execution_time:.2f}s - {query[:100]}...")

                return result

        except PostgresError as e:
            logger.error(f"PostgreSQL error: {e}")
            raise DALError(f"Database error: {e}")
        except Exception as e:
            logger.error(f"Query execution error: {e}")
            raise DALError(f"Query execution failed: {e}")

    async def execute_transaction(self, queries: list[tuple[str, list | tuple | dict | None]]) -> list[Any]:
        """
        Execute multiple queries in a transaction.

        Args:
            queries: List of (query, params) tuples

        Returns:
            List of query results

        Raises:
            DALError: If transaction fails
        """
        if not self._initialized or not self.pool:
            raise DALConnectionError("AsyncPG pool not initialized", "asyncpg")

        start_time = time.time()
        results = []

        try:
            async with self.get_connection() as conn:
                async with conn.transaction():
                    for query, params in queries:
                        if self.dal_config.logging.enable_sql_logging:
                            logger.debug(f"Transaction query: {query[:200]}...")

                        result = await conn.fetch(query, *params if params else [])
                        results.append(result)

                # Update statistics
                execution_time = time.time() - start_time
                self._pool_stats["queries_executed"] += len(queries)

                if execution_time > self.dal_config.performance.slow_query_threshold:
                    self._pool_stats["slow_queries"] += 1
                    logger.warning(f"Slow transaction: {execution_time:.2f}s with {len(queries)} queries")

                return results

        except PostgresError as e:
            logger.error(f"Transaction PostgreSQL error: {e}")
            raise DALError(f"Transaction database error: {e}")
        except Exception as e:
            logger.error(f"Transaction execution error: {e}")
            raise DALError(f"Transaction failed: {e}")

    async def health_check(self) -> dict[str, Any]:
        """
        Perform health check on the connection pool.

        Returns:
            Health check results
        """
        health_status = {
            "status": "healthy",
            "pool_initialized": self._initialized,
            "pool_size": 0,
            "idle_connections": 0,
            "response_time": None,
            "last_check": datetime.now(UTC).isoformat(),
            "errors": [],
        }

        if not self._initialized or not self.pool:
            health_status["status"] = "unhealthy"
            health_status["errors"].append("Pool not initialized")
            return health_status

        try:
            start_time = time.time()

            # Test connection with simple query
            result = await self.execute_query("SELECT 1 as health_check", fetch_mode="val")

            response_time = time.time() - start_time
            health_status["response_time"] = response_time

            # Update pool statistics
            health_status["pool_size"] = self.pool.get_size()
            health_status["idle_connections"] = self.pool.get_idle_size()

            # Check if response time is acceptable
            if response_time > self.dal_config.performance.slow_query_threshold:
                health_status["status"] = "degraded"
                health_status["errors"].append(f"Slow response time: {response_time:.2f}s")

            # Verify health check result
            if result != 1:
                health_status["status"] = "unhealthy"
                health_status["errors"].append("Health check query returned unexpected result")

            self._pool_stats["last_health_check"] = datetime.now(UTC)

        except Exception as e:
            health_status["status"] = "unhealthy"
            health_status["errors"].append(f"Health check failed: {str(e)}")
            logger.error(f"AsyncPG health check failed: {e}")

        return health_status

    def get_pool_stats(self) -> dict[str, Any]:
        """
        Get connection pool statistics.

        Returns:
            Pool statistics
        """
        stats = self._pool_stats.copy()

        if self.pool:
            stats.update(
                {
                    "current_pool_size": self.pool.get_size(),
                    "current_idle_connections": self.pool.get_idle_size(),
                    "max_pool_size": self.config.pool_max_size or 20,
                    "min_pool_size": self.config.pool_min_size or 5,
                }
            )

        return stats


class AsyncPGIntegration:
    """
    AsyncPG integration for raw SQL operations with performance optimization.
    """

    def __init__(self, config: DALConfig):
        self.config = config
        self.pools: dict[str, AsyncPGConnectionPool] = {}
        self._initialized = False

    async def initialize(self) -> None:
        """Initialize all AsyncPG connection pools."""
        if self._initialized:
            logger.warning("AsyncPG integration already initialized")
            return

        try:
            # Initialize pools for all asyncpg database configurations
            for db_name, db_config in self.config.database.items():
                if db_config.type == DatabaseType.ASYNCPG.value:
                    pool = AsyncPGConnectionPool(db_config, self.config)
                    await pool.initialize()
                    self.pools[db_name] = pool
                    logger.info(f"Initialized AsyncPG pool: {db_name}")

            if not self.pools:
                logger.warning("No AsyncPG database configurations found")

            self._initialized = True
            logger.info("AsyncPG integration initialized successfully")

        except Exception as e:
            logger.error(f"Failed to initialize AsyncPG integration: {e}")
            raise DALConnectionError(f"AsyncPG integration initialization failed: {e}", "asyncpg")

    async def close(self) -> None:
        """Close all connection pools."""
        for pool_name, pool in self.pools.items():
            try:
                await pool.close()
                logger.info(f"Closed AsyncPG pool: {pool_name}")
            except Exception as e:
                logger.error(f"Error closing pool {pool_name}: {e}")

        self.pools.clear()
        self._initialized = False
        logger.info("AsyncPG integration closed")

    def get_pool(self, pool_name: str | None = None) -> AsyncPGConnectionPool:
        """
        Get connection pool by name or return primary pool.

        Args:
            pool_name: Name of the pool to get (optional)

        Returns:
            AsyncPG connection pool

        Raises:
            DALConnectionError: If pool not found
        """
        if not self._initialized:
            raise DALConnectionError("AsyncPG integration not initialized", "asyncpg")

        if not self.pools:
            raise DALConnectionError("No AsyncPG pools available", "asyncpg")

        if pool_name:
            if pool_name not in self.pools:
                raise DALConnectionError(f"AsyncPG pool not found: {pool_name}", "asyncpg")
            return self.pools[pool_name]

        # Return first available pool as primary
        return next(iter(self.pools.values()))

    async def execute_crud_operation(self, operation: CRUDOperation) -> CRUDResult:
        """
        Execute CRUD operation using raw SQL.

        Args:
            operation: CRUD operation to execute

        Returns:
            CRUD operation result
        """
        if not self._initialized:
            raise DALConnectionError("AsyncPG integration not initialized", "asyncpg")

        start_time = time.time()

        try:
            pool = self.get_pool()

            # Build SQL query based on operation type
            if operation.operation_type == "create":
                result = await self._execute_create(pool, operation)
            elif operation.operation_type == "read":
                result = await self._execute_read(pool, operation)
            elif operation.operation_type == "update":
                result = await self._execute_update(pool, operation)
            elif operation.operation_type == "delete":
                result = await self._execute_delete(pool, operation)
            else:
                raise DALValidationError(f"Unsupported operation type: {operation.operation_type}")

            execution_time = time.time() - start_time
            result.execution_time = execution_time

            return result

        except Exception as e:
            logger.error(f"AsyncPG CRUD operation failed: {e}")
            return CRUDResult(
                success=False,
                error=str(e),
                operation_type=operation.operation_type,
                execution_time=time.time() - start_time,
            )

    async def _execute_create(self, pool: AsyncPGConnectionPool, operation: CRUDOperation) -> CRUDResult:
        """Execute CREATE operation."""
        # Validate tenant isolation
        if self.config.tenant.enforce_isolation and not operation.tenant_id:
            raise DALTenantError("Tenant ID required for create operation")

        # Add tenant ID and audit fields to data
        data = operation.data.copy()
        data[self.config.tenant.tenant_id_column] = operation.tenant_id
        data["created_at"] = datetime.now(UTC)
        data["updated_at"] = datetime.now(UTC)

        if operation.user_id:
            data["created_by"] = operation.user_id
            data["updated_by"] = operation.user_id

        # Generate ULID for ID if not provided
        if "id" not in data:
            try:
                import ulid

                data["id"] = str(ulid.new())
            except ImportError:
                # Fallback to UUID if ULID not available
                import uuid

                data["id"] = str(uuid.uuid4())

        # Build INSERT query
        columns = list(data.keys())
        placeholders = [f"${i+1}" for i in range(len(columns))]
        values = [data[col] for col in columns]

        query = f"""
            INSERT INTO {operation.table_name} ({', '.join(columns)})
            VALUES ({', '.join(placeholders)})
            RETURNING id
        """

        try:
            result_id = await pool.execute_query(query, values, fetch_mode="val")

            return CRUDResult(
                success=True, data={"id": result_id}, operation_type=operation.operation_type, affected_rows=1
            )

        except Exception as e:
            logger.error(f"Create operation failed: {e}")
            raise DALError(f"Create operation failed: {e}")

    async def _execute_read(self, pool: AsyncPGConnectionPool, operation: CRUDOperation) -> CRUDResult:
        """Execute READ operation."""
        # Build WHERE clause with tenant isolation
        where_conditions = []
        params = []
        param_count = 0

        # Add tenant isolation
        if self.config.tenant.enforce_isolation and operation.tenant_id:
            param_count += 1
            where_conditions.append(f"{self.config.tenant.tenant_id_column} = ${param_count}")
            params.append(operation.tenant_id)

        # Add soft delete filter
        if self.config.tenant.soft_delete_column:
            where_conditions.append(f"{self.config.tenant.soft_delete_column} IS NULL")

        # Add custom filters
        if operation.filters:
            for key, value in operation.filters.items():
                param_count += 1
                where_conditions.append(f"{key} = ${param_count}")
                params.append(value)

        # Build query
        where_clause = " AND ".join(where_conditions) if where_conditions else "1=1"

        # Handle pagination
        limit_clause = ""
        if operation.options:
            if "limit" in operation.options:
                param_count += 1
                limit_clause += f" LIMIT ${param_count}"
                params.append(operation.options["limit"])

            if "offset" in operation.options:
                param_count += 1
                limit_clause += f" OFFSET ${param_count}"
                params.append(operation.options["offset"])

        query = f"""
            SELECT * FROM {operation.table_name}
            WHERE {where_clause}
            ORDER BY created_at DESC
            {limit_clause}
        """

        try:
            # Determine if we want one record or multiple
            if operation.filters and "id" in operation.filters and operation.options.get("single", False):
                result = await pool.execute_query(query, params, fetch_mode="one")
                data = dict(result) if result else None
            else:
                results = await pool.execute_query(query, params, fetch_mode="all")
                data = [dict(row) for row in results]

            return CRUDResult(
                success=True,
                data=data,
                operation_type=operation.operation_type,
                affected_rows=len(data) if isinstance(data, list) else (1 if data else 0),
            )

        except Exception as e:
            logger.error(f"Read operation failed: {e}")
            raise DALError(f"Read operation failed: {e}")

    async def _execute_update(self, pool: AsyncPGConnectionPool, operation: CRUDOperation) -> CRUDResult:
        """Execute UPDATE operation."""
        if not operation.filters or "id" not in operation.filters:
            raise DALValidationError("ID required for update operation")

        # Validate tenant isolation
        if self.config.tenant.enforce_isolation and not operation.tenant_id:
            raise DALTenantError("Tenant ID required for update operation")

        # Add audit fields to data
        data = operation.data.copy()
        data["updated_at"] = datetime.now(UTC)

        if operation.user_id:
            data["updated_by"] = operation.user_id

        # Build SET clause
        set_clauses = []
        params = []
        param_count = 0

        for key, value in data.items():
            param_count += 1
            set_clauses.append(f"{key} = ${param_count}")
            params.append(value)

        # Build WHERE clause
        where_conditions = []

        # Add ID filter
        param_count += 1
        where_conditions.append(f"id = ${param_count}")
        params.append(operation.filters["id"])

        # Add tenant isolation
        if self.config.tenant.enforce_isolation:
            param_count += 1
            where_conditions.append(f"{self.config.tenant.tenant_id_column} = ${param_count}")
            params.append(operation.tenant_id)

        # Add soft delete filter
        if self.config.tenant.soft_delete_column:
            where_conditions.append(f"{self.config.tenant.soft_delete_column} IS NULL")

        query = f"""
            UPDATE {operation.table_name}
            SET {', '.join(set_clauses)}
            WHERE {' AND '.join(where_conditions)}
            RETURNING id
        """

        try:
            result = await pool.execute_query(query, params, fetch_mode="one")
            success = result is not None

            return CRUDResult(
                success=success,
                data={"updated": success, "id": result["id"] if result else None},
                operation_type=operation.operation_type,
                affected_rows=1 if success else 0,
            )

        except Exception as e:
            logger.error(f"Update operation failed: {e}")
            raise DALError(f"Update operation failed: {e}")

    async def _execute_delete(self, pool: AsyncPGConnectionPool, operation: CRUDOperation) -> CRUDResult:
        """Execute DELETE operation (soft delete by default)."""
        if not operation.filters or "id" not in operation.filters:
            raise DALValidationError("ID required for delete operation")

        # Validate tenant isolation
        if self.config.tenant.enforce_isolation and not operation.tenant_id:
            raise DALTenantError("Tenant ID required for delete operation")

        # Build WHERE clause
        where_conditions = []
        params = []
        param_count = 0

        # Add ID filter
        param_count += 1
        where_conditions.append(f"id = ${param_count}")
        params.append(operation.filters["id"])

        # Add tenant isolation
        if self.config.tenant.enforce_isolation:
            param_count += 1
            where_conditions.append(f"{self.config.tenant.tenant_id_column} = ${param_count}")
            params.append(operation.tenant_id)

        # Add soft delete filter (only delete non-deleted records)
        if self.config.tenant.soft_delete_column:
            where_conditions.append(f"{self.config.tenant.soft_delete_column} IS NULL")

        # Determine if hard delete is requested
        hard_delete = operation.options and operation.options.get("hard_delete", False)

        if hard_delete:
            # Hard delete - actually remove the record
            query = f"""
                DELETE FROM {operation.table_name}
                WHERE {' AND '.join(where_conditions)}
                RETURNING id
            """
        else:
            # Soft delete - set deleted_at timestamp
            param_count += 1
            params.append(datetime.now(UTC))

            if operation.user_id:
                param_count += 1
                params.append(operation.user_id)
                set_clause = f"{self.config.tenant.soft_delete_column} = ${param_count-1}, updated_by = ${param_count}, updated_at = NOW()"
            else:
                set_clause = f"{self.config.tenant.soft_delete_column} = ${param_count}, updated_at = NOW()"

            query = f"""
                UPDATE {operation.table_name}
                SET {set_clause}
                WHERE {' AND '.join(where_conditions)}
                RETURNING id
            """

        try:
            result = await pool.execute_query(query, params, fetch_mode="one")
            success = result is not None

            return CRUDResult(
                success=success,
                data={"deleted": success, "id": result["id"] if result else None},
                operation_type=operation.operation_type,
                affected_rows=1 if success else 0,
            )

        except Exception as e:
            logger.error(f"Delete operation failed: {e}")
            raise DALError(f"Delete operation failed: {e}")

    async def execute_raw_query(
        self,
        query: str,
        params: list | tuple | dict | None = None,
        fetch_mode: str = "all",
        pool_name: str | None = None,
    ) -> Any:
        """
        Execute raw SQL query for performance-critical operations.

        Args:
            query: Raw SQL query
            params: Query parameters
            fetch_mode: How to fetch results
            pool_name: Specific pool to use (optional)

        Returns:
            Query results
        """
        if not self._initialized:
            raise DALConnectionError("AsyncPG integration not initialized", "asyncpg")

        pool = self.get_pool(pool_name)
        return await pool.execute_query(query, params, fetch_mode)

    async def execute_transaction(
        self, queries: list[tuple[str, list | tuple | dict | None]], pool_name: str | None = None
    ) -> list[Any]:
        """
        Execute multiple queries in a transaction.

        Args:
            queries: List of (query, params) tuples
            pool_name: Specific pool to use (optional)

        Returns:
            List of query results
        """
        if not self._initialized:
            raise DALConnectionError("AsyncPG integration not initialized", "asyncpg")

        pool = self.get_pool(pool_name)
        return await pool.execute_transaction(queries)

    async def health_check(self) -> dict[str, Any]:
        """
        Perform health check on all pools.

        Returns:
            Health check results for all pools
        """
        health_results = {
            "overall_status": "healthy",
            "pools": {},
            "total_pools": len(self.pools),
            "healthy_pools": 0,
            "unhealthy_pools": 0,
        }

        for pool_name, pool in self.pools.items():
            try:
                pool_health = await pool.health_check()
                health_results["pools"][pool_name] = pool_health

                if pool_health["status"] == "healthy":
                    health_results["healthy_pools"] += 1
                else:
                    health_results["unhealthy_pools"] += 1

            except Exception as e:
                health_results["pools"][pool_name] = {"status": "unhealthy", "error": str(e)}
                health_results["unhealthy_pools"] += 1

        # Determine overall status
        if health_results["unhealthy_pools"] > 0:
            if health_results["healthy_pools"] == 0:
                health_results["overall_status"] = "unhealthy"
            else:
                health_results["overall_status"] = "degraded"

        return health_results

    def get_pool_stats(self) -> dict[str, Any]:
        """
        Get statistics for all connection pools.

        Returns:
            Pool statistics for all pools
        """
        stats = {"total_pools": len(self.pools), "pools": {}}

        for pool_name, pool in self.pools.items():
            stats["pools"][pool_name] = pool.get_pool_stats()

        return stats
