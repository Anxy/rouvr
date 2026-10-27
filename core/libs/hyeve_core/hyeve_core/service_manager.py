"""
Service Manager - Integration layer for service registry and placeholder services
Manages service lifecycle, discovery, and graceful fallbacks
"""

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any, NamedTuple

# Use simple DAL if RobustDALService not available
try:
    from hyeve_core.src.dal.dal_service import RobustDALService
except ImportError:

    class RobustDALService:  # type: ignore
        async def initialize(self):
            pass

        async def shutdown(self):
            pass


from hyeve_core.service_placeholders import (
    AuditServicePlaceholder,
    AuthServicePlaceholder,
    CacheServicePlaceholder,
    EventServicePlaceholder,
    NotificationServicePlaceholder,
)


class BaseService:
    """Base service interface"""

    async def initialize(self):
        pass

    async def shutdown(self):
        pass

    async def health_check(self):
        return {"status": "healthy"}


class ServiceRegistration(NamedTuple):
    service_name: str
    service_type: str
    version: str
    description: str
    dependencies: list[str]
    endpoints: dict[str, str]
    health_check_path: str
    config_schema: dict[str, Any] | None = None


class SimpleServiceRegistry:
    """Simple service registry placeholder"""

    def __init__(self, dal_service):
        self.dal_service = dal_service
        self.registrations: dict[str, ServiceRegistration] = {}

    async def initialize(self):
        pass

    async def register_service(self, registration: ServiceRegistration):
        self.registrations[registration.service_name] = registration

    async def get_service_registration(self, service_name: str) -> ServiceRegistration | None:
        return self.registrations.get(service_name)

    async def list_services(self) -> list[ServiceRegistration]:
        return list(self.registrations.values())

    async def shutdown(self):
        pass


logger = logging.getLogger(__name__)


