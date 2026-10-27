# mypy: ignore-errors
"""
SQLAlchemy Fallback Integration for DAL

This module provides SQLAlchemy engine setup, session management,
and CRUD operations as a fallback when Prisma is unavailable.
"""

import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import ulid
from sqlalchemy import (
    Column,
    DateTime,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    and_,
    delete,
    insert,
    select,
    text,
    update,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base

from .dal_config import DALConfig, DatabaseConfig
from .exceptions import DALConnectionError, DALError, DALTenantError, DALValidationError
from .interfaces import CRUDOperation, CRUDResult, DatabaseType

logger = logging.getLogger(__name__)

_NO_RECORD_VERSION_TABLES = {
    "outbox",
    "inbox",
    "idempotency_keys",
    "admin_audit_log",
    "service_config",
    "feature_flags",
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

# SQLAlchemy base for ORM models
Base = declarative_base()


class BaseTenantModel(Base):
    """
    Base model for all tenant-aware entities with common fields.
    """

    __abstract__ = True

    id = Column(String(26), primary_key=True, default=lambda: str(ulid.new()))
    tenant_id = Column(String(26), nullable=False, index=True)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(UTC))
    updated_at = Column(DateTime, nullable=False, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))
    created_by = Column(String(26), nullable=False)
    updated_by = Column(String(26), nullable=False)
    record_version = Column(Integer, nullable=False, default=1)
    deleted_at = Column(DateTime, nullable=True, index=True)

    __table_args__ = (Index("idx_tenant_deleted", "tenant_id", "deleted_at"),)


class SQLAlchemyConnectionManager:
    """Manages SQLAlchemy engine and session lifecycle."""

    def __init__(self, config: DatabaseConfig):
        self.config = config
        self.engine = None
        self.session_factory = None
        self._initialized = False

    async def initialize(self) -> None:
        """Initialize SQLAlchemy engine and session factory."""
        if self._initialized:
            logger.warning("SQLAlchemy connection manager already initialized")
            return

        try:
            connection_string = self.config.connection_string
            if connection_string.startswith("postgresql://"):
                connection_string = connection_string.replace("postgresql://", "postgresql+asyncpg://", 1)

            self.engine = create_async_engine(
                connection_string,
                pool_size=self.config.pool_size or 10,
                max_overflow=self.config.pool_max_size or 20,
                pool_timeout=self.config.timeout or 30,
                pool_pre_ping=True,
                echo=False,
                future=True,
            )

            self.session_factory = async_sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)

            # Test connection
            async with self.engine.begin() as conn:
                await conn.execute(select(1))

            self._initialized = True
            logger.info("SQLAlchemy connection manager initialized successfully")

        except Exception as e:
            logger.error(f"Failed to initialize SQLAlchemy connection manager: {e}")
            raise DALConnectionError(
                f"SQLAlchemy initialization failed: {e}",
                db_type="sqlalchemy",
                connection_string=self.config.connection_string,
            )

    @asynccontextmanager
    async def get_session(self):
        """Get an async database session with automatic cleanup."""
        if not self._initialized:
            raise DALConnectionError("Connection manager not initialized", db_type="sqlalchemy")

        session = self.session_factory()
        try:
            yield session
            await session.commit()
        except Exception as e:
            await session.rollback()
            logger.error(f"Session error, rolling back: {e}")
            raise
        finally:
            await session.close()

    async def close(self) -> None:
        """Close the database engine and cleanup resources."""
        if self.engine:
            await self.engine.dispose()
            self._initialized = False
            logger.info("SQLAlchemy connection manager closed")


