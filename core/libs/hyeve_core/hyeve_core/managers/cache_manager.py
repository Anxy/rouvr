"""
Centralized Cache Manager for Hyeve Core Platform

This module provides a centralized, reusable caching interface that handles
all low-level Redis interactions including client management, connection pooling,
error handling, and tenant-aware cache key generation.
"""

import asyncio
import logging
from datetime import UTC, datetime
from enum import Enum
from typing import Any

import orjson
import redis.asyncio as redis
from redis.asyncio import ConnectionPool
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import RedisError
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from ..dal.exceptions import DALCacheError


class CircuitBreakerState(Enum):
    """Circuit breaker states."""

    CLOSED = "closed"  # Normal operation
    OPEN = "open"  # Failing, reject requests
    HALF_OPEN = "half_open"  # Testing if service recovered


class CircuitBreaker:
    """
    Circuit breaker pattern implementation for cache operations.

    Prevents cascading failures by temporarily disabling cache operations
    when error rates exceed thresholds.
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: int = 60,
        expected_exception: type[BaseException] | tuple[type[BaseException], ...] = Exception,
    ):
        """
        Initialize circuit breaker.

        Args:
            failure_threshold: Number of failures before opening circuit
            recovery_timeout: Seconds to wait before attempting recovery
            expected_exception: Exception type that triggers circuit breaker
        """
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.expected_exception = expected_exception

        self.failure_count = 0
        self.last_failure_time = None
        self.state = CircuitBreakerState.CLOSED
        self._lock = asyncio.Lock()

    async def call(self, func, *args, **kwargs):
        """
        Execute function with circuit breaker protection.

        Args:
            func: Function to execute
            *args: Function arguments
            **kwargs: Function keyword arguments

        Returns:
            Function result or raises exception
        """
        async with self._lock:
            if self.state == CircuitBreakerState.OPEN:
                if self._should_attempt_reset():
                    self.state = CircuitBreakerState.HALF_OPEN
                else:
                    raise DALCacheError("Circuit breaker is OPEN - cache operations disabled")

            try:
                result = await func(*args, **kwargs)
                await self._on_success()
                return result

            except self.expected_exception:
                await self._on_failure()
                raise

    async def _on_success(self):
        """Handle successful operation."""
        self.failure_count = 0
        self.state = CircuitBreakerState.CLOSED

    async def _on_failure(self):
        """Handle failed operation."""
        self.failure_count += 1
        self.last_failure_time = datetime.now(UTC)

        if self.failure_count >= self.failure_threshold:
            self.state = CircuitBreakerState.OPEN

    def _should_attempt_reset(self) -> bool:
        """Check if circuit breaker should attempt to reset."""
        if self.last_failure_time is None:
            return True

        time_since_failure = (datetime.now(UTC) - self.last_failure_time).total_seconds()
        return time_since_failure >= self.recovery_timeout

    def get_state(self) -> dict[str, Any]:
        """Get circuit breaker state information."""
        return {
            "state": self.state.value,
            "failure_count": self.failure_count,
            "failure_threshold": self.failure_threshold,
            "last_failure_time": self.last_failure_time.isoformat() if self.last_failure_time else None,
            "recovery_timeout": self.recovery_timeout,
        }


logger = logging.getLogger(__name__)


class CacheManager:
    """
    Centralized Cache Manager for the Hyeve platform.

    Provides a unified interface for caching operations with:
    - Tenant-aware cache key generation and isolation
    - Connection pooling and error handling
    - TTL management and configuration
    - Pattern-based cache invalidation
    - Performance monitoring and metrics
    """

    def __init__(
        self,
        redis_url: str = "redis://localhost:6379",
        key_prefix: str = "hyeve",
        default_ttl: int = 3600,
        max_connections: int = 20,
        retry_on_timeout: bool = True,
        socket_keepalive: bool = True,
        socket_keepalive_options: dict[str, int] | None = None,
        decode_responses: bool = False,
        **redis_kwargs,
    ):
        """
        Initialize the Cache Manager.

        Args:
            redis_url: Redis connection URL
            key_prefix: Global key prefix for all cache keys
            default_ttl: Default TTL in seconds for cache entries
            max_connections: Maximum number of Redis connections in pool
            retry_on_timeout: Whether to retry on timeout errors
            socket_keepalive: Enable TCP keepalive
            socket_keepalive_options: TCP keepalive options
            decode_responses: Whether to decode Redis responses to strings
            **redis_kwargs: Additional Redis client configuration
        """
        self.redis_url = redis_url
        self.key_prefix = key_prefix
        self.default_ttl = default_ttl
        self.retry_on_timeout = retry_on_timeout

        # Connection pool configuration
        self.pool_config = {
            "max_connections": max_connections,
            "socket_keepalive": socket_keepalive,
            "socket_keepalive_options": socket_keepalive_options or {},
            "decode_responses": decode_responses,
            **redis_kwargs,
        }

        self._pool: ConnectionPool | None = None
        self._client: redis.Redis | None = None
        self._initialized = False
        self._lock = asyncio.Lock()

        # Circuit breaker for error handling
        self._circuit_breaker = CircuitBreaker(
            failure_threshold=5, recovery_timeout=60, expected_exception=(RedisConnectionError, RedisError)
        )

        # Performance metrics
        self._metrics: dict[str, Any] = {
            "hits": 0,
            "misses": 0,
            "errors": 0,
            "operations": 0,
            "circuit_breaker_trips": 0,
            "last_error": None,
            "last_error_time": None,
        }

    async def initialize(self) -> None:
        """Initialize Redis connection pool and client."""
        if self._initialized:
            return

        async with self._lock:
            if self._initialized:
                return

            try:
                # Create connection pool
                self._pool = ConnectionPool.from_url(self.redis_url, **self.pool_config)

                # Create Redis client
                self._client = redis.Redis(connection_pool=self._pool)

                # Test connection
                await self._client.ping()

                self._initialized = True
                logger.info("Cache Manager initialized successfully")

            except Exception as e:
                logger.error(f"Failed to initialize Cache Manager: {e}")
                raise DALCacheError(f"Cache initialization failed: {e}") from e

    async def shutdown(self) -> None:
        """Shutdown Redis connections and cleanup resources."""
        if not self._initialized:
            return

        async with self._lock:
            try:
                if self._client:
                    await self._client.aclose()
                    self._client = None

                if self._pool:
                    await self._pool.aclose()
                    self._pool = None

                self._initialized = False
                logger.info("Cache Manager shutdown completed")

            except Exception as e:
                logger.error(f"Error during Cache Manager shutdown: {e}")

    def _build_cache_key(self, key: str, tenant_id: str) -> str:
        """
        Build tenant-aware cache key with proper isolation.

        Args:
            key: Base cache key
            tenant_id: Tenant identifier for isolation

        Returns:
            Fully qualified cache key with tenant isolation
        """
        # Ensure tenant_id is always part of the key prefix for isolation
        return f"{self.key_prefix}:tenant:{tenant_id}:{key}"

    def _require_client(self) -> redis.Redis:
        if self._client is None:
            raise DALCacheError("Cache client not initialized")
        return self._client

    def _serialize_value(self, value: Any) -> bytes:
        """
        Serialize value for Redis storage using orjson for performance.

        Args:
            value: Value to serialize

        Returns:
            Serialized bytes
        """
        try:
            if isinstance(value, (str, int, float, bool)):
                # Simple types can be stored directly
                return orjson.dumps({"type": type(value).__name__, "value": value})
            else:
                # Complex types need full serialization
                return orjson.dumps({"type": "complex", "value": value})
        except (TypeError, ValueError) as e:
            raise DALCacheError(f"Failed to serialize cache value: {e}") from e

    def _deserialize_value(self, data: bytes) -> Any:
        """
        Deserialize value from Redis storage.

        Args:
            data: Serialized bytes from Redis

        Returns:
            Deserialized value
        """
        try:
            parsed = orjson.loads(data)
            if parsed["type"] in ("str", "int", "float", "bool"):
                return parsed["value"]
            else:
                return parsed["value"]
        except (orjson.JSONDecodeError, KeyError, TypeError) as e:
            raise DALCacheError(f"Failed to deserialize cache value: {e}") from e

    async def get(self, key: str, tenant_id: str) -> Any | None:
        """
        Get cached value with tenant isolation.

        Args:
            key: Cache key
            tenant_id: Tenant identifier for isolation

        Returns:
            Cached value or None if not found

        Raises:
            DALCacheError: If cache operation fails
        """
        if not self._initialized:
            await self.initialize()

        cache_key = self._build_cache_key(key, tenant_id)

        try:
            # Use circuit breaker to protect against cascading failures
            return await self._circuit_breaker.call(self._get_internal, cache_key)

        except DALCacheError:
            # Circuit breaker is open, return None to allow database fallback
            self._metrics["circuit_breaker_trips"] += 1
            logger.warning(f"Cache get operation blocked by circuit breaker for key {cache_key}")
            return None
        except Exception as e:
            self._metrics["errors"] += 1
            self._metrics["last_error"] = str(e)
            self._metrics["last_error_time"] = datetime.now(UTC)

            logger.error(f"Cache get operation failed for key {cache_key}: {e}")
            # Return None to allow fallback to database
            return None

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception_type((RedisConnectionError, RedisError)),
    )
    async def _get_internal(self, cache_key: str) -> Any | None:
        """Internal get method with retry logic."""
        self._metrics["operations"] += 1
        client = self._require_client()
        data = await client.get(cache_key)

        if data is None:
            self._metrics["misses"] += 1
            return None

        self._metrics["hits"] += 1
        return self._deserialize_value(data)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception_type((RedisConnectionError, RedisError)),
    )
    async def set(self, key: str, value: Any, tenant_id: str, ttl: int | None = None) -> None:
        """
        Set cached value with tenant isolation and TTL management.

        Args:
            key: Cache key
            value: Value to cache
            tenant_id: Tenant identifier for isolation
            ttl: Time to live in seconds (uses default_ttl if None)

        Raises:
            DALCacheError: If cache operation fails
        """
        if not self._initialized:
            await self.initialize()

        cache_key = self._build_cache_key(key, tenant_id)
        effective_ttl = ttl if ttl is not None else self.default_ttl

        try:
            self._metrics["operations"] += 1

            serialized_value = self._serialize_value(value)

            if effective_ttl > 0:
                await self._require_client().setex(cache_key, effective_ttl, serialized_value)
            else:
                await self._require_client().set(cache_key, serialized_value)

        except Exception as e:
            self._metrics["errors"] += 1
            self._metrics["last_error"] = str(e)
            self._metrics["last_error_time"] = datetime.now(UTC)

            logger.error(f"Cache set operation failed for key {cache_key}: {e}")

            if self.retry_on_timeout and isinstance(e, (RedisConnectionError, RedisError)):
                raise  # Let retry decorator handle it
            else:
                # For non-retryable errors, log and continue (don't block the operation)
                logger.warning(f"Cache set failed, continuing without caching: {e}")

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception_type((RedisConnectionError, RedisError)),
    )
    async def delete(self, key: str, tenant_id: str) -> None:
        """
        Delete cached value with tenant isolation.

        Args:
            key: Cache key
            tenant_id: Tenant identifier for isolation

        Raises:
            DALCacheError: If cache operation fails
        """
        if not self._initialized:
            await self.initialize()

        cache_key = self._build_cache_key(key, tenant_id)

        try:
            self._metrics["operations"] += 1
            await self._require_client().delete(cache_key)

        except Exception as e:
            self._metrics["errors"] += 1
            self._metrics["last_error"] = str(e)
            self._metrics["last_error_time"] = datetime.now(UTC)

            logger.error(f"Cache delete operation failed for key {cache_key}: {e}")

            if self.retry_on_timeout and isinstance(e, (RedisConnectionError, RedisError)):
                raise  # Let retry decorator handle it
            else:
                # For non-retryable errors, log and continue
                logger.warning(f"Cache delete failed, continuing: {e}")

    async def invalidate_pattern(self, pattern: str, tenant_id: str) -> int:
        """
        Invalidate cache entries matching pattern with tenant isolation.

        Args:
            pattern: Pattern to match (supports Redis glob patterns)
            tenant_id: Tenant identifier for isolation

        Returns:
            Number of keys deleted

        Raises:
            DALCacheError: If cache operation fails
        """
        if not self._initialized:
            await self.initialize()

        # Build tenant-aware pattern
        tenant_pattern = self._build_cache_key(pattern, tenant_id)

        try:
            self._metrics["operations"] += 1

            # Use SCAN to find matching keys (more efficient than KEYS)
            keys_to_delete = []
            client = self._require_client()
            async for key in client.scan_iter(match=tenant_pattern):
                keys_to_delete.append(key)

            if keys_to_delete:
                deleted_count = await client.delete(*keys_to_delete)
                logger.info(f"Invalidated {deleted_count} cache entries matching pattern: {tenant_pattern}")
                return deleted_count
            else:
                return 0

        except Exception as e:
            self._metrics["errors"] += 1
            self._metrics["last_error"] = str(e)
            self._metrics["last_error_time"] = datetime.now(UTC)

            logger.error(f"Cache pattern invalidation failed for pattern {tenant_pattern}: {e}")

            if self.retry_on_timeout and isinstance(e, (RedisConnectionError, RedisError)):
                raise  # Let retry decorator handle it
            else:
                # For non-retryable errors, log and continue
                logger.warning(f"Cache pattern invalidation failed, continuing: {e}")
                return 0

    async def invalidate_by_tags(self, tags: list[str], tenant_id: str) -> int:
        """
        Invalidate cache entries by tags with tenant isolation.

        Args:
            tags: List of tags to invalidate
            tenant_id: Tenant identifier for isolation

        Returns:
            Total number of keys deleted
        """
        total_deleted = 0

        for tag in tags:
            # Create pattern for tag-based invalidation
            tag_pattern = f"tag:{tag}:*"
            deleted_count = await self.invalidate_pattern(tag_pattern, tenant_id)
            total_deleted += deleted_count

        return total_deleted

    async def invalidate_by_entity(self, entity_type: str, entity_id: str, tenant_id: str) -> int:
        """
        Invalidate cache entries for a specific entity.

        Args:
            entity_type: Type of entity (e.g., 'user', 'order')
            entity_id: ID of the entity
            tenant_id: Tenant identifier for isolation

        Returns:
            Number of keys deleted
        """
        # Create patterns for entity-based invalidation
        patterns = [
            f"{entity_type}:{entity_id}:*",  # Direct entity cache
            f"*:{entity_type}:{entity_id}:*",  # Related entity cache
            f"list:{entity_type}:*",  # List caches that might include this entity
        ]

        total_deleted = 0
        for pattern in patterns:
            deleted_count = await self.invalidate_pattern(pattern, tenant_id)
            total_deleted += deleted_count

        return total_deleted

    async def invalidate_by_table(self, table_name: str, tenant_id: str) -> int:
        """
        Invalidate all cache entries for a specific table.

        Args:
            table_name: Name of the database table
            tenant_id: Tenant identifier for isolation

        Returns:
            Number of keys deleted
        """
        # Create patterns for table-based invalidation
        patterns = [
            f"table:{table_name}:*",  # Direct table cache
            f"query:{table_name}:*",  # Query result cache
            f"list:{table_name}:*",  # List caches
            f"count:{table_name}:*",  # Count caches
        ]

        total_deleted = 0
        for pattern in patterns:
            deleted_count = await self.invalidate_pattern(pattern, tenant_id)
            total_deleted += deleted_count

        return total_deleted

    async def exists(self, key: str, tenant_id: str) -> bool:
        """
        Check if a cache key exists.

        Args:
            key: Cache key
            tenant_id: Tenant identifier for isolation

        Returns:
            True if key exists, False otherwise
        """
        if not self._initialized:
            await self.initialize()

        cache_key = self._build_cache_key(key, tenant_id)

        try:
            return bool(await self._require_client().exists(cache_key))
        except Exception as e:
            logger.error(f"Cache exists check failed for key {cache_key}: {e}")
            return False

    async def expire(self, key: str, tenant_id: str, ttl: int) -> bool:
        """
        Set expiration time for a cache key.

        Args:
            key: Cache key
            tenant_id: Tenant identifier for isolation
            ttl: Time to live in seconds

        Returns:
            True if expiration was set, False if key doesn't exist
        """
        if not self._initialized:
            await self.initialize()

        cache_key = self._build_cache_key(key, tenant_id)

        try:
            return bool(await self._require_client().expire(cache_key, ttl))
        except Exception as e:
            logger.error(f"Cache expire operation failed for key {cache_key}: {e}")
            return False

    async def get_ttl(self, key: str, tenant_id: str) -> int:
        """
        Get remaining TTL for a cache key.

        Args:
            key: Cache key
            tenant_id: Tenant identifier for isolation

        Returns:
            TTL in seconds (-1 if no expiration, -2 if key doesn't exist)
        """
        if not self._initialized:
            await self.initialize()

        cache_key = self._build_cache_key(key, tenant_id)

        try:
            return await self._require_client().ttl(cache_key)
        except Exception as e:
            logger.error(f"Cache TTL check failed for key {cache_key}: {e}")
            return -2

    async def health_check(self) -> dict[str, Any]:
        """
        Perform health check on Redis connection.

        Returns:
            Health check results including connection status and metrics
        """
        health_status = {
            "status": "unknown",
            "initialized": self._initialized,
            "metrics": self._metrics.copy(),
            "error": None,
        }

        if not self._initialized:
            health_status["status"] = "not_initialized"
            return health_status

        try:
            # Test Redis connection
            pong = await self._require_client().ping()
            if pong:
                health_status["status"] = "healthy"
            else:
                health_status["status"] = "unhealthy"
                health_status["error"] = "Redis ping failed"

        except Exception as e:
            health_status["status"] = "unhealthy"
            health_status["error"] = str(e)

        return health_status

    def get_metrics(self) -> dict[str, Any]:
        """
        Get cache performance metrics.

        Returns:
            Dictionary containing cache performance metrics
        """
        metrics = self._metrics.copy()

        # Calculate hit rate
        hits = int(metrics.get("hits") or 0)
        misses = int(metrics.get("misses") or 0)
        total_reads = hits + misses
        if total_reads > 0:
            metrics["hit_rate"] = hits / total_reads
        else:
            metrics["hit_rate"] = 0.0

        # Calculate error rate
        operations = int(metrics.get("operations") or 0)
        errors = int(metrics.get("errors") or 0)
        if operations > 0:
            metrics["error_rate"] = errors / operations
        else:
            metrics["error_rate"] = 0.0

        # Add connection pool metrics if available
        if self._pool:
            try:
                metrics["pool_created_connections"] = getattr(self._pool, "created_connections", None)
                metrics["pool_available_connections"] = getattr(self._pool, "available_connections", None)
                metrics["pool_in_use_connections"] = getattr(self._pool, "in_use_connections", None)
            except AttributeError:
                # Some Redis versions might not have these attributes
                pass

        return metrics

    async def get_detailed_metrics(self) -> dict[str, Any]:
        """
        Get detailed cache performance metrics including Redis server info.

        Returns:
            Dictionary containing detailed cache performance metrics
        """
        metrics = self.get_metrics()

        if not self._initialized or not self._client:
            metrics["redis_info"] = None
            return metrics

        try:
            # Get Redis server information
            redis_info = await self._require_client().info()

            # Extract relevant Redis metrics
            metrics["redis_info"] = {
                "redis_version": redis_info.get("redis_version"),
                "used_memory": redis_info.get("used_memory"),
                "used_memory_human": redis_info.get("used_memory_human"),
                "connected_clients": redis_info.get("connected_clients"),
                "total_commands_processed": redis_info.get("total_commands_processed"),
                "keyspace_hits": redis_info.get("keyspace_hits"),
                "keyspace_misses": redis_info.get("keyspace_misses"),
                "uptime_in_seconds": redis_info.get("uptime_in_seconds"),
            }

            # Calculate Redis hit rate if available
            redis_hits = redis_info.get("keyspace_hits", 0)
            redis_misses = redis_info.get("keyspace_misses", 0)
            redis_total = int(redis_hits) + int(redis_misses)

            if redis_total > 0:
                metrics["redis_hit_rate"] = redis_hits / redis_total
            else:
                metrics["redis_hit_rate"] = 0.0

        except Exception as e:
            logger.warning(f"Failed to get Redis server info: {e}")
            metrics["redis_info"] = {"error": str(e)}

        return metrics

    def reset_metrics(self) -> None:
        """Reset performance metrics."""
        self._metrics = {
            "hits": 0,
            "misses": 0,
            "errors": 0,
            "operations": 0,
            "last_error": None,
            "last_error_time": None,
        }

    async def __aenter__(self):
        """Async context manager entry."""
        await self.initialize()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.shutdown()
