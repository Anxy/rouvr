# mypy: ignore-errors
"""
Prisma ORM Integration for DAL

This module provides Prisma client initialization, connection management,
and CRUD operations with error handling and retry logic.
"""

import asyncio
import logging
from datetime import UTC, date, datetime
from typing import Any
from uuid import uuid4

from .dal_config import DatabaseConfig
from .exceptions import DALConnectionError, DALValidationError
from .interfaces import CRUDOperation, CRUDResult, DatabaseType

logger = logging.getLogger(__name__)

_NO_RECORD_VERSION_TABLES = {
    "outbox",
    "inbox",
    "idempotency_keys",
    "admin_audit_log",
    "service_config",
    "feature_flags",
    "wfe_definitions",
    "wfe_definition_alias",
    "wfe_instances",
    "wfe_steps",
    "wfe_tasks",
    "wfe_timers",
    "wfe_events",
    "arrears_snapshots",
    "payment_allocations",
    "payment_variations",
    "payments",
    "repayment_schedule",
    "bre_rulesets",
    "bre_ruleset_alias",
    "bre_rule_traces",
    "application_documents",
    "document_checklist_items",
    "document_checklists",
    "user_account",
    "credential_password",
    "session",
    "refresh_token",
    "jwk_key",
    "role",
    "permission",
    "user_role",
    "role_permission",
}

_UUID_PK_TABLES = {
    "user_account",
    "credential_password",
    "session",
    "refresh_token",
    "jwk_key",
    "role",
    "permission",
    "user_role",
    "role_permission",
}

_NO_AUDIT_FIELDS_TABLES = {
    "user_account",
    "credential_password",
    "session",
    "refresh_token",
    "jwk_key",
    "role",
    "permission",
    "user_role",
    "role_permission",
    "outbox",
    "audit_log",
    "file_event",
}

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

try:
    from prisma import Json, Prisma
    from prisma.errors import PrismaError

    try:
        from prisma.errors import ConnectError, DisconnectedError
    except ImportError:  # pragma: no cover - older prisma client
        ConnectError = PrismaError  # type: ignore[misc,assignment]
        DisconnectedError = PrismaError  # type: ignore[misc,assignment]

    PRISMA_AVAILABLE = True
except ImportError:
    logger.warning("Prisma client not available. Install with: pip install prisma")
    PRISMA_AVAILABLE = False

    # Mock classes for when Prisma is not available
    class Prisma:
        pass

    class Json:  # type: ignore[no-redef]
        def __init__(self, value: Any):
            self.value = value

    class PrismaError(Exception):
        pass

    class ConnectError(PrismaError):
        pass

    class DisconnectedError(PrismaError):
        pass


class PrismaConnectionManager:
    """Manages Prisma client connections with retry logic."""

    def __init__(self, config: DatabaseConfig):
        self.config = config
        self.client: Prisma | None = None
        self._connected = False
        self._connection_lock = asyncio.Lock()

    async def initialize(self) -> None:
        """Initialize Prisma client and establish connection."""
        if not PRISMA_AVAILABLE:
            raise DALConnectionError("Prisma client not available", db_type=DatabaseType.PRISMA.value)

        async with self._connection_lock:
            if self._connected:
                return

            try:
                self.client = Prisma()
                await self.client.connect()
                self._connected = True
                logger.info("Prisma client connected successfully")

            except ConnectError as e:
                raise DALConnectionError(
                    f"Failed to connect to Prisma: {e}",
                    db_type=DatabaseType.PRISMA.value,
                    connection_string=self.config.connection_string,
                )
            except Exception as e:
                raise DALConnectionError(
                    f"Unexpected error connecting to Prisma: {e}", db_type=DatabaseType.PRISMA.value
                )

    async def disconnect(self) -> None:
        """Disconnect Prisma client."""
        async with self._connection_lock:
            if self.client and self._connected:
                try:
                    await self.client.disconnect()
                    self._connected = False
                    logger.info("Prisma client disconnected")
                except Exception as e:
                    logger.error(f"Error disconnecting Prisma client: {e}")

    def is_connected(self) -> bool:
        """Check if Prisma client is connected."""
        return self._connected and self.client is not None

    async def ensure_connected(self) -> None:
        """Ensure Prisma client is connected, reconnect if necessary."""
        if not self.is_connected():
            await self.initialize()

    def get_client(self) -> Prisma:
        """Get Prisma client instance."""
        if not self.client:
            raise DALConnectionError("Prisma client not initialized", db_type=DatabaseType.PRISMA.value)
        return self.client