class SQLAlchemyFallbackIntegration:
    """Main SQLAlchemy fallback integration class."""

    def __init__(self, config: DALConfig):
        self.config = config
        self.connection_manager: SQLAlchemyConnectionManager | None = None
        self._initialized = False

    async def initialize(self) -> None:
        """Initialize SQLAlchemy integration."""
        if self._initialized:
            logger.warning("SQLAlchemy integration already initialized")
            return

        try:
            sqlalchemy_config = self.config.get_database_config(DatabaseType.SQLALCHEMY)
            self.connection_manager = SQLAlchemyConnectionManager(sqlalchemy_config)
            await self.connection_manager.initialize()

            self._initialized = True
            logger.info("SQLAlchemy fallback integration initialized successfully")

        except Exception as e:
            logger.error(f"Failed to initialize SQLAlchemy integration: {e}")
            raise DALConnectionError(f"SQLAlchemy integration initialization failed: {e}", db_type="sqlalchemy")

    async def execute_crud_operation(self, operation: CRUDOperation) -> CRUDResult:
        """Execute CRUD operation using SQLAlchemy."""
        if not self._initialized:
            raise DALConnectionError("SQLAlchemy integration not initialized", db_type="sqlalchemy")

        try:
            start_time = datetime.now(UTC)

            # Execute operation based on type
            if operation.operation_type == "create":
                result = await self._execute_create(operation)
            elif operation.operation_type == "read":
                result = await self._execute_read(operation)
            elif operation.operation_type == "update":
                result = await self._execute_update(operation)
            elif operation.operation_type == "delete":
                result = await self._execute_delete(operation)
            else:
                raise DALValidationError(f"Unsupported operation type: {operation.operation_type}")

            # Calculate execution time
            execution_time = (datetime.now(UTC) - start_time).total_seconds()
            result.execution_time = execution_time

            return result

        except Exception as e:
            logger.error(f"SQLAlchemy CRUD operation failed: {e}")
            if isinstance(e, (DALError, DALConnectionError, DALValidationError, DALTenantError)):
                raise
            raise DALError(f"SQLAlchemy operation failed: {e}")

    async def _execute_create(self, operation: CRUDOperation) -> CRUDResult:
        """Execute CREATE operation using SQLAlchemy."""
        try:
            async with self.connection_manager.get_session() as session:
                await self._apply_tenant_context(session, operation.tenant_id)

                # Get table metadata
                table = await self._get_table_metadata(operation.table_name, session)

                # Prepare data with common fields
                data = self._prepare_create_data(operation)

                # Filter data to only include columns that exist in the table
                table_columns = {col.name for col in table.columns}
                filtered_data = {k: v for k, v in data.items() if k in table_columns}

                # Execute insert
                stmt = insert(table).values(**filtered_data)
                result = await session.execute(stmt)

                # Get the inserted ID
                inserted_id = filtered_data.get("id") or filtered_data.get("event_id")

                return CRUDResult(
                    success=True,
                    data={"id": inserted_id, **filtered_data},
                    operation_type=operation.operation_type,
                    affected_rows=result.rowcount,
                )

        except IntegrityError as e:
            logger.error(f"SQLAlchemy integrity error during create: {e}")
            raise DALValidationError(f"Data integrity violation: {e}")
        except Exception as e:
            logger.error(f"SQLAlchemy create operation failed: {e}")
            raise DALError(f"Create operation failed: {e}")

    async def _execute_read(self, operation: CRUDOperation) -> CRUDResult:
        """Execute READ operation using SQLAlchemy."""
        try:
            async with self.connection_manager.get_session() as session:
                await self._apply_tenant_context(session, operation.tenant_id)

                # Get table metadata
                table = await self._get_table_metadata(operation.table_name, session)
                table_columns = {col.name for col in table.columns}

                # Build query
                stmt = select(table)

                # Add tenant isolation
                if operation.tenant_id:
                    stmt = stmt.where(table.c.tenant_id == operation.tenant_id)

                # Add soft delete filter only if deleted_at column exists
                if "deleted_at" in table_columns:
                    stmt = stmt.where(table.c.deleted_at.is_(None))

                # Add filters
                if operation.filters:
                    for key, value in operation.filters.items():
                        if hasattr(table.c, key):
                            stmt = stmt.where(getattr(table.c, key) == value)

                # Add options (limit, offset, order)
                if operation.options:
                    if "limit" in operation.options:
                        stmt = stmt.limit(operation.options["limit"])
                    if "offset" in operation.options:
                        stmt = stmt.offset(operation.options["offset"])
                    if "order_by" in operation.options:
                        order_column = operation.options["order_by"]
                        if hasattr(table.c, order_column):
                            stmt = stmt.order_by(getattr(table.c, order_column))

                # Execute query
                result = await session.execute(stmt)
                rows = result.fetchall()

                # Convert to dictionaries
                data = []
                for row in rows:
                    row_dict = {}
                    for column in table.columns:
                        row_dict[column.name] = getattr(row, column.name)
                    data.append(row_dict)

                # Return single record if primary identifier filter was used
                if operation.filters and len(data) == 1:
                    id_field = None
                    if "id" in operation.filters:
                        id_field = "id"
                    elif "audit_id" in operation.filters:
                        id_field = "audit_id"
                    if id_field:
                        data = data[0]

                return CRUDResult(
                    success=True,
                    data=data,
                    operation_type=operation.operation_type,
                    affected_rows=len(rows) if isinstance(data, list) else 1,
                )

        except Exception as e:
            logger.error(f"SQLAlchemy read operation failed: {e}")
            raise DALError(f"Read operation failed: {e}")

    async def _execute_update(self, operation: CRUDOperation) -> CRUDResult:
        """Execute UPDATE operation using SQLAlchemy."""
        try:
            if not operation.filters or not any(key in operation.filters for key in ("id", "audit_id")):
                raise DALValidationError("ID filter required for update operation")

            async with self.connection_manager.get_session() as session:
                await self._apply_tenant_context(session, operation.tenant_id)

                # Get table metadata
                table = await self._get_table_metadata(operation.table_name, session)
                table_columns = {col.name for col in table.columns}

                # Prepare update data
                data = self._prepare_update_data(operation)

                # Filter data to only include columns that exist in the table
                filtered_data = {k: v for k, v in data.items() if k in table_columns}

                # Build update statement - check if deleted_at column exists before filtering
                if "id" in operation.filters and "id" in table_columns:
                    id_field = "id"
                elif "audit_id" in operation.filters and "audit_id" in table_columns:
                    id_field = "audit_id"
                else:
                    raise DALValidationError("ID filter required for update operation")

                where_conditions = [
                    table.c[id_field] == operation.filters[id_field],
                    table.c.tenant_id == operation.tenant_id,
                ]
                if "deleted_at" in table_columns:
                    where_conditions.append(table.c.deleted_at.is_(None))

                stmt = update(table).where(and_(*where_conditions)).values(**filtered_data)

                # Execute update
                result = await session.execute(stmt)

                if result.rowcount == 0:
                    return CRUDResult(
                        success=False,
                        error="Record not found or access denied",
                        operation_type=operation.operation_type,
                        affected_rows=0,
                    )

                result_payload = {id_field: operation.filters[id_field], "updated": True, **filtered_data}
                return CRUDResult(
                    success=True,
                    data=result_payload,
                    operation_type=operation.operation_type,
                    affected_rows=result.rowcount,
                )

        except Exception as e:
            logger.error(f"SQLAlchemy update operation failed: {e}")
            raise DALError(f"Update operation failed: {e}")

    async def _execute_delete(self, operation: CRUDOperation) -> CRUDResult:
        """Execute DELETE operation using SQLAlchemy (soft delete)."""
        try:
            if not operation.filters or not any(key in operation.filters for key in ("id", "audit_id")):
                raise DALValidationError("ID filter required for delete operation")

            async with self.connection_manager.get_session() as session:
                await self._apply_tenant_context(session, operation.tenant_id)

                # Get table metadata
                table = await self._get_table_metadata(operation.table_name, session)
                table_columns = {col.name for col in table.columns}

                # Build where conditions
                if "id" in operation.filters and "id" in table_columns:
                    id_field = "id"
                elif "audit_id" in operation.filters and "audit_id" in table_columns:
                    id_field = "audit_id"
                else:
                    raise DALValidationError("ID filter required for delete operation")

                where_conditions = [
                    table.c[id_field] == operation.filters[id_field],
                    table.c.tenant_id == operation.tenant_id,
                ]
                if "deleted_at" in table_columns:
                    where_conditions.append(table.c.deleted_at.is_(None))

                # Prepare update values - only include columns that exist
                update_values = {}
                if "deleted_at" in table_columns:
                    update_values["deleted_at"] = datetime.now(UTC)
                if "updated_by" in table_columns:
                    update_values["updated_by"] = operation.user_id or "system"
                if "updated_at" in table_columns:
                    update_values["updated_at"] = datetime.now(UTC)

                # Perform soft delete by setting deleted_at timestamp (if column exists)
                # Otherwise, perform hard delete
                if "deleted_at" in table_columns:
                    stmt = update(table).where(and_(*where_conditions)).values(**update_values)
                else:
                    # Hard delete if soft delete column doesn't exist
                    stmt = delete(table).where(and_(*where_conditions))

                # Execute soft delete
                result = await session.execute(stmt)

                if result.rowcount == 0:
                    return CRUDResult(
                        success=False,
                        error="Record not found or already deleted",
                        operation_type=operation.operation_type,
                        affected_rows=0,
                    )

                return CRUDResult(
                    success=True,
                    data={id_field: operation.filters[id_field], "deleted": True},
                    operation_type=operation.operation_type,
                    affected_rows=result.rowcount,
                )

        except Exception as e:
            logger.error(f"SQLAlchemy delete operation failed: {e}")
            raise DALError(f"Delete operation failed: {e}")

    def _prepare_create_data(self, operation: CRUDOperation) -> dict[str, Any]:
        """Prepare data for create operation with common fields."""
        data = operation.data.copy() if operation.data else {}

        # Add common fields
        now = datetime.now(UTC)
        data.update(
            {
                "tenant_id": operation.tenant_id,
                "created_at": now,
                "updated_at": now,
                "created_by": operation.user_id or "system",
                "updated_by": operation.user_id or "system",
                "deleted_at": None,
            }
        )
        if operation.table_name not in _NO_RECORD_VERSION_TABLES:
            data.setdefault("record_version", 1)

        # Generate primary key if missing (handles non-standard PK fields)
        pk_field = self._get_primary_key_field(operation.table_name, data)
        if pk_field not in data:
            if operation.table_name in _UUID_PK_TABLES:
                return data
            try:
                if hasattr(ulid, "new"):
                    data[pk_field] = str(ulid.new())
                elif hasattr(ulid, "ULID"):
                    data[pk_field] = str(ulid.ULID())
                else:
                    import uuid

                    data[pk_field] = str(uuid.uuid4())
            except Exception:
                import uuid

                data[pk_field] = str(uuid.uuid4())
        if pk_field != "id":
            data.pop("id", None)

        return data

    def _prepare_update_data(self, operation: CRUDOperation) -> dict[str, Any]:
        """Prepare data for update operation."""
        data = operation.data.copy() if operation.data else {}

        # Add update fields
        data.update({"updated_at": datetime.now(UTC), "updated_by": operation.user_id or "system"})

        # Increment record version if present
        if "record_version" in data:
            data["record_version"] = data["record_version"] + 1

        # Remove fields that shouldn't be updated
        for field in ["id", "tenant_id", "created_at", "created_by", "deleted_at"]:
            data.pop(field, None)

        return data

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

    async def _get_table_metadata(self, table_name: str, session: AsyncSession) -> Table:
        """Get table metadata for dynamic queries."""
        try:
            # Create metadata object
            metadata = MetaData()

            # Reflect the table structure
            async with session.bind.connect() as conn:
                await conn.run_sync(metadata.reflect, only=[table_name])

            if table_name not in metadata.tables:
                raise DALValidationError(f"Table not found: {table_name}")

            return metadata.tables[table_name]

        except Exception as e:
            logger.error(f"Failed to get table metadata for {table_name}: {e}")
            raise DALError(f"Table metadata error: {e}")

    async def _apply_tenant_context(self, session: AsyncSession, tenant_id: str | None) -> None:
        if not tenant_id:
            return
        await session.execute(
            text("SELECT set_config('app.current_tenant', :tenant_id, true)"), {"tenant_id": tenant_id}
        )

    async def execute_raw_query(self, query: str, params: dict[str, Any] | None = None) -> CRUDResult:
        """Execute raw SQL query using SQLAlchemy."""
        if not self._initialized:
            raise DALConnectionError("SQLAlchemy integration not initialized", db_type="sqlalchemy")

        try:
            async with self.connection_manager.get_session() as session:
                result = await session.execute(query, params or {})

                # Handle different result types
                if result.returns_rows:
                    rows = result.fetchall()
                    data = [dict(row._mapping) for row in rows]
                    affected_rows = len(data)
                else:
                    data = {"affected_rows": result.rowcount}
                    affected_rows = result.rowcount

                return CRUDResult(success=True, data=data, operation_type="raw_query", affected_rows=affected_rows)

        except Exception as e:
            logger.error(f"SQLAlchemy raw query failed: {e}")
            raise DALError(f"Raw query execution failed: {e}")

    async def begin_transaction(self):
        """Begin a database transaction."""
        if not self._initialized:
            raise DALConnectionError("SQLAlchemy integration not initialized", db_type="sqlalchemy")

        return self.connection_manager.get_session()

    async def get_table_schema(self, table_name: str) -> dict[str, Any]:
        """Get table schema information."""
        try:
            async with self.connection_manager.get_session() as session:
                table = await self._get_table_metadata(table_name, session)

                schema = {"table_name": table_name, "columns": []}

                for column in table.columns:
                    column_info = {
                        "name": column.name,
                        "type": str(column.type),
                        "nullable": column.nullable,
                        "primary_key": column.primary_key,
                        "default": str(column.default) if column.default else None,
                    }
                    schema["columns"].append(column_info)

                return schema

        except Exception as e:
            logger.error(f"Failed to get schema for table {table_name}: {e}")
            raise DALError(f"Schema retrieval failed: {e}")

    async def health_check(self) -> dict[str, Any]:
        """Perform health check on SQLAlchemy connection."""
        if not self._initialized:
            return {"status": "unhealthy", "error": "Not initialized"}

        try:
            async with self.connection_manager.get_session() as session:
                result = await session.execute(select(1))
                result.fetchone()

            return {
                "status": "healthy",
                "database_type": "sqlalchemy",
                "connection_pool_size": self.connection_manager.config.pool_size,
            }

        except Exception as e:
            logger.error(f"SQLAlchemy health check failed: {e}")
            return {"status": "unhealthy", "error": str(e), "database_type": "sqlalchemy"}

    async def close(self) -> None:
        """Close SQLAlchemy integration and cleanup resources."""
        if self.connection_manager:
            await self.connection_manager.close()

        self._initialized = False
        logger.info("SQLAlchemy fallback integration closed")
