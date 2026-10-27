"""
Hyeve Service Placeholders
=========================

Placeholder implementations for core Hyeve services to enable continuous development.
These provide basic functionality using existing DAL and infrastructure tables.
"""

import asyncio
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol, cast
from uuid import uuid4

import redis  # type: ignore[import-untyped]


class DALServiceProtocol(Protocol):
    async def execute_query(self, query: str, params: tuple = ()) -> list[dict[str, Any]]:
        ...

    async def execute_mutation(self, query: str, params: tuple = ()) -> dict[str, Any]:
        ...


# Import existing DAL service
if TYPE_CHECKING:
    from hyeve_core.src.dal.dal_service import RobustDALService, get_dal_service
else:
    try:
        from hyeve_core.src.dal.dal_service import RobustDALService, get_dal_service
    except ImportError:
        # Fallback for development
        class RobustDALService(DALServiceProtocol):
            async def execute_query(self, query: str, params: tuple = ()) -> list[dict[str, Any]]:
                return []

            async def execute_mutation(self, query: str, params: tuple = ()) -> dict[str, Any]:
                return {"success": True}

        def get_dal_service() -> RobustDALService:
            return RobustDALService()


import logging

logger = logging.getLogger(__name__)


@dataclass
class ServiceResponse:
    """Standard service response format"""

    success: bool
    data: Any = None
    error: str | None = None
    metadata: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class BasePlaceholderService:
    """Base class for all placeholder services"""

    def __init__(self, service_name: str, dal_service: DALServiceProtocol | None = None):
        self.service_name = service_name
        self.dal: DALServiceProtocol = cast(
            DALServiceProtocol, dal_service if dal_service is not None else get_dal_service()
        )
        self.placeholder = True

    async def initialize(self):
        """Initialize the service"""
        logger.info(f"Initializing placeholder service: {self.service_name}")

    async def shutdown(self):
        """Shutdown the service"""
        logger.info(f"Shutting down placeholder service: {self.service_name}")

    async def health_check(self) -> ServiceResponse:
        """Health check for the service"""
        return ServiceResponse(
            success=True, data={"status": "healthy", "service": self.service_name}, metadata={"placeholder": True}
        )

    async def audit_log(
        self,
        tenant_id: str,
        actor_id: str,
        action: str,
        entity: str,
        entity_id: str,
        before: dict | None = None,
        after: dict | None = None,
    ):
        """Log action using existing audit infrastructure"""

        audit_id = f"aud_{uuid4().hex[:8]}"

        try:
            await self.dal.execute_mutation(
                """INSERT INTO admin_audit_log
                   (audit_id, tenant_id, actor_id, action, entity, entity_id, before_json, after_json, at)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)""",
                (
                    audit_id,
                    tenant_id,
                    actor_id,
                    action,
                    entity,
                    entity_id,
                    json.dumps(before) if before else None,
                    json.dumps(after) if after else None,
                    datetime.now(UTC),
                ),
            )
        except Exception as e:
            logger.warning(f"Audit logging failed for {self.service_name}: {e}")

    async def emit_event(self, tenant_id: str, event_type: str, aggregate: str, aggregate_id: str, payload: dict):
        """Emit event using existing outbox pattern"""

        event_id = f"evt_{uuid4().hex[:8]}"

        try:
            await self.dal.execute_mutation(
                """INSERT INTO outbox
                   (event_id, tenant_id, aggregate, aggregate_id, event_type, payload_json, created_at)
                   VALUES ($1, $2, $3, $4, $5, $6, $7)""",
                (event_id, tenant_id, aggregate, aggregate_id, event_type, json.dumps(payload), datetime.now(UTC)),
            )
        except Exception as e:
            logger.warning(f"Event emission failed for {self.service_name}: {e}")