class PrismaDALIntegration:
    """Prisma ORM integration for DAL operations."""

    def __init__(self, config: DatabaseConfig):
        self.config = config
        self.connection_manager = PrismaConnectionManager(config)

    async def initialize(self) -> None:
        """Initialize Prisma integration."""
        await self.connection_manager.initialize()

    async def close(self) -> None:
        """Close Prisma integration."""
        await self.connection_manager.disconnect()

    async def execute_crud(self, operation: CRUDOperation) -> CRUDResult:
        """
        Execute CRUD operation using Prisma client.

        Args:
            operation: CRUD operation to execute

        Returns:
            CRUD operation result
        """
        try:
            await self.connection_manager.ensure_connected()
            client = self.connection_manager.get_client()

            if operation.operation_type == "create":
                return await self._execute_create(client, operation)
            elif operation.operation_type == "read":
                return await self._execute_read(client, operation)
            elif operation.operation_type == "update":
                return await self._execute_update(client, operation)
            elif operation.operation_type == "delete":
                return await self._execute_delete(client, operation)
            else:
                raise DALValidationError(f"Unsupported operation type: {operation.operation_type}")

        except DisconnectedError:
            # Retry once on disconnection
            logger.warning("Prisma client disconnected, attempting reconnect")
            await self.connection_manager.initialize()
            return await self.execute_crud(operation)

        except PrismaError as e:
            logger.error(f"Prisma error during {operation.operation_type}: {e}")
            return CRUDResult(success=False, error=f"Prisma error: {e}", operation_type=operation.operation_type)

        except Exception as e:
            logger.error(f"Unexpected error during {operation.operation_type}: {e}")
            return CRUDResult(success=False, error=f"Unexpected error: {e}", operation_type=operation.operation_type)

    async def execute_crud_operation(self, operation: CRUDOperation) -> CRUDResult:
        """Compatibility shim for DAL service."""
        return await self.execute_crud(operation)

    async def _execute_create(self, client: Prisma, operation: CRUDOperation) -> CRUDResult:
        """Execute create operation using Prisma."""
        try:
            # Get the model dynamically
            model = self._get_prisma_model(client, operation.table_name)

            # Prepare data with audit fields
            create_data = self._prepare_create_data(operation)

            # Execute create
            result = await model.create(data=create_data)

            return CRUDResult(
                success=True,
                data=self._serialize_prisma_result(result),
                operation_type=operation.operation_type,
                affected_rows=1,
            )

        except Exception as e:
            logger.error(f"Prisma create error: {e}")
            return CRUDResult(success=False, error=str(e), operation_type=operation.operation_type)

    async def _execute_read(self, client: Prisma, operation: CRUDOperation) -> CRUDResult:
        """Execute read operation using Prisma."""
        try:
            model = self._get_prisma_model(client, operation.table_name)

            # Build where clause with tenant isolation and soft delete filtering
            where_clause = self._build_where_clause(operation)

            pk_field = self._get_primary_key_field(operation.table_name, operation.filters or {})
            if operation.filters and pk_field in operation.filters:
                # Single record by ID
                result = await model.find_unique(where={pk_field: operation.filters[pk_field]})
                if result and self._check_tenant_access(result, operation.tenant_id):
                    return CRUDResult(
                        success=True,
                        data=self._serialize_prisma_result(result),
                        operation_type=operation.operation_type,
                    )
                else:
                    return CRUDResult(success=True, data=None, operation_type=operation.operation_type)
            else:
                # Multiple records
                limit = operation.options.get("limit", 100) if operation.options else 100
                offset = operation.options.get("offset", 0) if operation.options else 0

                results = await model.find_many(
                    where=where_clause, take=limit, skip=offset, order={"created_at": "desc"}
                )

                return CRUDResult(
                    success=True,
                    data=[self._serialize_prisma_result(r) for r in results],
                    operation_type=operation.operation_type,
                    affected_rows=len(results),
                )

        except Exception as e:
            logger.error(f"Prisma read error: {e}")
            return CRUDResult(success=False, error=str(e), operation_type=operation.operation_type)

    async def _execute_update(self, client: Prisma, operation: CRUDOperation) -> CRUDResult:
        """Execute update operation using Prisma."""
        try:
            pk_field = self._get_primary_key_field(operation.table_name, operation.filters or {})
            if not operation.filters or pk_field not in operation.filters:
                raise DALValidationError("ID required for update operation")

            model = self._get_prisma_model(client, operation.table_name)

            # Prepare update data with audit fields
            update_data = self._prepare_update_data(operation)

            # Build where clause with tenant isolation
            where_clause = {
                pk_field: operation.filters[pk_field],
                "tenant_id": operation.tenant_id,
            }
            if operation.table_name not in _NO_DELETED_AT_TABLES:
                where_clause["deleted_at"] = None

            result = await model.update(where=where_clause, data=update_data)

            return CRUDResult(
                success=True,
                data=self._serialize_prisma_result(result),
                operation_type=operation.operation_type,
                affected_rows=1,
            )

        except Exception as e:
            logger.error(f"Prisma update error: {e}")
            return CRUDResult(success=False, error=str(e), operation_type=operation.operation_type)

    async def _execute_delete(self, client: Prisma, operation: CRUDOperation) -> CRUDResult:
        """Execute soft delete operation using Prisma."""
        try:
            pk_field = self._get_primary_key_field(operation.table_name, operation.filters or {})
            if not operation.filters or pk_field not in operation.filters:
                raise DALValidationError("ID required for delete operation")

            model = self._get_prisma_model(client, operation.table_name)

            # Soft delete by setting deleted_at timestamp
            now = datetime.now(UTC)
            update_data = {"updated_at": now}
            if operation.table_name not in _NO_DELETED_AT_TABLES:
                update_data["deleted_at"] = now

            if operation.user_id and operation.table_name not in _NO_AUDIT_FIELDS_TABLES:
                update_data["updated_by"] = operation.user_id

            # Build where clause with tenant isolation
            where_clause = {
                pk_field: operation.filters[pk_field],
                "tenant_id": operation.tenant_id,
            }
            if operation.table_name not in _NO_DELETED_AT_TABLES:
                where_clause["deleted_at"] = None

            result = await model.update(where=where_clause, data=update_data)

            return CRUDResult(
                success=True,
                data={"deleted": True, pk_field: operation.filters[pk_field]},
                operation_type=operation.operation_type,
                affected_rows=1,
            )

        except Exception as e:
            logger.error(f"Prisma delete error: {e}")
            return CRUDResult(success=False, error=str(e), operation_type=operation.operation_type)

    def _get_prisma_model(self, client: Prisma, table_name: str) -> Any:
        """
        Get Prisma model by table name.

        Args:
            client: Prisma client instance
            table_name: Database table name

        Returns:
            Prisma model instance

        Raises:
            DALValidationError: If model not found
        """
        for candidate in self._table_name_variants(table_name):
            model_name = self._table_to_model_name(candidate)
            if hasattr(client, model_name.lower()):
                return getattr(client, model_name.lower())
            if hasattr(client, candidate):
                return getattr(client, candidate)

        raise DALValidationError(f"Prisma model not found for table: {table_name}")

    def _table_to_model_name(self, table_name: str) -> str:
        """Convert table name to Prisma model name."""
        # Convert snake_case to PascalCase
        parts = table_name.split("_")
        return "".join(word.capitalize() for word in parts)

    def _table_name_variants(self, table_name: str) -> list[str]:
        variants = [table_name]
        if table_name.endswith("ies") and len(table_name) > 3:
            variants.append(f"{table_name[:-3]}y")
        if table_name.endswith("es") and len(table_name) > 2:
            variants.append(table_name[:-2])
        if table_name.endswith("s") and not table_name.endswith("ss") and len(table_name) > 1:
            variants.append(table_name[:-1])
        return list(dict.fromkeys(variants))

    def _prepare_create_data(self, operation: CRUDOperation) -> dict[str, Any]:
        """Prepare data for create operation with audit fields."""
        data = operation.data.copy() if operation.data else {}

        # Add tenant isolation
        data["tenant_id"] = operation.tenant_id

        # Add audit fields
        now = datetime.now(UTC)
        data.setdefault("created_at", now)
        data.setdefault("updated_at", now)
        if operation.table_name not in _NO_RECORD_VERSION_TABLES:
            data.setdefault("record_version", 1)
        if operation.table_name not in _NO_DELETED_AT_TABLES:
            data.setdefault("deleted_at", None)

        if operation.user_id and operation.table_name not in _NO_AUDIT_FIELDS_TABLES:
            data.setdefault("created_by", operation.user_id)
            data.setdefault("updated_by", operation.user_id)

        # Generate primary key if missing (handles non-standard PK fields)
        pk_field = self._get_primary_key_field(operation.table_name, data)
        if pk_field not in data:
            if operation.table_name in _UUID_PK_TABLES:
                return self._coerce_json_values(data)
            try:
                import ulid

                if hasattr(ulid, "new"):
                    data[pk_field] = str(ulid.new())
                elif hasattr(ulid, "ULID"):
                    data[pk_field] = str(ulid.ULID())
                else:
                    data[pk_field] = str(uuid4())
            except Exception:
                data[pk_field] = str(uuid4())
        if pk_field != "id":
            data.pop("id", None)

        return self._coerce_json_values(data)

    def _prepare_update_data(self, operation: CRUDOperation) -> dict[str, Any]:
        """Prepare data for update operation with audit fields."""
        data = operation.data.copy() if operation.data else {}

        # Add audit fields
        data["updated_at"] = datetime.now(UTC)

        if operation.user_id and operation.table_name not in _NO_AUDIT_FIELDS_TABLES:
            data["updated_by"] = operation.user_id

        # Increment version if present
        if "record_version" in data:
            data["record_version"] = data.get("record_version", 1) + 1

        # Remove fields that shouldn't be updated
        data.pop("id", None)
        data.pop("tenant_id", None)
        data.pop("created_at", None)
        data.pop("created_by", None)

        return self._coerce_json_values(data)

    def _get_primary_key_field(self, table_name: str, data: dict[str, Any]) -> str:
        """Resolve primary key field name for tables with non-standard PKs."""
        explicit_keys = {"event_id", "message_id", "audit_id", "trace_id"}
        for key in explicit_keys:
            if key in data:
                return key

        table_key_map = {
            "outbox": "event_id",
            "inbox": "message_id",
            "admin_audit_log": "audit_id",
            "bre_rule_traces": "trace_id",
            "session": "session_id",
        }

        return table_key_map.get(table_name, "id")

    def _coerce_json_values(self, data: dict[str, Any]) -> dict[str, Any]:
        """Coerce values into Prisma-safe payloads (JSON + date/time)."""

        def _serialize(value: Any) -> Any:
            if isinstance(value, dict):
                return {k: _serialize(v) for k, v in value.items()}
            if isinstance(value, list):
                return [_serialize(v) for v in value]
            if isinstance(value, datetime):
                return value.isoformat()
            if isinstance(value, date):
                return datetime.combine(value, datetime.min.time(), tzinfo=UTC).isoformat()
            return value

        coerced: dict[str, Any] = {}
        for key, value in data.items():
            if isinstance(value, (dict, list)):
                coerced[key] = Json(_serialize(value))
            else:
                coerced[key] = _serialize(value)
        return coerced

    def _build_where_clause(self, operation: CRUDOperation) -> dict[str, Any]:
        """Build where clause with tenant isolation and soft delete filtering."""
        where_clause = {"tenant_id": operation.tenant_id}
        if operation.table_name not in _NO_DELETED_AT_TABLES:
            where_clause["deleted_at"] = None  # Filter out soft-deleted records

        # Add additional filters
        if operation.filters:
            pk_field = self._get_primary_key_field(operation.table_name, operation.filters)
            for key, value in operation.filters.items():
                if key != pk_field:  # ID is handled separately
                    where_clause[key] = value

        return where_clause

    def _check_tenant_access(self, result: Any, tenant_id: str) -> bool:
        """Check if result belongs to the correct tenant."""
        if hasattr(result, "tenant_id"):
            return result.tenant_id == tenant_id
        return True  # Allow if no tenant_id field

    def _serialize_prisma_result(self, result: Any) -> dict[str, Any]:
        """Serialize Prisma result to dictionary."""
        if result is None:
            return None

        # Convert Prisma model to dict
        if hasattr(result, "dict"):
            return result.dict()
        elif hasattr(result, "__dict__"):
            return {k: v for k, v in result.__dict__.items() if not k.startswith("_")}
        else:
            return result

    async def health_check(self) -> dict[str, Any]:
        """Perform health check on Prisma connection."""
        try:
            if not PRISMA_AVAILABLE:
                return {"status": "unavailable", "error": "Prisma client not installed"}

            await self.connection_manager.ensure_connected()
            client = self.connection_manager.get_client()

            # Simple query to test connection
            await client.query_raw("SELECT 1")

            return {
                "status": "healthy",
                "connected": self.connection_manager.is_connected(),
                "client_version": getattr(client, "__version__", "unknown"),
            }

        except Exception as e:
            return {"status": "unhealthy", "error": str(e), "connected": False}


async def create_prisma_integration(config: DatabaseConfig) -> PrismaDALIntegration:
    """
    Create and initialize Prisma DAL integration.

    Args:
        config: Database configuration for Prisma

    Returns:
        Initialized Prisma DAL integration
    """
    integration = PrismaDALIntegration(config)
    await integration.initialize()
    return integration


class PrismaIntegration(PrismaDALIntegration):
    """Alias used by DAL service for Prisma operations."""

    pass
