"""
Service Loader
==============

Dynamic service loading and integration for BIVR code generation.
Enables automatic loading of services based on tenant subscriptions.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from .registry import ServiceManifest, ServiceRegistry, SubscriptionTier, TenantSubscription, get_registry

logger = logging.getLogger(__name__)


@dataclass
class ServiceInstance:
    """Represents a loaded service instance"""

    name: str
    version: str
    instance: Any
    manifest: ServiceManifest | None
    subscription: TenantSubscription | None = None

    def has_capability(self, capability: str) -> bool:
        """Check if service provides capability for current subscription"""
        if not self.subscription or not self.manifest:
            return False

        return self.subscription.has_capability(capability, self.manifest)


class PlaceholderService:
    """Placeholder service for missing dependencies"""

    def __init__(self, service_name: str, reason: str = "Service not available"):
        self.service_name = service_name
        self.reason = reason
        self._methods: dict[str, Callable] = {}

    def __getattr__(self, name: str):
        """Return placeholder method that logs missing service call"""

        def placeholder_method(*args, **kwargs):
            logger.warning(f"Called {name} on placeholder service '{self.service_name}': {self.reason}")
            return {
                "error": f"Service '{self.service_name}' not available",
                "reason": self.reason,
                "method": name,
                "placeholder": True,
            }

        return placeholder_method


class ServiceLoader:
    """
    Dynamic service loader for BIVR ecosystem

    Features:
    - Load services based on tenant subscriptions
    - Create placeholder services for missing dependencies
    - Automatic service discovery and wiring
    - Capability-based access control
    """

    def __init__(self, registry: ServiceRegistry | None = None):
        self.registry = registry or get_registry()
        self.loaded_services: dict[str, ServiceInstance] = {}
        self.service_factories: dict[str, Callable] = {}

        # Register built-in service factories
        self._register_core_factories()

    def register_service_factory(self, service_name: str, factory: Callable):
        """Register a factory function for creating service instances"""
        self.service_factories[service_name] = factory
        logger.info(f"Registered service factory: {service_name}")

    def load_services_for_tenant(
        self, tenant_id: UUID, required_capabilities: list[str] | None = None
    ) -> dict[str, ServiceInstance]:
        """Load all services available to tenant based on subscriptions"""

        services: dict[str, ServiceInstance] = {}

        # Get tenant subscriptions
        subscriptions = self.registry.get_tenant_subscriptions(tenant_id)

        for subscription in subscriptions:
            try:
                service = self._load_service(subscription.service_name, subscription)

                if service:
                    # Check if service provides required capabilities
                    if required_capabilities:
                        provides_required = any(service.has_capability(cap) for cap in required_capabilities)
                        if not provides_required:
                            continue

                    services[service.name] = service
                    logger.info(f"Loaded service: {service.name} for tenant {tenant_id}")

            except Exception as e:
                logger.error(f"Failed to load service {subscription.service_name}: {e}")

                # Create placeholder service
                placeholder = PlaceholderService(subscription.service_name, f"Load failed: {str(e)}")
                services[subscription.service_name] = ServiceInstance(
                    name=subscription.service_name,
                    version="placeholder",
                    instance=placeholder,
                    manifest=self.registry.get_service_manifest(subscription.service_name),
                    subscription=subscription,
                )

        return services

    def load_service_with_dependencies(
        self, service_name: str, tenant_id: UUID, resolve_placeholders: bool = True
    ) -> ServiceInstance:
        """Load service and resolve its dependencies"""

        # Get subscription
        subscription = self.registry.get_tenant_subscription(tenant_id, service_name)
        if not subscription:
            raise ValueError(f"No subscription found for service '{service_name}' and tenant {tenant_id}")

        # Load primary service
        service = self._load_service(service_name, subscription)
        if not service:
            raise RuntimeError(f"Failed to load service '{service_name}'")

        # Resolve dependencies
        dependency_resolution = self.registry.resolve_dependencies(service_name, tenant_id)

        # Load available dependencies
        dependency_services: dict[str, ServiceInstance] = {}

        for dep_service_name in dependency_resolution["available"]:
            try:
                dep_subscription = self.registry.get_tenant_subscription(tenant_id, dep_service_name)
                if dep_subscription is None:
                    continue
                dep_service = self._load_service(dep_service_name, dep_subscription)

                if dep_service:
                    dependency_services[dep_service_name] = dep_service

            except Exception as e:
                logger.error(f"Failed to load dependency {dep_service_name}: {e}")

        # Create placeholders for missing dependencies if enabled
        if resolve_placeholders:
            for dep_service_name in dependency_resolution["unavailable"]:
                placeholder = PlaceholderService(dep_service_name, "Service not registered")
                dependency_services[dep_service_name] = ServiceInstance(
                    name=dep_service_name, version="placeholder", instance=placeholder, manifest=None
                )

            for dep_service_name in dependency_resolution["missing_subscription"]:
                placeholder = PlaceholderService(dep_service_name, "Missing subscription")
                dependency_services[dep_service_name] = ServiceInstance(
                    name=dep_service_name,
                    version="placeholder",
                    instance=placeholder,
                    manifest=self.registry.get_service_manifest(dep_service_name),
                )

        # Inject dependencies into service instance if supported
        if hasattr(service.instance, "inject_dependencies"):
            service.instance.inject_dependencies(dependency_services)

        return service

    def get_service_capabilities_for_tenant(self, tenant_id: UUID) -> dict[str, list[str]]:
        """Get all capabilities available to tenant across all subscriptions"""

        capabilities_by_service: dict[str, list[str]] = {}
        subscriptions = self.registry.get_tenant_subscriptions(tenant_id)

        for subscription in subscriptions:
            manifest = self.registry.get_service_manifest(subscription.service_name)
            if manifest:
                tier_caps = manifest.subscription_tiers.get(subscription.subscription_tier.value, [])
                capabilities_by_service[subscription.service_name] = tier_caps

        return capabilities_by_service

    def discover_services_for_capability(self, capability: str, tenant_id: UUID) -> list[ServiceInstance]:
        """Discover and load services that provide a specific capability"""

        services: list[ServiceInstance] = []
        manifests = self.registry.discover_services(capability, tenant_id)

        for manifest in manifests:
            subscription = self.registry.get_tenant_subscription(tenant_id, manifest.name)

            if subscription and subscription.has_capability(capability, manifest):
                try:
                    service = self._load_service(manifest.name, subscription)
                    if service:
                        services.append(service)
                except Exception as e:
                    logger.error(f"Failed to load service {manifest.name}: {e}")

        return services

    def _load_service(self, service_name: str, subscription: TenantSubscription) -> ServiceInstance | None:
        """Load individual service instance"""

        # Check if already loaded
        cache_key = f"{service_name}:{subscription.subscription_tier.value}"
        if cache_key in self.loaded_services:
            return self.loaded_services[cache_key]

        # Get manifest
        manifest = self.registry.get_service_manifest(service_name)
        if not manifest:
            logger.error(f"No manifest found for service: {service_name}")
            return None

        # Get factory
        factory = self.service_factories.get(service_name)
        if not factory:
            logger.warning(f"No factory found for service: {service_name}")

            # Create generic service instance with manifest data
            instance = GenericServiceInstance(manifest, subscription)
        else:
            # Use factory to create instance
            instance = factory(manifest, subscription)

        # Create service instance wrapper
        service = ServiceInstance(
            name=service_name, version=manifest.version, instance=instance, manifest=manifest, subscription=subscription
        )

        # Cache loaded service
        self.loaded_services[cache_key] = service

        return service

    def _register_core_factories(self):
        """Register factories for core Hyeve services"""

        # Auth service factory
        def create_auth_service(manifest: ServiceManifest | None, subscription: TenantSubscription):
            if manifest is None:
                raise ValueError("Auth service manifest missing")
            from ..auth import AuthServiceClient  # type: ignore[import-untyped]  # Import when needed

            return AuthServiceClient(
                base_url=manifest.deployment.get("base_url", "http://auth-service"),
                subscription_tier=subscription.subscription_tier.value,
            )

        # DAL service factory
        def create_dal_service(manifest: ServiceManifest | None, subscription: TenantSubscription):
            if manifest is None:
                raise ValueError("DAL service manifest missing")
            from ..dal import DALServiceClient  # type: ignore[attr-defined]  # Import when needed

            return DALServiceClient(
                connection_string=manifest.deployment.get("connection_string"), tenant_id=str(subscription.tenant_id)
            )

        # Audit service factory
        def create_audit_service(manifest: ServiceManifest | None, subscription: TenantSubscription):
            if manifest is None:
                raise ValueError("Audit service manifest missing")
            from ..audit import AuditServiceClient  # type: ignore[import-untyped]  # Import when needed

            return AuditServiceClient(
                base_url=manifest.deployment.get("base_url", "http://audit-service"),
                tenant_id=str(subscription.tenant_id),
            )

        # Event service factory
        def create_event_service(manifest: ServiceManifest | None, subscription: TenantSubscription):
            if manifest is None:
                raise ValueError("Event service manifest missing")
            from ..events import EventServiceClient  # type: ignore[import-untyped]  # Import when needed

            return EventServiceClient(
                broker_url=manifest.deployment.get("broker_url", "redis://events-service"),
                tenant_id=str(subscription.tenant_id),
            )

        # Cache service factory
        def create_cache_service(manifest: ServiceManifest | None, subscription: TenantSubscription):
            if manifest is None:
                raise ValueError("Cache service manifest missing")
            from ..cache import CacheServiceClient  # type: ignore[import-untyped]  # Import when needed

            return CacheServiceClient(
                redis_url=manifest.deployment.get("redis_url", "redis://cache-service"),
                tenant_id=str(subscription.tenant_id),
            )

        # Register factories
        self.service_factories.update(
            {
                "auth-service": create_auth_service,
                "dal-service": create_dal_service,
                "audit-service": create_audit_service,
                "event-service": create_event_service,
                "cache-service": create_cache_service,
            }
        )


class GenericServiceInstance:
    """Generic service instance created from manifest when no factory available"""

    def __init__(self, manifest: ServiceManifest | None, subscription: TenantSubscription):
        if manifest is None:
            raise ValueError("Manifest required to build generic service instance")
        self.manifest = manifest
        self.subscription = subscription
        self._endpoints: dict[str, Any] = {}

        # Create endpoint methods from manifest
        for endpoint_name, endpoint_config in manifest.endpoints.items():
            self._create_endpoint_method(endpoint_name, endpoint_config)

    def _create_endpoint_method(self, name: str, config: dict[str, Any]):
        """Create dynamic method for endpoint"""

        def endpoint_method(*args, **kwargs):
            # Check capability access
            tier_caps = self.manifest.subscription_tiers.get(self.subscription.subscription_tier.value, [])
            if name not in tier_caps:
                return {
                    "error": "Access denied",
                    "reason": f"Capability '{name}' not available in {self.subscription.subscription_tier.value} tier",
                }

            # Return endpoint configuration for now
            # In real implementation, this would make HTTP calls
            return {
                "endpoint": name,
                "config": config,
                "args": args,
                "kwargs": kwargs,
                "subscription_tier": self.subscription.subscription_tier.value,
            }

        setattr(self, name, endpoint_method)


# Default loader instance
_default_loader: ServiceLoader | None = None


def get_loader() -> ServiceLoader:
    """Get default service loader instance"""
    global _default_loader

    if _default_loader is None:
        _default_loader = ServiceLoader()

    return _default_loader


if __name__ == "__main__":
    print("Hyeve Service Loader")
    print("=" * 30)

    from uuid import uuid4

    # Create test environment
    registry = ServiceRegistry()
    loader = ServiceLoader(registry)

    # Create sample tenant and subscription
    tenant_id = uuid4()

    # Register sample services (these would normally be loaded from manifests)
    from .registry import ServiceManifest

    auth_manifest = ServiceManifest(
        name="auth-service",
        version="1.0.0",
        provider="hyeve-core",
        description="Authentication service",
        subscription_tiers={
            "basic": ["authenticate"],
            "premium": ["authenticate", "sso"],
            "enterprise": ["authenticate", "sso", "custom_claims"],
        },
        endpoints={"authenticate": {"method": "POST", "path": "/auth/authenticate"}},
        dependencies={"required": ["dal-service"]},
        integration={},
        deployment={"base_url": "http://auth-service"},
    )

    registry.register_service(auth_manifest)

    # Create subscription
    subscription = registry.create_subscription(tenant_id, "auth-service", SubscriptionTier.PREMIUM)

    # Test loading
    services = loader.load_services_for_tenant(tenant_id)
    print(f"✅ Loaded {len(services)} services for tenant")

    # Test capability discovery
    capabilities = loader.get_service_capabilities_for_tenant(tenant_id)
    print(f"✅ Available capabilities: {capabilities}")

    # Test service with dependencies
    try:
        auth_service = loader.load_service_with_dependencies("auth-service", tenant_id)
        print("✅ Loaded auth service with dependencies")

        # Test capability
        result = auth_service.instance.authenticate("test_user", "password")
        print(f"✅ Service call result: {result}")

    except Exception as e:
        print(f"⚠️ Service loading with placeholders: {e}")

    print("\nService Loader Ready!")
    print("Ready for dynamic service integration!")
