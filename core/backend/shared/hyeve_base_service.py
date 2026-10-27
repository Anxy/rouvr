"""
DAL Base Service Classes
Common CRUD operations and patterns for all generated services using DAL.
"""

import asyncio
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Generic, TypeVar
from uuid import uuid4

from backend.shared.service_registry import registry
from hyeve_core.core_libs.types import PackBundle, PackContext, PackLayer
from hyeve_core.dal.dal_config import DALConfig
from hyeve_core.dal.dal_service import init_dal_service
from hyeve_core.dal.interfaces import CRUDOperation
from hyeve_core.packs import LayeredPackResolver, LayeredPackResolverConfig, LayerSourceConfig
from hyeve_core.service_placeholders import ServicePlaceholderFactory
from hyeve_core.telemetry import emit_metric
from hyeve_core.validation import Validator

try:
    import ulid
except Exception:  # pragma: no cover - optional dependency
    ulid = None

# Security and validation
import bleach  # type: ignore[import-untyped]
from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)
TCreate = TypeVar("TCreate", bound=BaseModel)
TUpdate = TypeVar("TUpdate", bound=BaseModel)
# Pack resolver configuration (shared across services)
PACKS_BASE_PATH = Path(
    os.getenv(
        "HYEVE_PACKS_BASE_PATH",
        Path(__file__).resolve().parents[1] / "packs",
    ),
)

_LAYER_SOURCES: list[LayerSourceConfig] = [
    LayerSourceConfig(layer=PackLayer.GLOBAL, base_path=PACKS_BASE_PATH / "global"),
    LayerSourceConfig(layer=PackLayer.PRODUCT, base_path=PACKS_BASE_PATH / "product"),
    LayerSourceConfig(
        layer=PackLayer.INDUSTRY,
        base_path=PACKS_BASE_PATH / "industry",
        value_getter=lambda ctx: ctx.industry,
    ),
    LayerSourceConfig(
        layer=PackLayer.CLIENT,
        base_path=PACKS_BASE_PATH / "client",
        value_getter=lambda ctx: ctx.client_id,
    ),
    LayerSourceConfig(
        layer=PackLayer.TENANT,
        base_path=PACKS_BASE_PATH / "tenant",
        value_getter=lambda ctx: ctx.tenant_id,
    ),
    LayerSourceConfig(
        layer=PackLayer.ENVIRONMENT,
        base_path=PACKS_BASE_PATH / "environment",
        value_getter=lambda ctx: ctx.environment,
    ),
    LayerSourceConfig(
        layer=PackLayer.LOCALE,
        base_path=PACKS_BASE_PATH / "locale",
        value_getter=lambda ctx: ctx.locale,
    ),
]

_RESOLVER_CONFIG = LayeredPackResolverConfig(
    sources=_LAYER_SOURCES,
    cache_size=int(os.getenv("PACK_RESOLVER_CACHE_SIZE", "512")),
)

try:
    pack_resolver = LayeredPackResolver(_RESOLVER_CONFIG)
except Exception as exc:  # pragma: no cover - fallback
    logger.warning("Pack resolver initialization failed: %s", exc)

    class _FallbackPackResolver:
        @staticmethod
        def get(*_args, **_kwargs):
            return SimpleNamespace(validation_rules={})

    pack_resolver = _FallbackPackResolver()

# Constants
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = int(os.getenv("MAX_PAGE_SIZE", "200"))
MAX_FILTER_DEPTH = 5
SANITIZE_ALLOWED_TAGS: list[str] = []
SANITIZE_ALLOWED_ATTRIBUTES: dict[str, Any] = {}


# Service error codes
class ServiceError(RuntimeError):
    """Service error with stable error codes for HTTP mapping"""

    def __init__(self, code: str, params: dict | None = None):
        super().__init__(code)
        self.code, self.params = code, (params or {})

    @property
    def details(self) -> dict:
        return self.params