class ServiceManager:
    """
    Manages the lifecycle and integration of services in the Hyeve ecosystem.
    Provides service discovery, placeholder management, and graceful degradation.
    """

    def __init__(self, dal_service: RobustDALService):
        self.dal_service = dal_service
        self.service_registry = SimpleServiceRegistry(dal_service)
        self.active_services: dict[str, BaseService] = {}
        self.placeholder_services: dict[str, type] = {
            "auth_service": AuthServicePlaceholder,
            "audit_service": AuditServicePlaceholder,
            "event_service": EventServicePlaceholder,
            "cache_service": CacheServicePlaceholder,
            "notification_service": NotificationServicePlaceholder,
        }
        self.service_configs: dict[str, dict[str, Any]] = {}

    async def initialize(self) -> bool:
        """Initialize the service manager and register placeholder services"""
        try:
            logger.info("Initializing Service Manager...")

            # Initialize service registry
            await self.service_registry.initialize()

            # Register placeholder services if not already registered
            await self._register_placeholder_services()

            # Start essential services
            await self._start_essential_services()

            logger.info("Service Manager initialized successfully")
            return True

        except Exception as e:
            logger.error(f"Failed to initialize Service Manager: {e}")
            return False

    async def _register_placeholder_services(self):
        """Register placeholder services with the service registry"""

        placeholder_registrations = [
            ServiceRegistration(
                service_name="auth_service",
                service_type="placeholder",
                version="1.0.0",
                description="Authentication service placeholder",
                dependencies=["dal_service"],
                endpoints={
                    "authenticate": "/auth/authenticate",
                    "validate_token": "/auth/validate",
                    "create_user": "/auth/users",
                    "get_user": "/auth/users/{user_id}",
                },
                health_check_path="/auth/health",
                config_schema={
                    "jwt_secret": {"type": "string", "required": True},
                    "token_expiry": {"type": "integer", "default": 3600},
                },
            ),
            ServiceRegistration(
                service_name="audit_service",
                service_type="placeholder",
                version="1.0.0",
                description="Audit logging service placeholder",
                dependencies=["dal_service"],
                endpoints={"log_event": "/audit/log", "query_logs": "/audit/query"},
                health_check_path="/audit/health",
            ),
            ServiceRegistration(
                service_name="event_service",
                service_type="placeholder",
                version="1.0.0",
                description="Event processing service placeholder",
                dependencies=["dal_service"],
                endpoints={"publish": "/events/publish", "subscribe": "/events/subscribe"},
                health_check_path="/events/health",
            ),
            ServiceRegistration(
                service_name="cache_service",
                service_type="placeholder",
                version="1.0.0",
                description="Caching service placeholder",
                dependencies=[],
                endpoints={"get": "/cache/get", "set": "/cache/set", "delete": "/cache/delete"},
                health_check_path="/cache/health",
                config_schema={
                    "redis_url": {"type": "string", "required": False},
                    "default_ttl": {"type": "integer", "default": 300},
                },
            ),
            ServiceRegistration(
                service_name="notification_service",
                service_type="placeholder",
                version="1.0.0",
                description="Notification service placeholder",
                dependencies=["dal_service"],
                endpoints={
                    "send_notification": "/notifications/send",
                    "get_notifications": "/notifications/user/{user_id}",
                },
                health_check_path="/notifications/health",
            ),
        ]

        for registration in placeholder_registrations:
            try:
                await self.service_registry.register_service(registration)
                logger.info(f"Registered placeholder service: {registration.service_name}")
            except Exception as e:
                logger.warning(f"Failed to register {registration.service_name}: {e}")

    async def _start_essential_services(self):
        """Start essential services (placeholders for now)"""
        essential_services = ["auth_service", "audit_service", "cache_service"]

        for service_name in essential_services:
            try:
                await self.start_service(service_name)
                logger.info(f"Started essential service: {service_name}")
            except Exception as e:
                logger.error(f"Failed to start essential service {service_name}: {e}")

    async def start_service(self, service_name: str, config: dict[str, Any] | None = None) -> bool:
        """Start a service by name"""
        try:
            # Check if service is already running
            if service_name in self.active_services:
                logger.info(f"Service {service_name} is already running")
                return True

            # Get service registration
            registration = await self.service_registry.get_service_registration(service_name)
            if not registration:
                logger.error(f"No registration found for service: {service_name}")
                return False

            # Use placeholder if available
            if service_name in self.placeholder_services:
                service_class = self.placeholder_services[service_name]

                # Initialize with config
                service_config = config or {}
                if service_name == "cache_service":
                    # Special config for cache service
                    service_config.setdefault("redis_url", None)
                    service_config.setdefault("default_ttl", 300)

                service_instance = service_class(dal_service=self.dal_service, **service_config)

                # Initialize the service
                await service_instance.initialize()

                # Store active service
                self.active_services[service_name] = service_instance
                self.service_configs[service_name] = service_config

                logger.info(f"Started placeholder service: {service_name}")
                return True

            logger.warning(f"No implementation available for service: {service_name}")
            return False

        except Exception as e:
            logger.error(f"Failed to start service {service_name}: {e}")
            return False

    async def stop_service(self, service_name: str) -> bool:
        """Stop a running service"""
        try:
            if service_name in self.active_services:
                service = self.active_services[service_name]

                # Call shutdown if available
                if hasattr(service, "shutdown"):
                    await service.shutdown()

                # Remove from active services
                del self.active_services[service_name]
                if service_name in self.service_configs:
                    del self.service_configs[service_name]

                logger.info(f"Stopped service: {service_name}")
                return True

            logger.warning(f"Service {service_name} is not running")
            return False

        except Exception as e:
            logger.error(f"Failed to stop service {service_name}: {e}")
            return False

    async def get_service(self, service_name: str) -> BaseService | None:
        """Get an active service instance"""
        return self.active_services.get(service_name)

    async def list_services(self) -> dict[str, dict[str, Any]]:
        """List all services and their status"""
        services: dict[str, dict[str, Any]] = {}

        # Get all registered services
        registrations = await self.service_registry.list_services()

        for registration in registrations:
            service_name = registration.service_name
            is_active = service_name in self.active_services

            services[service_name] = {
                "registration": registration,
                "active": is_active,
                "type": registration.service_type,
                "version": registration.version,
                "description": registration.description,
            }

            if is_active:
                service = self.active_services[service_name]
                services[service_name]["instance"] = service
                services[service_name]["config"] = self.service_configs.get(service_name, {})

        return services

    async def health_check(self) -> dict[str, Any]:
        """Perform health check on all active services"""
        health_status: dict[str, Any] = {
            "manager": "healthy",
            "services": {},
            "timestamp": datetime.now(UTC).isoformat(),
        }

        for service_name, service in self.active_services.items():
            try:
                if hasattr(service, "health_check"):
                    service_health = await service.health_check()
                    health_status["services"][service_name] = service_health or "healthy"
                else:
                    health_status["services"][service_name] = "healthy"
            except Exception as e:
                health_status["services"][service_name] = f"unhealthy: {e}"

        return health_status

    async def restart_service(self, service_name: str) -> bool:
        """Restart a service"""
        try:
            # Get current config
            config = self.service_configs.get(service_name, {})

            # Stop service
            await self.stop_service(service_name)

            # Wait a moment
            await asyncio.sleep(0.1)

            # Start service
            return await self.start_service(service_name, config)

        except Exception as e:
            logger.error(f"Failed to restart service {service_name}: {e}")
            return False

    async def shutdown(self):
        """Shutdown all services and the service manager"""
        logger.info("Shutting down Service Manager...")

        # Stop all active services
        for service_name in list(self.active_services.keys()):
            await self.stop_service(service_name)

        # Shutdown service registry
        if hasattr(self.service_registry, "shutdown"):
            await self.service_registry.shutdown()

        logger.info("Service Manager shutdown complete")