class AuthServicePlaceholder(BasePlaceholderService):
    """Placeholder authentication service"""

    def __init__(self, dal_service=None, **kwargs):
        super().__init__("auth-service", dal_service)

    async def authenticate(self, username: str, password: str, tenant_id: str) -> ServiceResponse:
        """Placeholder authentication - always succeeds for development"""

        logger.info(f"[PLACEHOLDER] Auth request for {username} in tenant {tenant_id}")

        # Simulate basic authentication
        user_data = {
            "user_id": f"usr_{uuid4().hex[:8]}",
            "username": username,
            "tenant_id": tenant_id,
            "roles": ["user"],
            "session_id": f"ses_{uuid4().hex[:8]}",
            "expires_at": (datetime.now(UTC).timestamp() + 3600),  # 1 hour
        }

        # Audit log
        await self.audit_log(
            tenant_id=tenant_id,
            actor_id="system",
            action="AUTHENTICATE",
            entity="user",
            entity_id=str(user_data["user_id"]),
            after={"username": username, "success": True},
        )

        # Emit event
        await self.emit_event(
            tenant_id=tenant_id,
            event_type="user.authenticated.v1",
            aggregate="auth",
            aggregate_id=str(user_data["session_id"]),
            payload=user_data,
        )

        return ServiceResponse(success=True, data=user_data, metadata={"placeholder": True, "service": "auth-service"})

    async def validate_token(self, token: str, tenant_id: str) -> ServiceResponse:
        """Placeholder token validation"""

        logger.info(f"[PLACEHOLDER] Token validation for tenant {tenant_id}")

        # Always validate as successful for development
        return ServiceResponse(
            success=True,
            data={"valid": True, "user_id": f"usr_{uuid4().hex[:8]}", "tenant_id": tenant_id, "roles": ["user"]},
            metadata={"placeholder": True, "service": "auth-service"},
        )

    async def sso(self, provider: str, token: str, tenant_id: str) -> ServiceResponse:
        """Placeholder SSO authentication"""

        logger.info(f"[PLACEHOLDER] SSO request via {provider} for tenant {tenant_id}")

        return ServiceResponse(
            success=True,
            data={
                "user_id": f"usr_{uuid4().hex[:8]}",
                "provider": provider,
                "tenant_id": tenant_id,
                "sso_session_id": f"sso_{uuid4().hex[:8]}",
            },
            metadata={"placeholder": True, "service": "auth-service"},
        )


class AuditServicePlaceholder(BasePlaceholderService):
    """Placeholder audit service"""

    def __init__(self, dal_service=None, **kwargs):
        super().__init__("audit-service", dal_service)

    async def log_event(
        self,
        tenant_id: str,
        actor_id: str | None = None,
        action: str | None = None,
        entity: str | None = None,
        entity_id: str | None = None,
        before: dict | None = None,
        after: dict | None = None,
        **kwargs,
    ) -> ServiceResponse:
        """Log audit event using existing infrastructure"""

        logger.info(f"[PLACEHOLDER] Audit log for tenant {tenant_id}")

        # Handle both direct parameters and event_data dict
        if "event_data" in kwargs:
            event_data = kwargs["event_data"]
            actor_id = actor_id or str(event_data.get("actor_id", "system"))
            action = action or str(event_data.get("action", "UNKNOWN"))
            entity = entity or str(event_data.get("entity", "unknown"))
            entity_id = entity_id or str(event_data.get("entity_id", ""))
            before = before or event_data.get("before")
            after = after or event_data.get("after")

        # Use the base audit_log method
        await self.audit_log(
            tenant_id=tenant_id,
            actor_id=actor_id or "system",
            action=action or "UNKNOWN",
            entity=entity or "unknown",
            entity_id=entity_id or "",
            before=before or {},
            after=after or {},
        )

        return ServiceResponse(
            success=True,
            data={"audit_id": f"aud_{uuid4().hex[:8]}"},
            metadata={"placeholder": True, "service": "audit-service"},
        )

    async def query_logs(self, tenant_id: str, filters: dict | None = None) -> ServiceResponse:
        """Query audit logs"""

        logger.info(f"[PLACEHOLDER] Audit query for tenant {tenant_id}")

        try:
            # Query existing audit logs
            results = await self.dal.execute_query(
                """SELECT audit_id, actor_id, action, entity, entity_id, at
                   FROM admin_audit_log
                   WHERE tenant_id = $1
                   ORDER BY at DESC LIMIT 100""",
                (tenant_id,),
            )

            return ServiceResponse(
                success=True, data={"logs": results}, metadata={"placeholder": True, "service": "audit-service"}
            )

        except Exception as e:
            return ServiceResponse(
                success=False,
                error=f"Query failed: {str(e)}",
                metadata={"placeholder": True, "service": "audit-service"},
            )


