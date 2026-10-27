"""
Service Registry Package
========================

Hyeve Service Registry - Metadata-driven service discovery and subscription management.

Enables the BIVR ecosystem to generate infrastructure-aware code that automatically
discovers and consumes services based on tenant subscriptions and capabilities.
"""

from typing import Optional

from .loader import GenericServiceInstance, PlaceholderService, ServiceInstance, ServiceLoader, get_loader
from .manifest import (
    AUDIT_SERVICE_MANIFEST,
    AUTH_SERVICE_MANIFEST,
    CACHE_SERVICE_MANIFEST,
    CORE_SERVICE_MANIFESTS,
    DAL_SERVICE_MANIFEST,
    EVENT_SERVICE_MANIFEST,
    NOTIFICATION_SERVICE_MANIFEST,
    REGISTRATION_SERVICE_MANIFEST,
    bootstrap_registry,
)
from .registry import (
    ServiceManifest,
    ServiceRegistry,
    ServiceStatus,
    SubscriptionTier,
    TenantSubscription,
    get_registry,
)

__version__ = "1.0.0"
__author__ = "Hyeve Team"

# Public API exports
__all__ = [
    # Core classes
    "ServiceRegistry",
    "ServiceManifest",
    "TenantSubscription",
    "ServiceLoader",
    "ServiceInstance",

    # Enums
    "SubscriptionTier",
    "ServiceStatus",

    # Utility classes
    "PlaceholderService",
    "GenericServiceInstance",

    # Manifest templates
    "AUTH_SERVICE_MANIFEST",
    "DAL_SERVICE_MANIFEST",
    "AUDIT_SERVICE_MANIFEST",
    "EVENT_SERVICE_MANIFEST",
    "CACHE_SERVICE_MANIFEST",
    "NOTIFICATION_SERVICE_MANIFEST",
    "REGISTRATION_SERVICE_MANIFEST",
    "CORE_SERVICE_MANIFESTS",

    # Factory functions
    "get_registry",
    "get_loader",
    "bootstrap_registry"
]


def initialize_service_registry() -> ServiceRegistry:
    """
    Initialize and bootstrap the service registry with core services.
    
    This is the main entry point for setting up the service registry
    in a Hyeve deployment.
    
    Returns:
        ServiceRegistry: Initialized registry with core services loaded
    """

    print("Initializing Hyeve Service Registry")
    print("=" * 40)

    # Create registry instance
    registry = get_registry()

    # Bootstrap with core services
    bootstrap_registry(registry)

    print("✅ Service Registry Initialized")
    print(f"Services available: {len(registry.services)}")

    return registry


def create_development_tenant(registry: ServiceRegistry | None = None) -> dict:
    """
    Create a development tenant with full enterprise subscriptions.
    
    Useful for development and testing environments.
    
    Args:
        registry: Service registry instance (uses default if None)
        
    Returns:
        dict: Tenant information and subscriptions
    """

    if registry is None:
        registry = get_registry()

    from uuid import uuid4

    # Create development tenant
    tenant_id = uuid4()
    tenant_name = f"dev-tenant-{tenant_id.hex[:8]}"

    print(f"Creating Development Tenant: {tenant_name}")
    print(f"🆔 Tenant ID: {tenant_id}")

    # Create enterprise subscriptions for all services
    subscriptions = []

    for service_name in registry.services.keys():
        subscription = registry.create_subscription(
            tenant_id=tenant_id,
            service_name=service_name,
            tier=SubscriptionTier.ENTERPRISE,
            duration_days=365  # 1 year for dev
        )
        subscriptions.append(subscription)
        print(f"✅ Enterprise subscription: {service_name}")

    # Get service loader and load services
    loader = get_loader()
    loaded_services = loader.load_services_for_tenant(tenant_id)

    print("\nDevelopment Tenant Ready!")
    print(f"Active subscriptions: {len(subscriptions)}")
    print(f"Loaded services: {len(loaded_services)}")

    return {
        "tenant_id": tenant_id,
        "tenant_name": tenant_name,
        "subscriptions": subscriptions,
        "loaded_services": loaded_services,
        "capabilities": loader.get_service_capabilities_for_tenant(tenant_id)
    }


if __name__ == "__main__":
    # Demo the service registry system
    print("Hyeve Service Registry Demo")
    print("=" * 35)

    # Initialize registry
    registry = initialize_service_registry()

    # Create development tenant
    dev_tenant = create_development_tenant(registry)

    # Show capabilities
    print(f"\nAvailable Capabilities for {dev_tenant['tenant_name']}:")
    for service_name, capabilities in dev_tenant['capabilities'].items():
        print(f"  {service_name}: {', '.join(capabilities)}")

    # Test service discovery
    loader = get_loader()
    auth_services = loader.discover_services_for_capability("authenticate", dev_tenant['tenant_id'])

    print(f"\nServices providing 'authenticate' capability: {len(auth_services)}")
    for service in auth_services:
        print(f"  ✅ {service.name} v{service.version}")

    print("\nService Registry System Ready!")
    print("Ready for BIVR integration and code generation!")