async def demo_service_manager():
    """Demonstration of the Service Manager"""

    # Initialize DAL service
    dal_service = RobustDALService()
    await dal_service.initialize()

    # Create service manager
    manager = ServiceManager(dal_service)

    # Initialize
    success = await manager.initialize()
    if not success:
        print("Failed to initialize Service Manager")
        return

    print("=== Service Manager Demo ===")

    # List services
    print("\n1. Listing all services:")
    services = await manager.list_services()
    for name, info in services.items():
        status = "ACTIVE" if info["active"] else "INACTIVE"
        print(f"  {name}: {status} ({info['type']}) - {info['description']}")

    # Health check
    print("\n2. Health check:")
    health = await manager.health_check()
    print(f"  Manager: {health['manager']}")
    for service_name, status in health["services"].items():
        print(f"  {service_name}: {status}")

    # Test service functionality
    print("\n3. Testing service functionality:")

    # Test auth service
    auth_service = await manager.get_service("auth_service")
    if auth_service:
        try:
            if hasattr(auth_service, "authenticate"):
                result = await auth_service.authenticate("test_tenant", "testuser", "password")  # type: ignore
                print(f"  Auth test: {result.message}")
            else:
                print("  Auth test: Service active but interface not available")
        except Exception as e:
            print(f"  Auth test error: {e}")

    # Test audit service
    audit_service = await manager.get_service("audit_service")
    if audit_service:
        try:
            if hasattr(audit_service, "log_event"):
                await audit_service.log_event(  # type: ignore
                    tenant_id="test_tenant",
                    actor_id="testuser",
                    action="TEST_ACTION",
                    entity="test_entity",
                    entity_id="123",
                )
                print("  Audit test: Event logged successfully")
            else:
                print("  Audit test: Service active but interface not available")
        except Exception as e:
            print(f"  Audit test error: {e}")

    # Test cache service
    cache_service = await manager.get_service("cache_service")
    if cache_service:
        try:
            if hasattr(cache_service, "set") and hasattr(cache_service, "get"):
                await cache_service.set("test_tenant", "test_key", {"data": "test_value"})  # type: ignore
                result = await cache_service.get("test_tenant", "test_key")  # type: ignore
                print(f"  Cache test: {result.message}")
            else:
                print("  Cache test: Service active but interface not available")
        except Exception as e:
            print(f"  Cache test error: {e}")

    print("\n4. Service restart test:")
    restart_success = await manager.restart_service("cache_service")
    print(f"  Cache service restart: {'SUCCESS' if restart_success else 'FAILED'}")

    # Final health check
    print("\n5. Final health check:")
    health = await manager.health_check()
    for service_name, status in health["services"].items():
        print(f"  {service_name}: {status}")

    # Cleanup
    await manager.shutdown()
    await dal_service.shutdown()

    print("\n=== Demo Complete ===")


if __name__ == "__main__":
    asyncio.run(demo_service_manager())