class EventServicePlaceholder(BasePlaceholderService):
    """Placeholder event service"""

    def __init__(self, dal_service=None, **kwargs):
        super().__init__("event-service", dal_service)

    async def publish_event(self, tenant_id: str, event_type: str, payload: dict) -> ServiceResponse:
        """Publish event using existing outbox"""

        logger.info(f"[PLACEHOLDER] Publishing event {event_type} for tenant {tenant_id}")

        await self.emit_event(
            tenant_id=tenant_id,
            event_type=event_type,
            aggregate="application",
            aggregate_id=f"app_{uuid4().hex[:8]}",
            payload=payload,
        )

        return ServiceResponse(
            success=True,
            data={"event_id": f"evt_{uuid4().hex[:8]}"},
            metadata={"placeholder": True, "service": "event-service"},
        )

    async def subscribe_event(self, tenant_id: str, event_type: str, callback_url: str) -> ServiceResponse:
        """Subscribe to event type"""

        logger.info(f"[PLACEHOLDER] Event subscription for {event_type} in tenant {tenant_id}")

        return ServiceResponse(
            success=True,
            data={"subscription_id": f"sub_{uuid4().hex[:8]}", "event_type": event_type, "callback_url": callback_url},
            metadata={"placeholder": True, "service": "event-service"},
        )


class CacheServicePlaceholder(BasePlaceholderService):
    """Placeholder cache service using Redis if available"""

    def __init__(self, redis_url=None, default_ttl=300, dal_service=None, **kwargs):
        super().__init__("cache-service", dal_service)
        self.redis_client = None
        self.default_ttl = default_ttl

        try:
            if redis_url:
                self.redis_client = redis.from_url(redis_url, decode_responses=True)
            else:
                self.redis_client = redis.Redis(host="localhost", port=6379, decode_responses=True)
            self.redis_client.ping()
        except Exception:
            logger.warning("Redis not available, using in-memory cache for placeholder")
            self.memory_cache = {}

    async def get(self, key: str, tenant_id: str) -> ServiceResponse:
        """Get cached value"""

        logger.info(f"[PLACEHOLDER] Cache GET {key} for tenant {tenant_id}")

        full_key = f"{tenant_id}:{key}"

        try:
            if self.redis_client:
                value = self.redis_client.get(full_key)
                data = json.loads(value) if value else None
            else:
                data = self.memory_cache.get(full_key)

            return ServiceResponse(
                success=True,
                data={"value": data, "hit": data is not None},
                metadata={"placeholder": True, "service": "cache-service"},
            )

        except Exception as e:
            return ServiceResponse(
                success=False,
                error=f"Cache get failed: {str(e)}",
                metadata={"placeholder": True, "service": "cache-service"},
            )

    async def set(self, key: str, value: Any, tenant_id: str, ttl: int = 300) -> ServiceResponse:
        """Set cached value"""

        logger.info(f"[PLACEHOLDER] Cache SET {key} for tenant {tenant_id}")

        full_key = f"{tenant_id}:{key}"

        try:
            if self.redis_client:
                self.redis_client.setex(full_key, ttl, json.dumps(value))
            else:
                self.memory_cache[full_key] = value
                # Simple TTL simulation (not implemented for memory cache)

            return ServiceResponse(
                success=True, data={"key": key, "ttl": ttl}, metadata={"placeholder": True, "service": "cache-service"}
            )

        except Exception as e:
            return ServiceResponse(
                success=False,
                error=f"Cache set failed: {str(e)}",
                metadata={"placeholder": True, "service": "cache-service"},
            )

    async def delete(self, key: str, tenant_id: str) -> ServiceResponse:
        """Delete cached value"""

        logger.info(f"[PLACEHOLDER] Cache DELETE {key} for tenant {tenant_id}")

        full_key = f"{tenant_id}:{key}"

        try:
            if self.redis_client:
                deleted = self.redis_client.delete(full_key)
            else:
                deleted = 1 if self.memory_cache.pop(full_key, None) is not None else 0

            return ServiceResponse(
                success=True,
                data={"deleted": bool(deleted)},
                metadata={"placeholder": True, "service": "cache-service"},
            )

        except Exception as e:
            return ServiceResponse(
                success=False,
                error=f"Cache delete failed: {str(e)}",
                metadata={"placeholder": True, "service": "cache-service"},
            )


