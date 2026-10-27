"""
Hyeve Service Registry
======================

Core service discovery and subscription management system.
Enables metadata-driven, subscription-based service ecosystem.
"""

import json
import logging
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import yaml  # type: ignore[import-untyped]

logger = logging.getLogger(__name__)


class SubscriptionTier(Enum):
    """Service subscription tiers"""

    BASIC = "basic"
    PREMIUM = "premium"
    ENTERPRISE = "enterprise"


class ServiceStatus(Enum):
    """Service status"""

    ACTIVE = "active"
    INACTIVE = "inactive"
    MAINTENANCE = "maintenance"
    DEPRECATED = "deprecated"


@dataclass
class ServiceManifest:
    """Service manifest describing capabilities and requirements"""

    name: str
    version: str
    provider: str
    description: str

    # Subscription tiers and capabilities
    subscription_tiers: dict[str, list[str]]

    # API endpoints
    endpoints: dict[str, dict[str, Any]]

    # Dependencies
    dependencies: dict[str, list[str]]

    # Integration metadata
    integration: dict[str, Any]

    # Deployment info
    deployment: dict[str, Any]

    status: ServiceStatus = ServiceStatus.ACTIVE
    created_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary"""
        result = asdict(self)
        result["status"] = self.status.value
        if self.created_at:
            result["created_at"] = self.created_at.isoformat()
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ServiceManifest":
        """Create from dictionary"""
        # Handle enum conversion
        if "status" in data:
            data["status"] = ServiceStatus(data["status"])
        if "created_at" in data and data["created_at"]:
            data["created_at"] = datetime.fromisoformat(data["created_at"])
        return cls(**data)

    @classmethod
    def from_yaml_file(cls, file_path: str | Path) -> "ServiceManifest":
        """Load manifest from YAML file"""
        with open(file_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)

        # Flatten nested service structure if present
        if "service" in data:
            service_data = data["service"]
            # Merge other top-level keys
            for key, value in data.items():
                if key != "service":
                    service_data[key] = value
            data = service_data

        return cls.from_dict(data)


@dataclass
class TenantSubscription:
    """Tenant subscription to a service"""

    id: UUID
    tenant_id: UUID
    service_name: str
    subscription_tier: SubscriptionTier
    expires_at: datetime | None = None
    created_at: datetime | None = None

    def is_active(self) -> bool:
        """Check if subscription is currently active"""
        if self.expires_at and datetime.now(UTC) > self.expires_at:
            return False
        return True

    def has_capability(self, capability: str, manifest: ServiceManifest) -> bool:
        """Check if subscription tier includes capability"""
        tier_capabilities = manifest.subscription_tiers.get(self.subscription_tier.value, [])
        return capability in tier_capabilities


class ServiceRegistry:
    """
    Core service registry for discovery and subscription management

    Features:
    - Service manifest management
    - Subscription-based access control
    - Dynamic service discovery
    - Dependency resolution
    - Health monitoring
    """

    def __init__(self, registry_path: str = "services"):
        self.registry_path = Path(registry_path)
        self.services: dict[str, ServiceManifest] = {}
        self.subscriptions: dict[UUID, list[TenantSubscription]] = {}

        # Ensure registry directory exists
        self.registry_path.mkdir(parents=True, exist_ok=True)

        # Load existing services
        self._load_services()

    def _load_services(self):
        """Load service manifests from registry directory"""

        manifest_dir = self.registry_path / "manifests"
        if not manifest_dir.exists():
            manifest_dir.mkdir(parents=True, exist_ok=True)
            return

        for manifest_file in manifest_dir.glob("*.yml"):
            try:
                manifest = ServiceManifest.from_yaml_file(manifest_file)
                self.services[manifest.name] = manifest
                logger.info(f"Loaded service manifest: {manifest.name}")
            except Exception as e:
                logger.error(f"Failed to load manifest {manifest_file}: {e}")

    def register_service(self, manifest: ServiceManifest) -> bool:
        """Register a new service"""

        try:
            # Validate manifest
            if not self._validate_manifest(manifest):
                return False

            # Store in memory
            self.services[manifest.name] = manifest

            # Save to disk
            manifest_file = self.registry_path / "manifests" / f"{manifest.name}.yml"
            with open(manifest_file, "w", encoding="utf-8") as f:
                yaml.dump(manifest.to_dict(), f, default_flow_style=False)

            logger.info(f"Registered service: {manifest.name}")
            return True

        except Exception as e:
            logger.error(f"Failed to register service {manifest.name}: {e}")
            return False

    def get_service_manifest(self, service_name: str) -> ServiceManifest | None:
        """Get service manifest by name"""
        return self.services.get(service_name)

    def discover_services(self, capability: str | None = None, tenant_id: UUID | None = None) -> list[ServiceManifest]:
        """Discover available services based on capability and tenant subscriptions"""

        results: list[ServiceManifest] = []

        for service_name, manifest in self.services.items():
            # Check if service is active
            if manifest.status != ServiceStatus.ACTIVE:
                continue

            # If capability specified, check if service provides it
            if capability:
                provides_capability = False
                for tier_caps in manifest.subscription_tiers.values():
                    if capability in tier_caps:
                        provides_capability = True
                        break

                if not provides_capability:
                    continue

            # If tenant specified, check subscription
            if tenant_id:
                subscription = self.get_tenant_subscription(tenant_id, service_name)
                if not subscription or not subscription.is_active():
                    continue

            results.append(manifest)

        return results

    def create_subscription(
        self, tenant_id: UUID, service_name: str, tier: SubscriptionTier, duration_days: int | None = None
    ) -> TenantSubscription:
        """Create tenant subscription to service"""

        expires_at = None
        if duration_days:
            expires_at = datetime.now(UTC) + timedelta(days=duration_days)

        subscription = TenantSubscription(
            id=uuid4(),
            tenant_id=tenant_id,
            service_name=service_name,
            subscription_tier=tier,
            expires_at=expires_at,
            created_at=datetime.now(UTC),
        )

        # Store subscription
        if tenant_id not in self.subscriptions:
            self.subscriptions[tenant_id] = []

        self.subscriptions[tenant_id].append(subscription)

        # Persist to disk
        self._save_subscriptions()

        logger.info(f"Created subscription: {tenant_id} -> {service_name} ({tier.value})")
        return subscription

    def get_tenant_subscription(self, tenant_id: UUID, service_name: str) -> TenantSubscription | None:
        """Get tenant's subscription to specific service"""

        tenant_subscriptions = self.subscriptions.get(tenant_id, [])

        for subscription in tenant_subscriptions:
            if subscription.service_name == service_name and subscription.is_active():
                return subscription

        return None

    def get_tenant_subscriptions(self, tenant_id: UUID) -> list[TenantSubscription]:
        """Get all active subscriptions for tenant"""

        tenant_subscriptions = self.subscriptions.get(tenant_id, [])
        return [sub for sub in tenant_subscriptions if sub.is_active()]

    def resolve_dependencies(self, service_name: str, tenant_id: UUID) -> dict[str, list[str]]:
        """Resolve service dependencies for tenant"""

        manifest = self.get_service_manifest(service_name)
        if not manifest:
            return {"missing_service": [service_name]}

        results: dict[str, list[str]] = {"available": [], "unavailable": [], "missing_subscription": []}

        # Check required dependencies
        required_deps = manifest.dependencies.get("required", [])

        for dep_service in required_deps:
            dep_manifest = self.get_service_manifest(dep_service)

            if not dep_manifest:
                results["unavailable"].append(dep_service)
                continue

            subscription = self.get_tenant_subscription(tenant_id, dep_service)

            if not subscription:
                results["missing_subscription"].append(dep_service)
                continue

            results["available"].append(dep_service)

        return results

    def _validate_manifest(self, manifest: ServiceManifest) -> bool:
        """Validate service manifest"""

        # Basic validation
        if not manifest.name or not manifest.version:
            return False

        # Check subscription tiers
        if not manifest.subscription_tiers:
            return False

        # Validate tier structure
        for _tier, capabilities in manifest.subscription_tiers.items():
            if not isinstance(capabilities, list):
                return False

        return True

    def _save_subscriptions(self):
        """Save subscriptions to disk"""

        subscriptions_file = self.registry_path / "subscriptions.json"

        # Convert to serializable format
        data = {}
        for tenant_id, subs in self.subscriptions.items():
            data[str(tenant_id)] = [
                {
                    "id": str(sub.id),
                    "tenant_id": str(sub.tenant_id),
                    "service_name": sub.service_name,
                    "subscription_tier": sub.subscription_tier.value,
                    "expires_at": sub.expires_at.isoformat() if sub.expires_at else None,
                    "created_at": sub.created_at.isoformat() if sub.created_at else None,
                }
                for sub in subs
            ]

        with open(subscriptions_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def _load_subscriptions(self):
        """Load subscriptions from disk"""

        subscriptions_file = self.registry_path / "subscriptions.json"

        if not subscriptions_file.exists():
            return

        try:
            with open(subscriptions_file, encoding="utf-8") as f:
                data = json.load(f)

            # Convert back to objects
            for tenant_id_str, subs_data in data.items():
                tenant_id = UUID(tenant_id_str)
                subscriptions = []

                for sub_data in subs_data:
                    sub = TenantSubscription(
                        id=UUID(sub_data["id"]),
                        tenant_id=UUID(sub_data["tenant_id"]),
                        service_name=sub_data["service_name"],
                        subscription_tier=SubscriptionTier(sub_data["subscription_tier"]),
                        expires_at=datetime.fromisoformat(sub_data["expires_at"]) if sub_data["expires_at"] else None,
                        created_at=datetime.fromisoformat(sub_data["created_at"]) if sub_data["created_at"] else None,
                    )
                    subscriptions.append(sub)

                self.subscriptions[tenant_id] = subscriptions

        except Exception as e:
            logger.error(f"Failed to load subscriptions: {e}")


# Default registry instance
_default_registry: ServiceRegistry | None = None


def get_registry() -> ServiceRegistry:
    """Get default service registry instance"""
    global _default_registry

    if _default_registry is None:
        _default_registry = ServiceRegistry()

    return _default_registry


if __name__ == "__main__":
    print("Hyeve Service Registry")
    print("=" * 30)

    # Create sample registry
    registry = ServiceRegistry()

    # Example service manifest
    auth_manifest = ServiceManifest(
        name="auth-service",
        version="1.0.0",
        provider="hyeve-core",
        description="Authentication and authorization service",
        subscription_tiers={
            "basic": ["authenticate", "validate_token"],
            "premium": ["authenticate", "validate_token", "sso", "mfa"],
            "enterprise": ["authenticate", "validate_token", "sso", "mfa", "tenant_isolation", "custom_claims"],
        },
        endpoints={
            "authenticate": {
                "method": "POST",
                "path": "/auth/authenticate",
                "input_schema": "schemas/authenticate.json",
            }
        },
        dependencies={"required": ["dal-service", "audit-service"], "optional": ["notification-service"]},
        integration={
            "dal_tables": ["users", "auth_providers", "sessions"],
            "events_published": ["user.authenticated", "auth.failed"],
        },
        deployment={"docker_image": "hyeve/auth-service:1.0.0", "health_check": "/health"},
    )

    # Register service
    registry.register_service(auth_manifest)

    # Create sample subscription
    tenant_id = uuid4()
    subscription = registry.create_subscription(tenant_id, "auth-service", SubscriptionTier.PREMIUM)

    # Test discovery
    services = registry.discover_services("authenticate", tenant_id)

    print(f"✅ Registered services: {len(registry.services)}")
    print(f"✅ Tenant subscriptions: {len(registry.get_tenant_subscriptions(tenant_id))}")
    print(f"✅ Discovered services with 'authenticate': {len(services)}")

    print("\nService Registry Foundation Ready!")
    print(f"Registry path: {registry.registry_path}")
    print("Ready for BIVR service-aware generation!")