# DAL base service class with common CRUD operations
class HyeveBaseService(Generic[T, TCreate, TUpdate]):
    """
    Base service class providing common CRUD operations using DAL.
    Generated services should inherit from this class and override
    methods as needed for custom business logic.
    """

    def __init__(self, table_name: str, dal_service=None, auth=None):
        self.table_name = table_name
        self.logger = logging.getLogger(f"hyeve.services.{table_name}")
        self._initialized = False
        self._dal = dal_service
        self._auth = auth
        self._lock = asyncio.Lock()  # Thread safety
        self._cache_ttl = 300  # 5 minutes default
        self._cache_manager = None  # TODO: Implement DAL-compatible caching
        self._cache_enabled = os.getenv("CACHE_ENABLED", "true").lower() in {"1", "true", "yes"}
        self._cache_url = os.getenv("CACHE_URL") or os.getenv("REDIS_URL") or "redis://127.0.0.1:6379/0"

    async def initialize(self):
        """Initialize service dependencies with thread safety"""
        if self._initialized:
            return
        async with self._lock:
            if self._initialized:
                return
            self.logger.info(f"Initializing {self.table_name} service with DAL")
            # Initialize DAL service
            if self._dal is None:
                # Load dal_config.json
                dal_config_path = Path(__file__).resolve().parents[1] / "hyeve_aix_service" / "dal_config.json"
                try:
                    dal_config = DALConfig.load_from_file(str(dal_config_path))
                    self._dal = await init_dal_service(dal_config)
                except FileNotFoundError:
                    self.logger.error(f"DAL config not found at {dal_config_path}")
                    raise
                except Exception as e:
                    self.logger.error(f"Failed to initialize DAL: {e}")
                    raise

            if self._auth is None:
                self._auth = ServicePlaceholderFactory.get_service("auth-service")
                if os.getenv("HYEVE_AUTH_MODE", "").lower() != "local":
                    # Ensure this service is allowed to call auth-service
                    try:
                        registry.ensure_capability("authenticate")
                    except (FileNotFoundError, PermissionError) as exc:
                        self.logger.warning(
                            "Service manifest not found or capability check failed: %s. Continuing without capability check.",
                            exc,
                        )
                    except Exception as exc:
                        self.logger.error("Service is not authorized to call auth-service: %s", exc)
                        raise

            # TODO: Implement caching compatible with DAL
            # if self._cache_enabled and self._cache_manager is None:
            #     try:
            #         # Implement DAL-compatible cache manager
            #         pass
            #     except Exception as exc:
            #         self.logger.warning("Cache manager initialization failed: %s", exc)
            #         self._cache_manager = None
            #         self._cache_enabled = False

            self._initialized = True

    def _normalize_record(self, rec: dict[str, Any]) -> dict[str, Any]:
        """Normalize record field names to snake_case"""
        import re

        normalized = {}
        for k, v in rec.items():
            # Convert camelCase to snake_case
            snake_key = re.sub(r"(?<!^)(?=[A-Z])", "_", k).lower()
            normalized[snake_key] = v
        return normalized

    def _sanitize_input(self, data: dict[str, Any]) -> dict[str, Any]:
        """Sanitize input data for security - only user text fields"""
        sanitized: dict[str, Any] = {}
        for k, v in data.items():
            key_lower = k.lower()
            if isinstance(v, str):
                if (
                    k
                    not in {
                        "id",
                        "tenant_id",
                        "correlation_id",
                        "created_by",
                        "updated_by",
                    }
                    and "config" not in key_lower
                    and "data" not in key_lower
                ):
                    sanitized[k] = bleach.clean(
                        v,
                        tags=SANITIZE_ALLOWED_TAGS,
                        attributes=SANITIZE_ALLOWED_ATTRIBUTES,
                    )
                else:
                    sanitized[k] = v
            elif isinstance(v, dict):
                # Do NOT sanitize nested configs/data at all
                if "config" in key_lower or "data" in key_lower:
                    sanitized[k] = v
                else:
                    sanitized[k] = self._sanitize_input(v)
            else:
                sanitized[k] = v
        return sanitized

    def _filter_validation_payload(self, data: dict[str, Any], bundle: Any) -> dict[str, Any]:
        schema = getattr(bundle, "schema", None)
        if isinstance(schema, dict):
            props = schema.get("properties")
            if isinstance(props, dict):
                return {k: v for k, v in data.items() if k in props}
        return data

    def _serialize_for_validation(self, value: Any) -> Any:
        """Ensure validation sees JSON-friendly types (e.g., datetime -> ISO strings)."""
        if isinstance(value, dict):
            return {key: self._serialize_for_validation(val) for key, val in value.items()}
        if isinstance(value, list):
            return [self._serialize_for_validation(item) for item in value]
        if isinstance(value, datetime):
            return value.isoformat()
        return value

    def _serialize_violation(self, violation: Any) -> Any:
        if hasattr(violation, "to_dict"):
            try:
                return violation.to_dict()
            except Exception:
                pass
        if hasattr(violation, "model_dump"):
            try:
                return violation.model_dump()
            except Exception:
                pass
        if isinstance(violation, dict):
            return violation
        data = getattr(violation, "__dict__", None)
        if data:
            return data
        return {"message": str(violation)}

    def _build_pack_context(
        self,
        tenant_id: str,
        pack_context: PackContext | None = None,
    ) -> PackContext:
        """Build or reuse a PackContext for validation lookups."""
        if pack_context is not None:
            return pack_context
        return PackContext(
            service=f"{self.table_name}_service",
            entity=self.table_name,
            version=os.getenv("HYEVE_AIX_SERVICE_PACK_VERSION", "current"),
            tenant_id=tenant_id,
            locale="en",
        )

    def _get_pack_field_rules(
        self,
        bundle: PackBundle,
        *,
        allow_required: bool = True,
    ) -> dict[str, Any]:
        """Extract field-level validation rules from the bundle."""
        validation_rules = bundle.validation_rules or {}
        field_rules = (
            validation_rules.get("_shared") if isinstance(validation_rules.get("_shared"), dict) else validation_rules
        )
        if not isinstance(field_rules, dict):
            return {}
        if allow_required:
            return field_rules
        return {
            field: {**rules, "required": False} if isinstance(rules, dict) else rules
            for field, rules in field_rules.items()
        }

    def _prune_field_rules(self, field_rules: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        """Remove validation rules for fields not present in the payload."""
        if not isinstance(field_rules, dict):
            return {}
        return {key: rules for key, rules in field_rules.items() if key in payload}

    async def create(
        self,
        data: TCreate,
        tenant_id: str,
        user_id: str | None = None,
        correlation_id: str | None = None,
        pack_context: PackContext | None = None,
    ) -> T:
        """Create new record with validation and business logic using DAL"""
        service_identifier = f"{self.table_name}_service"
        bundle = pack_resolver.get(self._build_pack_context(tenant_id, pack_context))
        emit_metric("service_validate_total", {"service": service_identifier, "entity": self.table_name})
        emit_metric("service_crud_total", {"op": "create", "table": self.table_name})
        self.logger.info(
            f"Creating {self.table_name} record for tenant {tenant_id} using DAL",
            extra={"correlation_id": correlation_id},
        )
        if not self._initialized:
            await self.initialize()

        try:
            # Validate input with Pydantic model
            validated_data = data.model_validate(data.model_dump())
            record_data = validated_data.model_dump(exclude_none=True)
            model_fields = type(data).model_fields

            if "correlation_id" in model_fields and "correlation_id" not in record_data:
                record_data["correlation_id"] = correlation_id or f"corr-{uuid4().hex}"

            # Enforce tenant isolation centrally
            record_data["tenant_id"] = tenant_id

            # Enforce tenant/user in payload
            if user_id:
                if "created_by" in model_fields:
                    record_data.setdefault("created_by", user_id)
                if "updated_by" in model_fields:
                    record_data["updated_by"] = user_id

            # Input validation and sanitization
            sanitized_data = self._sanitize_input(record_data)

            # Central validation
            field_rules = self._get_pack_field_rules(bundle, allow_required=True)
            validation_payload = self._filter_validation_payload(sanitized_data, bundle)
            validation_data = self._serialize_for_validation(validation_payload)
            field_rules = self._prune_field_rules(field_rules, validation_data)
            violations = Validator.validate(validation_data, field_rules)
            if violations:
                raise ServiceError("VAL.INVALID", {"violations": [self._serialize_violation(v) for v in violations]})

            # Business logic validation
            await self._validate_create_data(sanitized_data, tenant_id)

            # Apply business rules
            processed_data = await self._apply_create_business_rules(sanitized_data, tenant_id, user_id)

            # Use DAL create operation
            operation = CRUDOperation(
                table_name=self.table_name,
                operation_type="create",
                data=processed_data,
                tenant_id=tenant_id,
            )
            result = await self._execute_crud(operation)

            if result.success:
                created_record = result.data
            else:
                raise ServiceError(
                    "DAL.CREATE_FAILED",
                    {"error": getattr(result, "error_message", None) or getattr(result, "error", None)},
                )

            # Post-creation processing
            await self._post_create_processing(created_record, tenant_id, user_id)

            self.logger.info(f"Successfully created {self.table_name} with ID: {created_record.get('id')} using DAL")

            # TODO: Implement caching
            # await self._cache_set_record(created_record.get("id"), tenant_id, created_record)

            return self._create_response_model(created_record)

        except ValidationError as e:
            self.logger.warning(f"Validation error: {e}")
            raise ServiceError("VAL.INVALID", {"detail": str(e)}) from e
        except ServiceError:
            raise  # Re-raise service errors
        except Exception as e:
            self.logger.error(f"Failed to create {self.table_name} using DAL: {e}")
            raise ServiceError("DAL.CREATE_FAILED", {"table": self.table_name}) from e

    async def get_by_id(self, record_id: str, tenant_id: str, correlation_id: str | None = None) -> T | None:
        """Get record by ID with caching using DAL"""
        #        emit_metric("service_crud_total", {
        #            "op": "read",
        #            "table": self.table_name
        #        })
        self.logger.info(
            f"Retrieving {self.table_name} with ID: {record_id} for tenant {tenant_id} using DAL",
            extra={"correlation_id": correlation_id},
        )
        if not self._initialized:
            await self.initialize()

        try:
            # Input validation

            # TODO: Implement caching
            # cache_hit = await self._cache_get_record(record_id, tenant_id)
            # if cache_hit:
            #     return self._create_response_model(cache_hit)

            # Use DAL read operation
            operation = CRUDOperation(
                table_name=self.table_name,
                operation_type="read",
                filters={"id": record_id, "tenant_id": tenant_id},
                tenant_id=tenant_id,
            )
            result = await self._execute_crud(operation)

            if result.success and result.data:
                record = result.data[0] if isinstance(result.data, list) else result.data
            else:
                raise ServiceError("DAL.NOT_FOUND", {"id": record_id})

            # Apply read business rules
            processed_record = await self._apply_read_business_rules(record, tenant_id)

            # TODO: Implement caching
            # await self._cache_set_record(record_id, tenant_id, processed_record)

            return self._create_response_model(processed_record)

        except ServiceError:
            raise
        except Exception as e:
            self.logger.error(f"Failed to retrieve {self.table_name} {record_id} using DAL: {e}")
            raise ServiceError("DAL.READ_FAILED", {"table": self.table_name}) from e

    async def update(
        self,
        record_id: str,
        data: TUpdate,
        tenant_id: str,
        user_id: str | None = None,
        correlation_id: str | None = None,
        pack_context: PackContext | None = None,
    ) -> T | None:
        """Update record with validation and business logic using DAL"""
        service_identifier = f"{self.table_name}_service"
        bundle = pack_resolver.get(self._build_pack_context(tenant_id, pack_context))
        emit_metric("service_validate_total", {"service": service_identifier, "entity": self.table_name})
        emit_metric("service_crud_total", {"op": "update", "table": self.table_name})
        self.logger.info(
            f"Updating {self.table_name} {record_id} for tenant {tenant_id} using DAL",
            extra={"correlation_id": correlation_id},
        )
        if not self._initialized:
            await self.initialize()

        try:
            # Validate input with Pydantic model
            validated_data = data.model_validate(data.model_dump())
            update_data = validated_data.model_dump(exclude_none=True)
            model_fields = type(data).model_fields

            # Enforce tenant isolation centrally

            # Enforce tenant/user in payload
            if user_id and "updated_by" in model_fields:
                update_data["updated_by"] = user_id

            # Input validation
            sanitized_data = self._sanitize_input(update_data)

            # Central validation
            field_rules = self._get_pack_field_rules(bundle, allow_required=False)
            validation_payload = self._filter_validation_payload(sanitized_data, bundle)
            validation_data = self._serialize_for_validation(validation_payload)
            field_rules = self._prune_field_rules(field_rules, validation_data)
            violations = Validator.validate(validation_data, field_rules)
            if violations:
                raise ServiceError("VAL.INVALID", {"violations": [self._serialize_violation(v) for v in violations]})

            # Business logic validation
            await self._validate_update_data(record_id, sanitized_data, tenant_id)

            # Apply business rules
            processed_data = await self._apply_update_business_rules(record_id, sanitized_data, tenant_id, user_id)

            # Use DAL update operation
            operation = CRUDOperation(
                table_name=self.table_name,
                operation_type="update",
                data=processed_data,
                filters={"id": record_id, "tenant_id": tenant_id},
                tenant_id=tenant_id,
            )
            result = await self._execute_crud(operation)

            if result.success:
                updated_record = result.data
            else:
                raise ServiceError(
                    "DAL.UPDATE_FAILED",
                    {"error": getattr(result, "error_message", None) or getattr(result, "error", None)},
                )

            if not isinstance(updated_record, dict) or "tenant_id" not in updated_record:
                refreshed = await self.get_by_id(record_id, tenant_id)
                if refreshed is not None:
                    return refreshed

            # Post-update processing
            await self._post_update_processing(updated_record, tenant_id, user_id)

            # TODO: Implement caching
            # await self._cache_set_record(updated_record.get("id"), tenant_id, updated_record)

            return self._create_response_model(updated_record)

        except ValidationError as e:
            self.logger.warning(f"Validation error: {e}")
            raise ServiceError("VAL.INVALID", {"detail": str(e)}) from e
        except ServiceError:
            raise
        except Exception as e:
            self.logger.error(f"Failed to update {self.table_name} {record_id} using DAL: {e}")
            raise ServiceError("DAL.UPDATE_FAILED", {"table": self.table_name}) from e

    async def delete(
        self,
        record_id: str,
        tenant_id: str,
        user_id: str | None = None,
        correlation_id: str | None = None,
    ) -> bool:
        """Delete record using DAL"""
        #        emit_metric("service_crud_total", {
        #            "op": "delete",
        #            "table": self.table_name
        #        })
        self.logger.info(
            f"Deleting {self.table_name} with ID: {record_id} for tenant {tenant_id} using DAL",
            extra={"correlation_id": correlation_id},
        )
        if not self._initialized:
            await self.initialize()

        try:
            # Input validation

            # Business logic validation
            await self._validate_delete(record_id, tenant_id)

            # Use DAL delete operation
            operation = CRUDOperation(
                table_name=self.table_name,
                operation_type="delete",
                filters={"id": record_id, "tenant_id": tenant_id},
                tenant_id=tenant_id,
            )
            result = await self._execute_crud(operation)

            if not result.success:
                raise ServiceError(
                    "DAL.DELETE_FAILED",
                    {"error": getattr(result, "error_message", None) or getattr(result, "error", None)},
                )

            # Post-delete processing
            await self._post_delete_processing({"id": record_id}, tenant_id, user_id)

            self.logger.info(f"Successfully deleted {self.table_name} {record_id} using DAL")

            # TODO: Implement caching
            # await self._cache_evict_record(record_id, tenant_id)

            return True

        except Exception as e:
            self.logger.error(f"Failed to delete {self.table_name} {record_id} using DAL: {e}")
            raise ServiceError("DAL.DELETE_FAILED", {"table": self.table_name}) from e

    async def list(
        self,
        tenant_id: str,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
        filters: dict[str, Any] | None = None,
        sort_by: str | None = None,
        sort_order: str = "asc",
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        """List records with database-level pagination and filtering using DAL"""
        #        emit_metric("service_crud_total", {
        #            "op": "list",
        #            "table": self.table_name
        #        })
        self.logger.info(
            f"Listing {self.table_name} records for tenant {tenant_id} using DAL: limit={limit}, offset={offset}",
            extra={"correlation_id": correlation_id},
        )
        if not self._initialized:
            await self.initialize()

        try:
            # Input validation

            # Validate pagination parameters
            if limit < 1 or limit > MAX_PAGE_SIZE:
                self.logger.warning(f"Clamping limit {limit} to valid range [1, {MAX_PAGE_SIZE}]")
                limit = min(max(limit, 1), MAX_PAGE_SIZE)
            offset = max(offset, 0)

            # Sanitize and validate filters
            sanitized_filters = self._sanitize_filters(filters) if filters else {}

            # Apply default filters (tenant isolation)
            sanitized_filters = await self._apply_default_filters(sanitized_filters, tenant_id)

            # Use DAL list operation
            operation = CRUDOperation(
                table_name=self.table_name,
                operation_type="read",
                filters=sanitized_filters,
                tenant_id=tenant_id,
                options={"limit": limit, "offset": offset, "sort_by": sort_by, "sort_order": sort_order},
            )
            result = await self._execute_crud(operation)

            if result.success:
                records = result.data if isinstance(result.data, list) else [result.data] if result.data else []
                total_count = getattr(result, "total_count", len(records))
            else:
                raise ServiceError(
                    "DAL.LIST_FAILED",
                    {"error": getattr(result, "error_message", None) or getattr(result, "error", None)},
                )

            # Apply read business rules to each record
            processed_records = []
            for record in records:
                processed = await self._apply_read_business_rules(record, tenant_id)
                processed_records.append(self._create_response_model(processed))

            return {
                "items": processed_records,
                "total": total_count,
                "limit": limit,
                "offset": offset,
                "has_more": (offset + len(processed_records)) < total_count,
            }

        except Exception as e:
            self.logger.error(f"Failed to list {self.table_name} records using DAL: {e}")
            raise ServiceError("DAL.LIST_FAILED", {"table": self.table_name}) from e

    async def _execute_crud(self, operation: CRUDOperation):
        # Execute DAL CRUD operation with compatibility for older interfaces.
        if hasattr(self._dal, "execute_crud"):
            return await self._dal.execute_crud(operation)
        if hasattr(self._dal, "execute_crud_operation"):
            return await self._dal.execute_crud_operation(operation)
        exec_op = getattr(self._dal, "_execute_crud_operation", None)
        if exec_op is not None:
            return await exec_op(operation)
        raise AttributeError("DAL service missing execute_crud/execute_crud_operation")

    def _sanitize_filters(self, filters: dict[str, Any]) -> dict[str, Any]:
        """Sanitize and validate filter parameters"""

        def _depth(v, maxd=MAX_FILTER_DEPTH):
            if not isinstance(v, dict):
                return 0
            return 1 + max((_depth(x, maxd) for x in v.values()), default=0)

        sanitized = {}
        for key, value in filters.items():
            # Prevent deep nesting attacks
            if isinstance(value, dict):
                if _depth(value) > MAX_FILTER_DEPTH:
                    continue
                if len(str(value)) > 1000:
                    continue
            if isinstance(value, str):
                # Skip bleach for exact-match keys and IDs
                if key.endswith("_id") or key in {f"{self.table_name}_type", f"{self.table_name}_name"}:
                    sanitized[key] = value
                else:
                    sanitized[key] = bleach.clean(value, tags=[], attributes={})
            else:
                sanitized[key] = value
        return sanitized

    # === BUSINESS LOGIC HOOKS ===
    # These methods can be overridden in subclasses for custom behavior
    async def _validate_create_data(self, data: dict[str, Any], tenant_id: str) -> None:
        """Validate data for creation with metadata-driven rules"""
        # Tenant isolation validation - now handled centrally by TenantGuards
        # Business rules validation
        await self._validate_business_rules(data, "create")

    async def _validate_update_data(self, record_id: str, data: dict[str, Any], tenant_id: str) -> None:
        """Validate data for update"""
        # Business rules validation
        await self._validate_business_rules(data, "update")

    async def _validate_delete(self, record_id: str, tenant_id: str) -> None:
        """Validate deletion permissions"""
        # Add business logic validation here
        pass

    async def _validate_business_rules(self, data: dict[str, Any], operation: str) -> None:
        """Business rules validation"""
        # Add domain-specific business rules here
        # This can be extended based on pack metadata workflows
        pass

    @staticmethod
    def _generate_ulid() -> str:
        """Generate a ULID string compatible with available ulid packages."""
        if ulid and hasattr(ulid, "new"):
            return ulid.new().str
        if ulid and hasattr(ulid, "ULID"):
            return str(ulid.ULID())
        return f"ulid_{uuid4().hex[:16]}"

    async def _apply_create_business_rules(
        self,
        data: dict[str, Any],
        tenant_id: str,
        user_id: str | None,
    ) -> dict[str, Any]:
        """Apply business rules for create operations"""
        result_data = data.copy()
        if not result_data.get("id"):
            result_data["id"] = self._generate_ulid()
        # Add audit fields
        if "created_at" not in result_data:
            result_data["created_at"] = datetime.now(UTC)
        if "updated_at" not in result_data:
            result_data["updated_at"] = datetime.now(UTC)
        return result_data

    async def _apply_update_business_rules(
        self,
        record_id: str,
        data: dict[str, Any],
        tenant_id: str,
        user_id: str | None,
    ) -> dict[str, Any]:
        """Apply business rules for update operations"""
        result_data = data.copy()
        # Update audit fields
        if "updated_at" not in result_data:
            result_data["updated_at"] = datetime.now(UTC)
        return result_data

    async def _apply_read_business_rules(self, item: dict[str, Any], tenant_id: str) -> dict[str, Any]:
        """Apply business rules for read operations"""
        # Apply any read-time transformations or filtering
        return item

    async def _apply_default_filters(self, filters: dict[str, Any], tenant_id: str) -> dict[str, Any]:
        """Apply default filters based on context"""
        # Add tenant filtering
        filters["tenant_id"] = tenant_id
        # Filter soft-deleted items if applicable
        # if self._has_soft_delete():
        #     filters['deleted_at'] = None
        return filters

    async def _post_create_processing(self, item: dict[str, Any], tenant_id: str, user_id: str | None) -> None:
        """Post-creation processing"""
        # Implement any post-creation logic (notifications, events, etc.)
        pass

    async def _post_update_processing(self, item: dict[str, Any], tenant_id: str, user_id: str | None) -> None:
        """Post-update processing"""
        # Implement any post-update logic
        pass

    async def _post_delete_processing(self, item: dict[str, Any], tenant_id: str, user_id: str | None) -> None:
        """Post-delete processing"""
        # Implement any post-delete logic
        pass

    def _create_response_model(self, data: dict[str, Any]) -> T:
        """Create response model from data - must be implemented by subclasses"""
        raise NotImplementedError("Subclasses must implement _create_response_model")

    def _has_soft_delete(self) -> bool:
        """Check if model supports soft delete - can be overridden"""
        return False

    def _has_timestamps(self) -> bool:
        """Check if model has timestamp fields - can be overridden"""
        return True