class NotificationServicePlaceholder(BasePlaceholderService):
    """Placeholder notification service"""

    def __init__(self, dal_service=None, **kwargs):
        super().__init__("notification-service", dal_service)

    async def send_email(self, tenant_id: str, to: str, subject: str, body: str) -> ServiceResponse:
        """Send email notification (logged only)"""

        logger.info(f"[PLACEHOLDER] Email to {to} in tenant {tenant_id}: {subject}")

        # Audit the notification
        await self.audit_log(
            tenant_id=tenant_id,
            actor_id="system",
            action="SEND_EMAIL",
            entity="notification",
            entity_id=f"email_{uuid4().hex[:8]}",
            after={"to": to, "subject": subject, "type": "email"},
        )

        return ServiceResponse(
            success=True,
            data={"message_id": f"msg_{uuid4().hex[:8]}", "to": to, "status": "queued"},
            metadata={"placeholder": True, "service": "notification-service"},
        )

    async def send_sms(self, tenant_id: str, to: str, message: str) -> ServiceResponse:
        """Send SMS notification (logged only)"""

        logger.info(f"[PLACEHOLDER] SMS to {to} in tenant {tenant_id}: {message[:50]}...")

        # Audit the notification
        await self.audit_log(
            tenant_id=tenant_id,
            actor_id="system",
            action="SEND_SMS",
            entity="notification",
            entity_id=f"sms_{uuid4().hex[:8]}",
            after={"to": to, "message_preview": message[:50], "type": "sms"},
        )

        return ServiceResponse(
            success=True,
            data={"message_id": f"sms_{uuid4().hex[:8]}", "to": to, "status": "queued"},
            metadata={"placeholder": True, "service": "notification-service"},
        )


# Service factory for easy access
class ServicePlaceholderFactory:
    """Factory for creating placeholder service instances"""

    _instances: dict[str, BasePlaceholderService] = {}

    @classmethod
    def get_service(cls, service_name: str) -> BasePlaceholderService:
        """Get or create placeholder service instance"""

        if service_name not in cls._instances:
            if service_name in {"auth-service", "hyeve-auth-service"}:
                cls._instances[service_name] = AuthServicePlaceholder()
            elif service_name == "audit-service":
                cls._instances[service_name] = AuditServicePlaceholder()
            elif service_name == "event-service":
                cls._instances[service_name] = EventServicePlaceholder()
            elif service_name == "cache-service":
                cls._instances[service_name] = CacheServicePlaceholder()
            elif service_name == "notification-service":
                cls._instances[service_name] = NotificationServicePlaceholder()
            else:
                raise ValueError(f"Unknown service: {service_name}")

        return cls._instances[service_name]

    @classmethod
    def list_services(cls) -> list[str]:
        """List available placeholder services"""
        return [
            "auth-service",
            "hyeve-auth-service",
            "audit-service",
            "event-service",
            "cache-service",
            "notification-service",
        ]


if __name__ == "__main__":
    # Demo the placeholder services
    async def demo():
        print("Hyeve Service Placeholders Demo")
        print("=" * 35)

        # Test each service
        services = [
            (
                "auth-service",
                "authenticate",
                {"username": "test@example.com", "password": "test", "tenant_id": "tenant-123"},
            ),
            (
                "audit-service",
                "log_event",
                {
                    "tenant_id": "tenant-123",
                    "actor_id": "user-456",
                    "action": "CREATE",
                    "entity": "document",
                    "entity_id": "doc-789",
                },
            ),
            (
                "event-service",
                "publish_event",
                {"tenant_id": "tenant-123", "event_type": "document.created.v1", "payload": {"doc_id": "doc-789"}},
            ),
            ("cache-service", "set", {"key": "user:profile", "value": {"name": "John Doe"}, "tenant_id": "tenant-123"}),
            (
                "notification-service",
                "send_email",
                {"tenant_id": "tenant-123", "to": "user@example.com", "subject": "Welcome", "body": "Hello!"},
            ),
        ]

        for service_name, method, kwargs in services:
            try:
                service = ServicePlaceholderFactory.get_service(service_name)
                method_func = getattr(service, method)
                result = await method_func(**kwargs)

                print(f"✅ {service_name}.{method}(): {result.success}")
                if result.data:
                    print(f"   Data: {str(result.data)[:100]}...")

            except Exception as e:
                print(f"❌ {service_name}.{method}(): {e}")

        print("\nAll Placeholder Services Operational!")
        print(f"Available: {', '.join(ServicePlaceholderFactory.list_services())}")

    # Run demo
    asyncio.run(demo())
