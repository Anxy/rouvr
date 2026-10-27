"""
Configuration Validation Module

Enhanced validation logic for DAL configuration with detailed error messages
and schema validation against expected structure.
"""

import re
from typing import Any

from .interfaces import DatabaseType


class ConfigurationValidator:
    """
    Enhanced configuration validator with detailed error reporting.
    """

    # Required configuration sections
    REQUIRED_SECTIONS = {'database', 'cache', 'tenant', 'validation', 'ayrpack'}

    # Valid database types
    VALID_DB_TYPES = {db_type.value for db_type in DatabaseType}

    # Valid isolation modes
    VALID_ISOLATION_MODES = {'row_level_security', 'schema_based', 'database_based'}

    # Valid hash algorithms
    VALID_HASH_ALGORITHMS = {'md5', 'sha1', 'sha256', 'sha512'}

    # Valid log levels
    VALID_LOG_LEVELS = {'DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'}

    # Valid log formats
    VALID_LOG_FORMATS = {'json', 'text'}

    def __init__(self):
        """Initialize the validator."""
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def validate_configuration_structure(self, config_data: dict[str, Any]) -> tuple[list[str], list[str]]:
        """
        Validate the overall configuration structure.
        
        Args:
            config_data: Raw configuration data
            
        Returns:
            Tuple of (errors, warnings)
        """
        self.errors.clear()
        self.warnings.clear()

        # Check required sections
        self._validate_required_sections(config_data)

        # Validate each section
        if 'database' in config_data:
            self._validate_database_section(config_data['database'])

        if 'cache' in config_data:
            self._validate_cache_section(config_data['cache'])

        if 'tenant' in config_data:
            self._validate_tenant_section(config_data['tenant'])

        if 'validation' in config_data:
            self._validate_validation_section(config_data['validation'])

        if 'ayrpack' in config_data:
            self._validate_ayrpack_section(config_data['ayrpack'])

        if 'logging' in config_data:
            self._validate_logging_section(config_data['logging'])

        if 'performance' in config_data:
            self._validate_performance_section(config_data['performance'])

        return self.errors.copy(), self.warnings.copy()

    def _validate_required_sections(self, config_data: dict[str, Any]) -> None:
        """Validate that all required sections are present."""
        missing_sections = self.REQUIRED_SECTIONS - set(config_data.keys())
        if missing_sections:
            self.errors.append(f"Missing required configuration sections: {', '.join(missing_sections)}")

    def _validate_database_section(self, database_config: dict[str, Any]) -> None:
        """Validate database configuration section."""
        if not database_config:
            self.errors.append("Database configuration cannot be empty")
            return

        if not isinstance(database_config, dict):
            self.errors.append("Database configuration must be a dictionary")
            return

        # Validate each database configuration
        for db_name, db_config in database_config.items():
            self._validate_single_database_config(db_name, db_config)

        # Check for at least one valid database
        valid_configs = [
            config for config in database_config.values()
            if isinstance(config, dict) and config.get('type') in self.VALID_DB_TYPES
        ]

        if not valid_configs:
            self.errors.append("At least one valid database configuration is required")

    def _validate_single_database_config(self, db_name: str, db_config: Any) -> None:
        """Validate a single database configuration."""
        if not isinstance(db_config, dict):
            self.errors.append(f"Database config '{db_name}' must be a dictionary")
            return

        # Required fields
        required_fields = {'type', 'connection_string'}
        missing_fields = required_fields - set(db_config.keys())
        if missing_fields:
            self.errors.append(
                f"Database config '{db_name}' missing required fields: {', '.join(missing_fields)}"
            )

        # Validate database type
        db_type = db_config.get('type')
        if db_type and db_type not in self.VALID_DB_TYPES:
            self.errors.append(
                f"Database config '{db_name}' has invalid type '{db_type}'. "
                f"Valid types: {', '.join(self.VALID_DB_TYPES)}"
            )

        # Validate connection string
        conn_str = db_config.get('connection_string')
        if conn_str:
            self._validate_connection_string(db_name, conn_str)

        # Validate numeric fields
        numeric_fields = {
            'pool_size': (1, 100),
            'pool_min_size': (1, 50),
            'pool_max_size': (1, 200),
            'timeout': (1, 300),
            'retry_attempts': (0, 10)
        }

        for field, (min_val, max_val) in numeric_fields.items():
            if field in db_config:
                value = db_config[field]
                if not isinstance(value, int) or value < min_val or value > max_val:
                    self.errors.append(
                        f"Database config '{db_name}' field '{field}' must be an integer "
                        f"between {min_val} and {max_val}"
                    )

        # Validate pool size relationships
        pool_min = db_config.get('pool_min_size', 5)
        pool_max = db_config.get('pool_max_size', 20)
        if isinstance(pool_min, int) and isinstance(pool_max, int) and pool_min > pool_max:
            self.warnings.append(
                f"Database config '{db_name}': pool_min_size ({pool_min}) "
                f"should not be greater than pool_max_size ({pool_max})"
            )

    def _validate_connection_string(self, db_name: str, conn_str: str) -> None:
        """Validate database connection string format."""
        if not conn_str or not isinstance(conn_str, str):
            self.errors.append(f"Database config '{db_name}' connection_string must be a non-empty string")
            return

        # Check for environment variable patterns
        if '${' in conn_str or '$' in conn_str:
            # This is expected for environment variable substitution
            return

        # Basic PostgreSQL connection string validation
        if conn_str.startswith(('postgresql://', 'postgres://')):
            # Valid PostgreSQL connection string format
            return
        elif conn_str.startswith('sqlite://'):
            # Valid SQLite connection string format
            return
        else:
            self.warnings.append(
                f"Database config '{db_name}' connection string format may be invalid. "
                "Expected PostgreSQL (postgresql://) or SQLite (sqlite://) format"
            )

    def _validate_cache_section(self, cache_config: dict[str, Any]) -> None:
        """Validate cache configuration section."""
        if not isinstance(cache_config, dict):
            self.errors.append("Cache configuration must be a dictionary")
            return

        # Required fields when cache is enabled
        if cache_config.get('enabled', True):
            if not cache_config.get('redis_url'):
                self.errors.append("Cache is enabled but redis_url is not provided")

        # Validate numeric fields
        numeric_fields = {
            'default_ttl': (1, 86400 * 7),  # 1 second to 1 week
            'max_connections': (1, 100),
            'socket_timeout': (1, 60)
        }

        for field, (min_val, max_val) in numeric_fields.items():
            if field in cache_config:
                value = cache_config[field]
                if not isinstance(value, int) or value < min_val or value > max_val:
                    self.errors.append(
                        f"Cache config field '{field}' must be an integer "
                        f"between {min_val} and {max_val}"
                    )

        # Validate Redis URL format
        redis_url = cache_config.get('redis_url')
        if redis_url and not self._is_valid_redis_url(redis_url):
            self.warnings.append("Redis URL format may be invalid")

    def _is_valid_redis_url(self, redis_url: str) -> bool:
        """Check if Redis URL format is valid."""
        if not redis_url or not isinstance(redis_url, str):
            return False

        # Check for environment variable patterns
        if '${' in redis_url or '$' in redis_url:
            return True  # Assume valid for env vars

        # Basic Redis URL validation
        return redis_url.startswith(('redis://', 'rediss://'))

    def _validate_tenant_section(self, tenant_config: dict[str, Any]) -> None:
        """Validate tenant configuration section."""
        if not isinstance(tenant_config, dict):
            self.errors.append("Tenant configuration must be a dictionary")
            return

        # Validate isolation mode
        isolation_mode = tenant_config.get('isolation_mode')
        if isolation_mode and isolation_mode not in self.VALID_ISOLATION_MODES:
            self.errors.append(
                f"Invalid tenant isolation_mode '{isolation_mode}'. "
                f"Valid modes: {', '.join(self.VALID_ISOLATION_MODES)}"
            )

        # Validate column names
        column_fields = ['tenant_id_column', 'soft_delete_column']
        for field in column_fields:
            value = tenant_config.get(field)
            if value and not self._is_valid_column_name(value):
                self.errors.append(f"Tenant config '{field}' contains invalid column name: {value}")

        # Check isolation enforcement consistency
        if tenant_config.get('enforce_isolation', True):
            if not tenant_config.get('tenant_id_column'):
                self.warnings.append(
                    "Tenant isolation is enforced but tenant_id_column is not specified"
                )

    def _is_valid_column_name(self, column_name: str) -> bool:
        """Check if column name is valid SQL identifier."""
        if not column_name or not isinstance(column_name, str):
            return False

        # Basic SQL identifier validation
        pattern = r'^[a-zA-Z_][a-zA-Z0-9_]*$'
        return bool(re.match(pattern, column_name))

    def _validate_validation_section(self, validation_config: dict[str, Any]) -> None:
        """Validate validation configuration section."""
        if not isinstance(validation_config, dict):
            self.errors.append("Validation configuration must be a dictionary")
            return

        # All fields are boolean, validate them
        boolean_fields = [
            'strict_mode', 'validate_on_read', 'validate_on_write',
            'allow_extra_fields', 'validate_tenant_isolation', 'ulid_validation'
        ]

        for field in boolean_fields:
            if field in validation_config:
                value = validation_config[field]
                if not isinstance(value, bool):
                    self.errors.append(f"Validation config '{field}' must be a boolean value")

    def _validate_ayrpack_section(self, ayrpack_config: dict[str, Any]) -> None:
        """Validate Ayrpack configuration section."""
        if not isinstance(ayrpack_config, dict):
            self.errors.append("Ayrpack configuration must be a dictionary")
            return

        # Required fields when Ayrpack is enabled
        if ayrpack_config.get('enabled', True):
            required_fields = ['packet_table', 'rules_table']
            for field in required_fields:
                if not ayrpack_config.get(field):
                    self.errors.append(f"Ayrpack is enabled but '{field}' is not provided")

        # Validate hash algorithm
        hash_algo = ayrpack_config.get('hash_algorithm')
        if hash_algo and hash_algo not in self.VALID_HASH_ALGORITHMS:
            self.errors.append(
                f"Invalid Ayrpack hash_algorithm '{hash_algo}'. "
                f"Valid algorithms: {', '.join(self.VALID_HASH_ALGORITHMS)}"
            )

        # Validate max packet size
        max_size = ayrpack_config.get('max_packet_size')
        if max_size is not None:
            if not isinstance(max_size, int) or max_size < 1024 or max_size > 100 * 1024 * 1024:
                self.errors.append(
                    "Ayrpack max_packet_size must be an integer between 1KB and 100MB"
                )

        # Validate table names
        table_fields = ['packet_table', 'rules_table']
        for field in table_fields:
            value = ayrpack_config.get(field)
            if value and not self._is_valid_table_name(value):
                self.errors.append(f"Ayrpack config '{field}' contains invalid table name: {value}")

    def _is_valid_table_name(self, table_name: str) -> bool:
        """Check if table name is valid SQL identifier."""
        return self._is_valid_column_name(table_name)

    def _validate_logging_section(self, logging_config: dict[str, Any]) -> None:
        """Validate logging configuration section."""
        if not isinstance(logging_config, dict):
            self.errors.append("Logging configuration must be a dictionary")
            return

        # Validate log level
        level = logging_config.get('level')
        if level and level.upper() not in self.VALID_LOG_LEVELS:
            self.errors.append(
                f"Invalid logging level '{level}'. "
                f"Valid levels: {', '.join(self.VALID_LOG_LEVELS)}"
            )

        # Validate log format
        format_type = logging_config.get('format')
        if format_type and format_type not in self.VALID_LOG_FORMATS:
            self.errors.append(
                f"Invalid logging format '{format_type}'. "
                f"Valid formats: {', '.join(self.VALID_LOG_FORMATS)}"
            )

        # Validate boolean fields
        boolean_fields = [
            'enable_sql_logging', 'enable_cache_logging', 'log_sensitive_data'
        ]

        for field in boolean_fields:
            if field in logging_config:
                value = logging_config[field]
                if not isinstance(value, bool):
                    self.errors.append(f"Logging config '{field}' must be a boolean value")

        # Security warning for sensitive data logging
        if logging_config.get('log_sensitive_data', False):
            self.warnings.append(
                "Sensitive data logging is enabled. This should only be used in development"
            )

    def _validate_performance_section(self, performance_config: dict[str, Any]) -> None:
        """Validate performance configuration section."""
        if not isinstance(performance_config, dict):
            self.errors.append("Performance configuration must be a dictionary")
            return

        # Validate slow query threshold
        threshold = performance_config.get('slow_query_threshold')
        if threshold is not None:
            if not isinstance(threshold, (int, float)) or threshold <= 0:
                self.errors.append(
                    "Performance config 'slow_query_threshold' must be a positive number"
                )

        # Validate boolean fields
        boolean_fields = [
            'enable_metrics', 'connection_pool_monitoring',
            'cache_hit_rate_monitoring', 'enable_query_analysis'
        ]

        for field in boolean_fields:
            if field in performance_config:
                value = performance_config[field]
                if not isinstance(value, bool):
                    self.errors.append(f"Performance config '{field}' must be a boolean value")

    def validate_environment_variables(self, config_data: dict[str, Any]) -> list[str]:
        """
        Validate that all required environment variables are available.
        
        Args:
            config_data: Configuration data with potential env var references
            
        Returns:
            List of missing environment variables
        """
        missing_vars = []
        env_var_pattern = r'\$\{([^}:]+)(?::[^}]*)?\}'
        simple_var_pattern = r'\$([A-Za-z_][A-Za-z0-9_]*)'

        def extract_vars(data: Any) -> set[str]:
            """Recursively extract environment variable names."""
            vars_found = set()

            if isinstance(data, dict):
                for value in data.values():
                    vars_found.update(extract_vars(value))
            elif isinstance(data, list):
                for item in data:
                    vars_found.update(extract_vars(item))
            elif isinstance(data, str):
                # Find ${VAR} patterns
                for match in re.finditer(env_var_pattern, data):
                    var_name = match.group(1).strip()
                    if ':' not in match.group(0):  # No default value
                        vars_found.add(var_name)

                # Find $VAR patterns
                for match in re.finditer(simple_var_pattern, data):
                    vars_found.add(match.group(1))

            return vars_found

        required_vars = extract_vars(config_data)

        # Check which variables are missing
        import os
        for var_name in required_vars:
            if os.getenv(var_name) is None:
                missing_vars.append(var_name)

        return missing_vars
