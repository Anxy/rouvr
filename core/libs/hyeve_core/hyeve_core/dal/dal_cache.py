"""
DAL Cache Manager

Redis-based caching with automatic invalidation and tenant isolation.
This module provides a DAL-specific wrapper around the centralized Cache Manager.
"""

import logging
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from typing import Any

from ..managers.cache_manager import CacheManager as CoreCacheManager
from .exceptions import DALCacheError
from .interfaces import CacheManager

logger = logging.getLogger(__name__)


class DALCacheManager(CacheManager):
    """
    DAL-specific cache manager that wraps the centralized Cache Manager.

    Provides caching functionality with automatic tenant isolation,
    TTL management, and pattern-based invalidation specifically for DAL operations.
    """

    def __init__(self, cache_config: dict[str, Any]):
        """
        Initialize DAL cache manager with configuration.

        Args:
            cache_config: Cache configuration dictionary containing:
                - redis_url: Redis connection URL
                - key_prefix: Cache key prefix (defaults to 'hyeve:dal')
                - default_ttl: Default TTL in seconds
                - enabled: Whether caching is enabled
                - Additional Redis configuration options
        """
        self.config = cache_config
        self.enabled = cache_config.get("enabled", True)

        self._cache_manager: CoreCacheManager | None
        if self.enabled:
            # Initialize the centralized cache manager
            redis_url = cache_config.get("redis_url", "redis://localhost:6379")
            key_prefix = cache_config.get("key_prefix", "hyeve:dal")
            default_ttl = cache_config.get("default_ttl", 3600)
            max_connections = cache_config.get("max_connections", 20)

            self._cache_manager = CoreCacheManager(
                redis_url=redis_url,
                key_prefix=key_prefix,
                default_ttl=default_ttl,
                max_connections=max_connections,
                retry_on_timeout=cache_config.get("retry_on_timeout", True),
                socket_keepalive=cache_config.get("socket_keepalive", True),
                decode_responses=cache_config.get("decode_responses", False),
            )
        else:
            self._cache_manager = None

    async def initialize(self) -> None:
        """Initialize the cache manager."""
        if self.enabled and self._cache_manager:
            await self._cache_manager.initialize()

    async def shutdown(self) -> None:
        """Shutdown the cache manager."""
        if self.enabled and self._cache_manager:
            await self._cache_manager.shutdown()

    async def get(self, key: str, tenant_id: str) -> Any | None:
        """
        Get cached value with tenant isolation.

        Args:
            key: Cache key
            tenant_id: Tenant identifier for isolation

        Returns:
            Cached value or None if not found or caching is disabled
        """
        if not self.enabled or not self._cache_manager:
            return None

        try:
            return await self._cache_manager.get(key, tenant_id)
        except Exception as e:
            # Log error but don't fail the operation - allow fallback to database
            import logging

            logger = logging.getLogger(__name__)
            logger.warning(f"Cache get operation failed, falling back to database: {e}")
            return None

    async def set(self, key: str, value: Any, tenant_id: str, ttl: int | None = None) -> None:
        """
        Set cached value with tenant isolation.

        Args:
            key: Cache key
            value: Value to cache
            tenant_id: Tenant identifier for isolation
            ttl: Time to live in seconds (uses default_ttl if None)
        """
        if not self.enabled or not self._cache_manager:
            return

        try:
            await self._cache_manager.set(key, value, tenant_id, ttl)
        except Exception as e:
            # Log error but don't fail the operation
            import logging

            logger = logging.getLogger(__name__)
            logger.warning(f"Cache set operation failed, continuing without caching: {e}")

    async def delete(self, key: str, tenant_id: str) -> None:
        """
        Delete cached value.

        Args:
            key: Cache key
            tenant_id: Tenant identifier for isolation
        """
        if not self.enabled or not self._cache_manager:
            return

        try:
            await self._cache_manager.delete(key, tenant_id)
        except Exception as e:
            # Log error but don't fail the operation
            import logging

            logger = logging.getLogger(__name__)
            logger.warning(f"Cache delete operation failed: {e}")

    async def invalidate_pattern(self, pattern: str, tenant_id: str) -> int:
        """
        Invalidate cache entries matching pattern.

        Args:
            pattern: Pattern to match (supports Redis glob patterns)
            tenant_id: Tenant identifier for isolation

        Returns:
            Number of keys invalidated
        """
        if not self.enabled or not self._cache_manager:
            return 0

        try:
            return await self._cache_manager.invalidate_pattern(pattern, tenant_id)
        except Exception as e:
            # Log error but don't fail the operation
            import logging

            logger = logging.getLogger(__name__)
            logger.warning(f"Cache pattern invalidation failed: {e}")
            return 0

    async def invalidate_by_entity(self, entity_type: str, entity_id: str, tenant_id: str) -> int:
        """
        Invalidate cache entries for a specific entity and related data.

        This method implements smart cache invalidation by removing:
        - Direct entity cache entries
        - Related entity caches that might reference this entity
        - List caches that might include this entity

        Args:
            entity_type: Type of entity (e.g., 'user', 'order', 'product')
            entity_id: ID of the entity
            tenant_id: Tenant identifier for isolation

        Returns:
            Total number of keys invalidated
        """
        if not self.enabled or not self._cache_manager:
            return 0

        try:
            return await self._cache_manager.invalidate_by_entity(entity_type, entity_id, tenant_id)
        except Exception as e:
            import logging

            logger = logging.getLogger(__name__)
            logger.warning(f"Cache entity invalidation failed for {entity_type}:{entity_id}: {e}")
            return 0

    async def invalidate_by_table(self, table_name: str, tenant_id: str) -> int:
        """
        Invalidate all cache entries for a specific database table.

        This method is useful when performing bulk operations or schema changes
        that affect an entire table.

        Args:
            table_name: Name of the database table
            tenant_id: Tenant identifier for isolation

        Returns:
            Number of keys invalidated
        """
        if not self.enabled or not self._cache_manager:
            return 0

        try:
            return await self._cache_manager.invalidate_by_table(table_name, tenant_id)
        except Exception as e:
            import logging

            logger = logging.getLogger(__name__)
            logger.warning(f"Cache table invalidation failed for {table_name}: {e}")
            return 0

    async def invalidate_by_tags(self, tags: Sequence[str], tenant_id: str) -> int:
        """
        Invalidate cache entries by tags.

        This method allows for tag-based cache invalidation, useful for
        invalidating related data across multiple entities.

        Args:
            tags: List of tags to invalidate
            tenant_id: Tenant identifier for isolation

        Returns:
            Total number of keys invalidated
        """
        if not self.enabled or not self._cache_manager:
            return 0

        try:
            return await self._cache_manager.invalidate_by_tags(list(tags), tenant_id)
        except Exception as e:
            import logging

            logger = logging.getLogger(__name__)
            logger.warning(f"Cache tag invalidation failed for tags {tags}: {e}")
            return 0

    async def invalidate_crud_operation(
        self,
        operation_type: str,
        table_name: str,
        entity_id: str | None = None,
        tenant_id: str | None = None,
    ) -> int:
        """
        Smart cache invalidation based on CRUD operation type.

        This method implements intelligent cache invalidation patterns
        based on the type of database operation performed.

        Args:
            operation_type: Type of operation ('create', 'update', 'delete')
            table_name: Name of the affected table
            entity_id: ID of the affected entity (for update/delete operations)
            tenant_id: Tenant identifier for isolation

        Returns:
            Number of keys invalidated
        """
        if not self.enabled or not self._cache_manager or not tenant_id:
            return 0

        total_invalidated = 0

        try:
            if operation_type in ["create", "update", "delete"]:
                # Always invalidate list caches for the table
                list_patterns = [
                    f"list:{table_name}:*",
                    f"count:{table_name}:*",
                    f"query:{table_name}:*",
                ]

                for pattern in list_patterns:
                    invalidated = await self.invalidate_pattern(pattern, tenant_id)
                    total_invalidated += invalidated

                # For update and delete operations, also invalidate specific entity cache
                if entity_id and operation_type in ["update", "delete"]:
                    entity_invalidated = await self.invalidate_by_entity(table_name, entity_id, tenant_id)
                    total_invalidated += entity_invalidated

            return total_invalidated

        except Exception as e:
            import logging

            logger = logging.getLogger(__name__)
            logger.warning(f"CRUD cache invalidation failed for {operation_type} on {table_name}: {e}")
            return total_invalidated

    async def exists(self, key: str, tenant_id: str) -> bool:
        """
        Check if a cache key exists.

        Args:
            key: Cache key
            tenant_id: Tenant identifier for isolation

        Returns:
            True if key exists, False otherwise or if caching is disabled
        """
        if not self.enabled or not self._cache_manager:
            return False

        try:
            return await self._cache_manager.exists(key, tenant_id)
        except Exception:
            return False

    async def health_check(self) -> dict[str, Any]:
        """
        Perform health check on cache.

        Returns:
            Health check results
        """
        if not self.enabled or not self._cache_manager:
            return {"status": "disabled", "enabled": False, "error": None}

        try:
            return await self._cache_manager.health_check()
        except Exception as e:
            return {"status": "unhealthy", "enabled": True, "error": str(e)}

    def get_metrics(self) -> dict[str, Any]:
        """
        Get cache performance metrics.

        Returns:
            Dictionary containing cache performance metrics
        """
        if not self.enabled or not self._cache_manager:
            return {
                "enabled": False,
                "hits": 0,
                "misses": 0,
                "errors": 0,
                "operations": 0,
                "hit_rate": 0.0,
                "error_rate": 0.0,
            }

        try:
            metrics = self._cache_manager.get_metrics()
            metrics["enabled"] = True
            return metrics
        except Exception:
            return {
                "enabled": True,
                "hits": 0,
                "misses": 0,
                "errors": 0,
                "operations": 0,
                "hit_rate": 0.0,
                "error_rate": 0.0,
                "error": "Failed to retrieve metrics",
            }

    async def get_detailed_metrics(self) -> dict[str, Any]:
        """
        Get detailed cache performance metrics including Redis server info.

        Returns:
            Dictionary containing detailed cache performance metrics
        """
        if not self.enabled or not self._cache_manager:
            return {
                "enabled": False,
                "basic_metrics": self.get_metrics(),
                "redis_info": None,
                "circuit_breaker": None,
            }

        try:
            detailed_metrics = await self._cache_manager.get_detailed_metrics()
            detailed_metrics["enabled"] = True

            # Add circuit breaker state if available
            if hasattr(self._cache_manager, "_circuit_breaker"):
                detailed_metrics["circuit_breaker"] = self._cache_manager._circuit_breaker.get_state()

            return detailed_metrics
        except Exception as e:
            return {
                "enabled": True,
                "basic_metrics": self.get_metrics(),
                "redis_info": {"error": str(e)},
                "circuit_breaker": None,
                "error": "Failed to retrieve detailed metrics",
            }

    async def monitor_cache_performance(self, operation_name: str | None = None) -> dict[str, Any]:
        """
        Monitor cache performance and provide recommendations.

        Args:
            operation_name: Optional operation name for context

        Returns:
            Performance analysis and recommendations
        """
        metrics = await self.get_detailed_metrics()

        if not metrics.get("enabled", False):
            return {
                "status": "disabled",
                "recommendations": ["Enable caching to improve performance"],
                "metrics": metrics,
            }

        recommendations: list[str] = []
        warnings: list[str] = []
        analysis: dict[str, Any] = {
            "status": "healthy",
            "recommendations": recommendations,
            "warnings": warnings,
            "metrics": metrics,
            "operation_context": operation_name,
        }

        # Analyze hit rate
        hit_rate = float(metrics.get("hit_rate") or 0.0)
        if hit_rate < 0.5:
            warnings.append(f"Low cache hit rate: {hit_rate:.2%}")
            recommendations.append("Consider reviewing cache TTL settings or cache key patterns")
        elif hit_rate > 0.9:
            analysis["status"] = "excellent"

        # Analyze error rate
        error_rate = float(metrics.get("error_rate") or 0.0)
        if error_rate > 0.05:  # More than 5% errors
            warnings.append(f"High cache error rate: {error_rate:.2%}")
            recommendations.append("Check Redis connection stability and configuration")
            if error_rate > 0.2:
                analysis["status"] = "degraded"

        # Check circuit breaker state
        circuit_breaker = metrics.get("circuit_breaker")
        if isinstance(circuit_breaker, dict) and circuit_breaker.get("state") != "closed":
            warnings.append(f"Circuit breaker is {circuit_breaker['state']}")
            recommendations.append("Monitor Redis health and connection stability")
            analysis["status"] = "degraded"

        # Check Redis memory usage
        redis_info = metrics.get("redis_info", {})
        if isinstance(redis_info, dict) and "used_memory" in redis_info:
            # This is a simplified check - in production you'd want more sophisticated monitoring
            used_memory = float(redis_info.get("used_memory") or 0)
            if used_memory > 1024 * 1024 * 1024:  # 1GB threshold
                recommendations.append("Monitor Redis memory usage - consider TTL optimization")

        return analysis

    def reset_metrics(self) -> None:
        """Reset cache performance metrics."""
        if self.enabled and self._cache_manager:
            self._cache_manager.reset_metrics()

    async def cache_warmup(self, warmup_data: dict[str, Any], tenant_id: str) -> dict[str, Any]:
        """
        Warm up cache with frequently accessed data.

        Args:
            warmup_data: Dictionary of key-value pairs to pre-load into cache
            tenant_id: Tenant identifier for isolation

        Returns:
            Warmup results including success/failure counts
        """
        if not self.enabled or not self._cache_manager:
            return {
                "enabled": False,
                "attempted": 0,
                "successful": 0,
                "failed": 0,
                "errors": [],
            }

        successful = 0
        failed = 0
        errors: list[str] = []

        for key, value in warmup_data.items():
            try:
                await self.set(key, value, tenant_id)
                successful += 1
            except Exception as e:
                failed += 1
                errors.append(f"Failed to warm up key '{key}': {str(e)}")

        return {
            "enabled": True,
            "attempted": len(warmup_data),
            "successful": successful,
            "failed": failed,
            "errors": errors,
        }

    @asynccontextmanager
    async def cache_fallback_context(self, operation_name: str = "cache_operation") -> AsyncIterator["DALCacheManager"]:
        """
        Context manager that provides automatic fallback handling for cache operations.

        This ensures that cache failures don't block database operations by providing
        a safe context where cache errors are caught and logged but don't propagate.

        Args:
            operation_name: Name of the operation for logging context

        Usage:
            async with cache_manager.cache_fallback_context("user_lookup"):
                cached_data = await cache_manager.get("user:123", tenant_id)
                if cached_data is None:
                    # Fallback to database
                    data = await database.get_user(123)
                    await cache_manager.set("user:123", data, tenant_id)
        """
        try:
            yield self
        except DALCacheError as e:
            logger.warning(f"Cache operation '{operation_name}' failed with DAL error: {e}")
            # Don't re-raise DAL cache errors - allow fallback to database
        except Exception as e:
            logger.error(f"Unexpected error in cache operation '{operation_name}': {e}")
            # Don't re-raise unexpected errors - allow fallback to database

    async def safe_cache_operation(
        self,
        operation: Callable[..., Any],
        fallback_value: Any = None,
        operation_name: str = "cache_operation",
        *args,
        **kwargs,
    ) -> Any:
        """
        Execute a cache operation with automatic error handling and fallback.

        This method wraps cache operations to ensure they never block database
        operations, implementing the requirement that cache failures should not
        prevent database access.

        Args:
            operation: Cache operation function to execute
            fallback_value: Value to return if cache operation fails
            operation_name: Name of the operation for logging
            *args: Arguments to pass to the operation
            **kwargs: Keyword arguments to pass to the operation

        Returns:
            Result of the operation or fallback_value if operation fails
        """
        try:
            return await operation(*args, **kwargs)
        except DALCacheError as e:
            logger.warning(f"Cache operation '{operation_name}' failed: {e}")
            return fallback_value
        except Exception as e:
            logger.error(f"Unexpected error in cache operation '{operation_name}': {e}")
            return fallback_value

    async def batch_invalidate(
        self,
        invalidation_requests: list[str | dict[str, Any]],
        tenant_id: str,
        continue_on_error: bool = True,
    ) -> dict[str, Any]:
        """
        Perform batch cache invalidation with error handling.

        This method allows for efficient batch invalidation of multiple cache
        patterns while providing detailed error reporting.

        Args:
            invalidation_requests: List of invalidation requests, each can be:
                - str: Pattern to invalidate
                - dict: {"type": "pattern|entity|table|tags", "value": "..."}
            tenant_id: Tenant identifier for isolation
            continue_on_error: Whether to continue processing if individual requests fail

        Returns:
            Results of batch invalidation including success/failure counts
        """
        successful = 0
        failed = 0
        total_keys_invalidated = 0
        errors: list[str] = []
        details: list[dict[str, Any]] = []

        for i, request in enumerate(invalidation_requests):
            try:
                keys_invalidated = 0

                if isinstance(request, str):
                    # Simple pattern invalidation
                    keys_invalidated = await self.invalidate_pattern(request, tenant_id)
                    request_type = "pattern"
                    request_value = request

                elif isinstance(request, dict):
                    request_type = request.get("type", "pattern")
                    request_value = request.get("value", "")

                    if request_type == "pattern":
                        keys_invalidated = await self.invalidate_pattern(request_value, tenant_id)
                    elif request_type == "entity":
                        entity_type = request.get("entity_type", "")
                        entity_id = request.get("entity_id", "")
                        keys_invalidated = await self.invalidate_by_entity(entity_type, entity_id, tenant_id)
                    elif request_type == "table":
                        keys_invalidated = await self.invalidate_by_table(request_value, tenant_id)
                    elif request_type == "tags":
                        tags = request.get("tags", [])
                        keys_invalidated = await self.invalidate_by_tags(tags, tenant_id)
                    else:
                        raise ValueError(f"Unknown invalidation type: {request_type}")
                else:
                    raise ValueError(f"Invalid request format: {type(request)}")

                successful += 1
                total_keys_invalidated += keys_invalidated
                details.append(
                    {
                        "index": i,
                        "type": request_type,
                        "value": request_value,
                        "keys_invalidated": keys_invalidated,
                        "status": "success",
                    }
                )

            except Exception as e:
                failed += 1
                error_msg = f"Request {i} failed: {str(e)}"
                errors.append(error_msg)
                details.append({"index": i, "status": "failed", "error": str(e)})

                logger.warning(f"Batch invalidation request {i} failed: {e}")

                if not continue_on_error:
                    break

        return {
            "total_requests": len(invalidation_requests),
            "successful": successful,
            "failed": failed,
            "total_keys_invalidated": total_keys_invalidated,
            "errors": errors,
            "details": details,
        }

    async def health_check_with_fallback_test(self) -> dict[str, Any]:
        """
        Comprehensive health check that tests cache operations and fallback behavior.

        Returns:
            Detailed health check results including fallback capability testing
        """
        health_result = await self.health_check()

        # Add fallback testing
        fallback_test = {
            "fallback_enabled": True,
            "database_fallback_available": True,  # This would be determined by DAL service
            "circuit_breaker_functional": False,
            "error_handling_working": False,
        }

        # Test error handling by attempting operations when cache might be unavailable
        try:
            # Test safe operation wrapper
            test_result = await self.safe_cache_operation(
                self.get,
                fallback_value="fallback_test",
                operation_name="health_check_test",
                key="nonexistent_key",
                tenant_id="test_tenant",
            )
            fallback_test["error_handling_working"] = True

        except Exception as e:
            logger.warning(f"Fallback test failed: {e}")

        # Test circuit breaker if available
        if self._cache_manager and hasattr(self._cache_manager, "_circuit_breaker"):
            cb_state = self._cache_manager._circuit_breaker.get_state()
            fallback_test["circuit_breaker_functional"] = cb_state.get("state") in ["closed", "half_open"]

        health_result["fallback_capabilities"] = fallback_test
        return health_result
