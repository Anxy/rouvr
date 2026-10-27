"""
DAL Configuration Management

Configuration models and loaders for the DAL library using Pydantic for validation
and supporting environment variable substitution.
"""

import json
import os
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .exceptions import DALConfigurationError
from .interfaces import ConfigurationLoader, DatabaseType


class DatabaseConfig(BaseModel):
    """Database connection configuration."""

    type: str = Field(..., description="Database type (prisma, sqlalchemy, asyncpg)")
    connection_string: str = Field(..., description="Database connection string")
    schema_path: str | None = Field(None, description="Path to schema file (for Prisma)")
    pool_size: int | None = Field(10, description="Connection pool size")
    pool_min_size: int | None = Field(5, description="Minimum pool size")
    pool_max_size: int | None = Field(20, description="Maximum pool size")
    timeout: int | None = Field(30, description="Connection timeout in seconds")
    retry_attempts: int | None = Field(3, description="Number of retry attempts")

    @field_validator("type")
    @classmethod
    def validate_type(cls, v):
        """Validate database type."""
        valid_types = [db_type.value for db_type in DatabaseType]
        if v not in valid_types:
            raise ValueError(f"Invalid database type: {v}. Must be one of {valid_types}")
        return v

    @field_validator("connection_string")
    @classmethod
    def validate_connection_string(cls, v):
        """Validate connection string format."""
        if not v or not isinstance(v, str):
            raise ValueError("Connection string must be a non-empty string")
        return v


class CacheConfig(BaseModel):
    """Redis cache configuration."""

    enabled: bool = Field(True, description="Enable/disable caching")
    redis_url: str = Field(..., description="Redis connection URL")
    default_ttl: int = Field(3600, description="Default TTL in seconds")
    key_prefix: str = Field("hyeve:dal", description="Cache key prefix")
    max_connections: int | None = Field(10, description="Maximum Redis connections")
    retry_on_timeout: bool = Field(True, description="Retry on timeout")
    socket_timeout: int | None = Field(5, description="Socket timeout in seconds")

    @field_validator("default_ttl")
    @classmethod
    def validate_ttl(cls, v):
        """Validate TTL value."""
        if v <= 0:
            raise ValueError("TTL must be positive")
        return v


class TenantConfig(BaseModel):
    """Multi-tenant configuration."""

    isolation_mode: str = Field("row_level_security", description="Tenant isolation mode")
    tenant_id_column: str = Field("tenant_id", description="Tenant ID column name")
    soft_delete_column: str = Field("deleted_at", description="Soft delete column name")
    enforce_isolation: bool = Field(True, description="Enforce tenant isolation")
    default_tenant_id: str | None = Field(None, description="Default tenant ID for testing")

    @field_validator("isolation_mode")
    @classmethod
    def validate_isolation_mode(cls, v):
        """Validate isolation mode."""
        valid_modes = ["row_level_security", "schema_based", "database_based"]
        if v not in valid_modes:
            raise ValueError(f"Invalid isolation mode: {v}. Must be one of {valid_modes}")
        return v


class ValidationConfig(BaseModel):
    """Data validation configuration."""

    strict_mode: bool = Field(True, description="Enable strict validation")
    validate_on_read: bool = Field(False, description="Validate data on read operations")
    validate_on_write: bool = Field(True, description="Validate data on write operations")
    allow_extra_fields: bool = Field(False, description="Allow extra fields in models")
    validate_tenant_isolation: bool = Field(True, description="Validate tenant isolation")
    ulid_validation: bool = Field(True, description="Validate ULID format")


class AyrpackConfig(BaseModel):
    """Ayrpack workflow integration configuration."""

    enabled: bool = Field(True, description="Enable Ayrpack integration")
    packet_table: str = Field("workflow_packets", description="Workflow packets table name")
    rules_table: str = Field("workflow_rules", description="Workflow rules table name")
    hash_algorithm: str = Field("sha256", description="Hash algorithm for packet validation")
    max_packet_size: int = Field(1048576, description="Maximum packet size in bytes (1MB)")

    @field_validator("hash_algorithm")
    @classmethod
    def validate_hash_algorithm(cls, v):
        """Validate hash algorithm."""
        valid_algorithms = ["md5", "sha1", "sha256", "sha512"]
        if v not in valid_algorithms:
            raise ValueError(f"Invalid hash algorithm: {v}. Must be one of {valid_algorithms}")
        return v


class LoggingConfig(BaseModel):
    """Logging configuration."""

    level: str = Field("INFO", description="Log level")
    format: str = Field("json", description="Log format (json, text)")
    enable_sql_logging: bool = Field(False, description="Enable SQL query logging")
    enable_cache_logging: bool = Field(False, description="Enable cache operation logging")
    log_sensitive_data: bool = Field(False, description="Log sensitive data (for debugging)")

    @field_validator("level")
    @classmethod
    def validate_level(cls, v):
        """Validate log level."""
        valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        if v.upper() not in valid_levels:
            raise ValueError(f"Invalid log level: {v}. Must be one of {valid_levels}")
        return v.upper()


