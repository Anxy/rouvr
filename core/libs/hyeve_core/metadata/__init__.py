"""
Hyeve Core Metadata Module
=========================

Metadata extraction and processing utilities for database schemas and table information.
"""

from dataclasses import dataclass
from typing import Any


@dataclass
class FieldValidation:
    """Field validation rules."""

    max_length: int | None = None
    min_length: int | None = None
    pattern: str | None = None
    required: bool = False
    minimum: float | None = None
    maximum: float | None = None


class TableInfoExtractor:
    """Extracts metadata and validation rules from table column information."""

    def __init__(self):
        self.type_mapping = {
            "VARCHAR": "string",
            "TEXT": "string",
            "INTEGER": "integer",
            "BIGINT": "integer",
            "BOOLEAN": "boolean",
            "JSONB": "object",
            "JSON": "object",
            "TIMESTAMP": "string",
            "TIMESTAMPTZ": "string",
            "UUID": "string",
            "DECIMAL": "number",
            "NUMERIC": "number",
        }

    def _extract_validation(self, column: Any) -> FieldValidation | None:
        """Extract validation rules from column constraints."""
        validation = FieldValidation()
        has_validation = False

        # Extract length constraints for string types
        if hasattr(column, "type") and hasattr(column.type, "length") and column.type.length:
            validation.max_length = column.type.length
            has_validation = True

        # Extract numeric constraints
        if hasattr(column, "type") and hasattr(column.type, "precision") and column.type.precision:
            # For numeric types, use precision as maximum
            validation.maximum = 10**column.type.precision - 1
            has_validation = True

        # Set required flag
        if hasattr(column, "nullable") and not column.nullable:
            validation.required = True
            has_validation = True

        return validation if has_validation else None

    def extract_column_metadata(self, columns: list[Any]) -> dict[str, Any]:
        """
        Extract comprehensive metadata from table columns.

        Args:
            columns: List of column objects with type, nullable, etc. attributes

        Returns:
            Dictionary containing column metadata and validation rules
        """
        metadata: dict[str, Any] = {"columns": [], "validation_rules": {}, "constraints": []}

        for column in columns:
            try:
                column_info = self._extract_column_info(column)
                metadata["columns"].append(column_info)

                # Extract validation rules
                validation = self._extract_validation(column)
                if validation:
                    metadata["validation_rules"][column.name] = {
                        "max_length": validation.max_length,
                        "min_length": validation.min_length,
                        "pattern": validation.pattern,
                        "required": validation.required,
                        "minimum": validation.minimum,
                        "maximum": validation.maximum,
                    }

            except Exception as e:
                # Log error but continue processing other columns
                # Using logger if available, otherwise silent (errors should be logged via telemetry)
                import logging

                logger = logging.getLogger(__name__)
                logger.warning(f"Error extracting metadata for column {getattr(column, 'name', 'unknown')}: {e}")
                continue

        return metadata

    def _extract_column_info(self, column: Any) -> dict[str, Any]:
        """Extract information for a single column."""
        return {
            "name": getattr(column, "name", ""),
            "type": self._map_column_type(column),
            "nullable": getattr(column, "nullable", True),
            "default": getattr(column, "default", None),
            "primary_key": getattr(column, "primary_key", False),
            "unique": getattr(column, "unique", False),
            "autoincrement": getattr(column, "autoincrement", False),
            "comment": getattr(column, "comment", None),
        }

    def _map_column_type(self, column: Any) -> str:
        """Map SQLAlchemy column type to string representation."""
        if hasattr(column, "type"):
            type_str = str(column.type).upper()
            # Handle parameterized types like VARCHAR(255)
            base_type = type_str.split("(")[0]
            return self.type_mapping.get(base_type, "string")
        return "string"

    def validate_table_structure(self, table_info: dict[str, Any]) -> list[str]:
        """
        Validate table structure for common issues.

        Args:
            table_info: Table information dictionary

        Returns:
            List of validation issues found
        """
        issues = []

        columns = table_info.get("columns", [])

        # Check for primary key
        has_primary_key = any(col.get("primary_key", False) for col in columns)
        if not has_primary_key:
            issues.append("No primary key defined")

        # Check for tenant isolation column
        tenant_columns = ["tenant_id", "org_id", "organization_id"]
        has_tenant_column = any(col["name"].lower() in tenant_columns for col in columns)
        if not has_tenant_column:
            issues.append("No tenant isolation column found")

        # Check for audit columns
        audit_columns = ["created_at", "updated_at", "created_by", "updated_by"]
        missing_audit = []
        for audit_col in audit_columns:
            if not any(col["name"].lower() == audit_col for col in columns):
                missing_audit.append(audit_col)

        if missing_audit:
            issues.append(f"Missing audit columns: {', '.join(missing_audit)}")

        return issues