class PerformanceConfig(BaseModel):
    """Performance and monitoring configuration."""

    enable_metrics: bool = Field(True, description="Enable performance metrics")
    slow_query_threshold: float = Field(1.0, description="Slow query threshold in seconds")
    connection_pool_monitoring: bool = Field(True, description="Monitor connection pools")
    cache_hit_rate_monitoring: bool = Field(True, description="Monitor cache hit rates")
    enable_query_analysis: bool = Field(False, description="Enable query analysis")


class DALConfig(BaseModel):
    """
    DAL configuration loaded from dal_config.json.

    This class manages all configuration aspects of the DAL library including
    database connections, caching, tenant isolation, and validation settings.
    """

    database: dict[str, DatabaseConfig] = Field(..., description="Database configurations")
    cache: CacheConfig = Field(..., description="Cache configuration")
    tenant: TenantConfig = Field(..., description="Tenant configuration")
    validation: ValidationConfig = Field(..., description="Validation configuration")
    ayrpack: AyrpackConfig = Field(..., description="Ayrpack configuration")
    logging: LoggingConfig = Field(default_factory=LoggingConfig, description="Logging configuration")  # type: ignore[arg-type]
    performance: PerformanceConfig = Field(default_factory=PerformanceConfig, description="Performance configuration")  # type: ignore[arg-type]

    # Internal field for tracking primary database
    _primary_db: str | None = None

    model_config = ConfigDict(validate_assignment=True, extra="forbid", use_enum_values=True)

    @model_validator(mode="after")
    def validate_database_config(self):
        """Validate database configuration structure."""
        database_config = self.database

        if not database_config:
            raise ValueError("Database configuration is required")

        # Ensure we have at least one database configuration
        if not any(database_config.values()):
            raise ValueError("At least one database configuration is required")

        # Validate that primary database exists if referenced
        if "primary" not in database_config and len(database_config) > 1:
            # If no primary specified but multiple configs, use first one as primary
            first_key = next(iter(database_config.keys()))
            self._primary_db = first_key
        elif "primary" in database_config:
            self._primary_db = "primary"
        else:
            # Single database config
            self._primary_db = next(iter(database_config.keys()))

        return self

    @classmethod
    def load_from_file(cls, config_path: str) -> "DALConfig":
        """
        Load configuration from JSON file with environment variable substitution.

        Args:
            config_path: Path to the configuration file

        Returns:
            DALConfig instance

        Raises:
            DALConfigurationError: If configuration loading or validation fails
        """
        try:
            config_file = Path(config_path)

            if not config_file.exists():
                raise DALConfigurationError(f"Configuration file not found: {config_path}", config_path=config_path)

            # Read and parse JSON
            with open(config_file, encoding="utf-8") as f:
                config_data = json.load(f)

            # Substitute environment variables
            config_data = cls._substitute_env_vars(config_data)

            # Create and validate configuration
            return cls(**config_data)

        except json.JSONDecodeError as e:
            raise DALConfigurationError(f"Invalid JSON in configuration file: {e}", config_path=config_path)
        except Exception as e:
            if isinstance(e, DALConfigurationError):
                raise
            raise DALConfigurationError(f"Failed to load configuration: {e}", config_path=config_path)

    @classmethod
    def _substitute_env_vars(cls, data: Any) -> Any:
        """
        Recursively substitute environment variables in configuration data.

        Supports formats:
        - ${VAR_NAME} - Required variable
        - ${VAR_NAME:default_value} - Variable with default
        - $VAR_NAME - Simple variable reference

        Args:
            data: Configuration data (dict, list, or string)

        Returns:
            Data with environment variables substituted
        """
        if isinstance(data, dict):
            return {key: cls._substitute_env_vars(value) for key, value in data.items()}
        elif isinstance(data, list):
            return [cls._substitute_env_vars(item) for item in data]
        elif isinstance(data, str):
            return cls._substitute_string_env_vars(data)
        else:
            return data

    @classmethod
    def _substitute_string_env_vars(cls, text: str) -> str:
        """
        Substitute environment variables in a string.

        Args:
            text: String potentially containing environment variable references

        Returns:
            String with environment variables substituted
        """
        # Pattern for ${VAR_NAME} or ${VAR_NAME:default}
        pattern = r"\$\{([^}]+)\}"

        def replace_var(match):
            var_expr = match.group(1)
            if ":" in var_expr:
                var_name, default_value = var_expr.split(":", 1)
                return os.getenv(var_name.strip(), default_value)
            else:
                var_name = var_expr.strip()
                value = os.getenv(var_name)
                if value is None:
                    raise DALConfigurationError(f"Required environment variable not found: {var_name}")
                return value

        # Replace ${VAR} patterns
        result = re.sub(pattern, replace_var, text)

        # Pattern for simple $VAR references
        simple_pattern = r"\$([A-Za-z_][A-Za-z0-9_]*)"

        def replace_simple_var(match):
            var_name = match.group(1)
            value = os.getenv(var_name)
            if value is None:
                raise DALConfigurationError(f"Required environment variable not found: {var_name}")
            return value

        # Replace $VAR patterns
        result = re.sub(simple_pattern, replace_simple_var, result)

        return result

    def get_connection_string(self, db_type: DatabaseType) -> str:
        """
        Get connection string for specified database type.

        Args:
            db_type: Database type to get connection string for

        Returns:
            Connection string for the specified database type

        Raises:
            DALConfigurationError: If database type not configured
        """
        db_type_str = db_type.value if isinstance(db_type, DatabaseType) else str(db_type)

        # Try to find exact match first
        if db_type_str in self.database:
            return self.database[db_type_str].connection_string

        # Try to find by database type in config
        for config_name, db_config in self.database.items():
            if db_config.type == db_type_str:
                return db_config.connection_string

        # If not found, try primary database only if it matches the requested type
        primary_db = getattr(self, "_primary_db", None)
        if primary_db and primary_db in self.database:
            primary_config = self.database[primary_db]
            if primary_config.type == db_type_str:
                return primary_config.connection_string

        raise DALConfigurationError(
            f"No database configuration found for type: {db_type_str}", config_section="database"
        )

    def get_database_config(self, db_type: DatabaseType) -> DatabaseConfig:
        """
        Get database configuration for specified type.

        Args:
            db_type: Database type to get configuration for

        Returns:
            DatabaseConfig for the specified type

        Raises:
            DALConfigurationError: If database type not configured
        """
        db_type_str = db_type.value if isinstance(db_type, DatabaseType) else str(db_type)

        # Try to find exact match first
        if db_type_str in self.database:
            return self.database[db_type_str]

        # Try to find by database type in config
        for config_name, db_config in self.database.items():
            if db_config.type == db_type_str:
                return db_config

        raise DALConfigurationError(
            f"No database configuration found for type: {db_type_str}", config_section="database"
        )

    def get_primary_database_type(self) -> DatabaseType:
        """
        Get the primary database type.

        Returns:
            Primary database type
        """
        primary_db = getattr(self, "_primary_db", None)
        if primary_db and primary_db in self.database:
            db_type_str = self.database[primary_db].type
            return DatabaseType(db_type_str)

        # Fallback to first database
        if self.database:
            first_db = next(iter(self.database.values()))
            return DatabaseType(first_db.type)

        raise DALConfigurationError("No database configuration found")

    def validate_configuration(self) -> list[str]:
        """
        Validate the entire configuration and return any warnings.

        Returns:
            List of warning messages
        """
        warnings = []

        # Check database configurations
        if not self.database:
            warnings.append("No database configurations found")

        # Check cache configuration if enabled
        if self.cache.enabled:
            if not self.cache.redis_url:
                warnings.append("Cache is enabled but no Redis URL provided")

        # Check tenant configuration
        if self.tenant.enforce_isolation and not self.tenant.tenant_id_column:
            warnings.append("Tenant isolation is enabled but no tenant ID column specified")

        # Check Ayrpack configuration if enabled
        if self.ayrpack.enabled:
            if not self.ayrpack.packet_table or not self.ayrpack.rules_table:
                warnings.append("Ayrpack is enabled but table names not properly configured")

        return warnings

    def to_dict(self) -> dict[str, Any]:
        """
        Convert configuration to dictionary.

        Returns:
            Configuration as dictionary
        """
        return self.model_dump()

    def to_json(self, indent: int = 2) -> str:
        """
        Convert configuration to JSON string.

        Args:
            indent: JSON indentation level

        Returns:
            Configuration as JSON string
        """
        import json

        return json.dumps(self.model_dump(), indent=indent)


class DALConfigurationLoader(ConfigurationLoader):
    """
    Configuration loader implementation for DAL.
    """

    def load_config(self, config_path: str) -> DALConfig:
        """
        Load configuration from file.

        Args:
            config_path: Path to configuration file

        Returns:
            Loaded DAL configuration
        """
        return DALConfig.load_from_file(config_path)

    def validate_config(self, config: DALConfig) -> bool:
        """
        Validate configuration structure and values.

        Args:
            config: Configuration to validate

        Returns:
            True if configuration is valid

        Raises:
            DALConfigurationError: If configuration is invalid
        """
        try:
            warnings = config.validate_configuration()
            if warnings:
                # Log warnings but don't fail validation
                for warning in warnings:
                    print(f"Configuration warning: {warning}")
            return True
        except Exception as e:
            raise DALConfigurationError(f"Configuration validation failed: {e}")

    def get_connection_string(self, db_type: DatabaseType) -> str:
        """
        Get connection string for specified database type.

        This method requires a loaded configuration. Use DALConfig.get_connection_string instead.

        Args:
            db_type: Database type

        Raises:
            NotImplementedError: This method should be called on DALConfig instance
        """
        raise NotImplementedError("Use DALConfig.get_connection_string() method on a loaded configuration instance")
