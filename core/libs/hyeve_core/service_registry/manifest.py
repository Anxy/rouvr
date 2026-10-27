"""
Service Manifest Templates
==========================

Pre-defined service manifests for core Hyeve services.
Used to bootstrap the service registry with known services.
"""

from datetime import datetime, UTC

from .registry import ServiceManifest, ServiceStatus

# Auth Service Manifest
AUTH_SERVICE_MANIFEST = ServiceManifest(
    name="auth-service",
    version="1.0.0",
    provider="hyeve-core",
    description="Authentication and authorization service with multi-tenant support",
    subscription_tiers={
        "basic": [
            "authenticate",
            "validate_token",
            "logout"
        ],
        "premium": [
            "authenticate",
            "validate_token",
            "logout",
            "sso",
            "mfa",
            "password_reset"
        ],
        "enterprise": [
            "authenticate",
            "validate_token",
            "logout",
            "sso",
            "mfa",
            "password_reset",
            "tenant_isolation",
            "custom_claims",
            "audit_integration",
            "session_management"
        ]
    },
    endpoints={
        "authenticate": {
            "method": "POST",
            "path": "/auth/authenticate",
            "input_schema": "schemas/authenticate_request.json",
            "output_schema": "schemas/authenticate_response.json",
            "description": "Authenticate user credentials"
        },
        "validate_token": {
            "method": "POST",
            "path": "/auth/validate",
            "input_schema": "schemas/validate_token_request.json",
            "output_schema": "schemas/validate_token_response.json",
            "description": "Validate JWT token"
        },
        "sso": {
            "method": "POST",
            "path": "/auth/sso",
            "input_schema": "schemas/sso_request.json",
            "description": "Single sign-on authentication"
        }
    },
    dependencies={
        "required": ["dal-service", "audit-service"],
        "optional": ["notification-service", "cache-service"]
    },
    integration={
        "dal_tables": ["users", "auth_providers", "sessions", "user_roles"],
        "events_published": [
            "user.authenticated",
            "auth.failed",
            "user.logged_out",
            "session.expired"
        ],
        "events_consumed": ["user.created", "user.updated"],
        "cache_keys": ["session:{session_id}", "user_perms:{user_id}"]
    },
    deployment={
        "docker_image": "hyeve/auth-service:1.0.0",
        "base_url": "http://auth-service",
        "health_check": "/health",
        "config_keys": ["JWT_SECRET", "SESSION_TIMEOUT"]
    },
    status=ServiceStatus.ACTIVE,
    created_at=datetime.now(UTC)
)

# DAL Service Manifest
DAL_SERVICE_MANIFEST = ServiceManifest(
    name="dal-service",
    version="1.0.0",
    provider="hyeve-core",
    description="Data Access Layer with tenant isolation and query optimization",
    subscription_tiers={
        "basic": [
            "query",
            "insert",
            "update",
            "delete"
        ],
        "premium": [
            "query",
            "insert",
            "update",
            "delete",
            "batch_operations",
            "transactions",
            "query_optimization"
        ],
        "enterprise": [
            "query",
            "insert",
            "update",
            "delete",
            "batch_operations",
            "transactions",
            "query_optimization",
            "tenant_isolation",
            "data_encryption",
            "audit_logging",
            "performance_metrics"
        ]
    },
    endpoints={
        "query": {
            "method": "POST",
            "path": "/dal/query",
            "input_schema": "schemas/query_request.json",
            "description": "Execute database query"
        },
        "insert": {
            "method": "POST",
            "path": "/dal/insert",
            "input_schema": "schemas/insert_request.json",
            "description": "Insert records"
        },
        "batch_operations": {
            "method": "POST",
            "path": "/dal/batch",
            "input_schema": "schemas/batch_request.json",
            "description": "Execute batch database operations"
        }
    },
    dependencies={
        "required": ["audit-service"],
        "optional": ["cache-service", "event-service"]
    },
    integration={
        "database": {
            "type": "postgresql",
            "connection_pool": True,
            "tenant_strategy": "schema_per_tenant"
        },
        "events_published": [
            "dal.query_executed",
            "dal.data_modified",
            "dal.transaction_completed"
        ],
        "cache_integration": True
    },
    deployment={
        "docker_image": "hyeve/dal-service:1.0.0",
        "base_url": "http://dal-service",
        "health_check": "/health",
        "config_keys": ["DATABASE_URL", "POOL_SIZE", "QUERY_TIMEOUT"]
    },
    status=ServiceStatus.ACTIVE,
    created_at=datetime.now(UTC)
)

# Audit Service Manifest
AUDIT_SERVICE_MANIFEST = ServiceManifest(
    name="audit-service",
    version="1.0.0",
    provider="hyeve-core",
    description="Comprehensive audit logging and compliance tracking",
    subscription_tiers={
        "basic": [
            "log_event",
            "query_logs"
        ],
        "premium": [
            "log_event",
            "query_logs",
            "structured_logging",
            "log_retention",
            "compliance_reports"
        ],
        "enterprise": [
            "log_event",
            "query_logs",
            "structured_logging",
            "log_retention",
            "compliance_reports",
            "real_time_monitoring",
            "custom_audit_rules",
            "data_lineage",
            "regulatory_compliance"
        ]
    },
    endpoints={
        "log_event": {
            "method": "POST",
            "path": "/audit/log",
            "input_schema": "schemas/audit_log_request.json",
            "description": "Log audit event"
        },
        "query_logs": {
            "method": "GET",
            "path": "/audit/logs",
            "input_schema": "schemas/audit_query_request.json",
            "description": "Query audit logs"
        },
        "compliance_reports": {
            "method": "GET",
            "path": "/audit/compliance",
            "description": "Generate compliance reports"
        }
    },
    dependencies={
        "required": ["dal-service"],
        "optional": ["notification-service", "event-service"]
    },
    integration={
        "dal_tables": ["audit_logs", "audit_rules", "compliance_templates"],
        "events_published": [
            "audit.logged",
            "audit.compliance_violation",
            "audit.retention_policy_triggered"
        ],
        "events_consumed": ["*"]  # Consumes all events for auditing
    },
    deployment={
        "docker_image": "hyeve/audit-service:1.0.0",
        "base_url": "http://audit-service",
        "health_check": "/health",
        "config_keys": ["LOG_RETENTION_DAYS", "COMPLIANCE_RULES"]
    },
    status=ServiceStatus.ACTIVE,
    created_at=datetime.now(UTC)
)

# Event Service Manifest
EVENT_SERVICE_MANIFEST = ServiceManifest(
    name="event-service",
    version="1.0.0",
    provider="hyeve-core",
    description="Event-driven messaging and workflow orchestration",
    subscription_tiers={
        "basic": [
            "publish_event",
            "subscribe_event"
        ],
        "premium": [
            "publish_event",
            "subscribe_event",
            "event_routing",
            "dead_letter_queue",
            "event_replay"
        ],
        "enterprise": [
            "publish_event",
            "subscribe_event",
            "event_routing",
            "dead_letter_queue",
            "event_replay",
            "tenant_isolation",
            "event_transformation",
            "workflow_orchestration",
            "event_sourcing"
        ]
    },
    endpoints={
        "publish_event": {
            "method": "POST",
            "path": "/events/publish",
            "input_schema": "schemas/publish_event_request.json",
            "description": "Publish event to message broker"
        },
        "subscribe_event": {
            "method": "POST",
            "path": "/events/subscribe",
            "input_schema": "schemas/subscribe_event_request.json",
            "description": "Subscribe to event stream"
        },
        "event_replay": {
            "method": "POST",
            "path": "/events/replay",
            "description": "Replay events from specific point in time"
        }
    },
    dependencies={
        "required": ["audit-service"],
        "optional": ["dal-service", "notification-service"]
    },
    integration={
        "message_broker": {
            "type": "redis",
            "clustering": True,
            "persistence": True
        },
        "dal_tables": ["event_log", "subscriptions", "dead_letters"],
        "events_published": [
            "event.published",
            "event.delivered",
            "event.failed"
        ]
    },
    deployment={
        "docker_image": "hyeve/event-service:1.0.0",
        "base_url": "http://event-service",
        "broker_url": "redis://event-broker",
        "health_check": "/health",
        "config_keys": ["REDIS_URL", "MAX_RETRIES", "DLQ_TTL"]
    },
    status=ServiceStatus.ACTIVE,
    created_at=datetime.now(UTC)
)

# Cache Service Manifest
CACHE_SERVICE_MANIFEST = ServiceManifest(
    name="cache-service",
    version="1.0.0",
    provider="hyeve-core",
    description="Distributed caching with tenant isolation and TTL management",
    subscription_tiers={
        "basic": [
            "get",
            "set",
            "delete"
        ],
        "premium": [
            "get",
            "set",
            "delete",
            "batch_operations",
            "ttl_management",
            "cache_patterns"
        ],
        "enterprise": [
            "get",
            "set",
            "delete",
            "batch_operations",
            "ttl_management",
            "cache_patterns",
            "tenant_isolation",
            "distributed_locking",
            "cache_analytics",
            "eviction_policies"
        ]
    },
    endpoints={
        "get": {
            "method": "GET",
            "path": "/cache/{key}",
            "description": "Get cached value"
        },
        "set": {
            "method": "POST",
            "path": "/cache/set",
            "input_schema": "schemas/cache_set_request.json",
            "description": "Set cached value with TTL"
        },
        "batch_operations": {
            "method": "POST",
            "path": "/cache/batch",
            "input_schema": "schemas/cache_batch_request.json",
            "description": "Execute batch cache operations"
        }
    },
    dependencies={
        "optional": ["audit-service", "event-service"]
    },
    integration={
        "cache_backend": {
            "type": "redis",
            "clustering": True,
            "persistence": False
        },
        "events_published": [
            "cache.hit",
            "cache.miss",
            "cache.evicted"
        ],
        "tenant_strategy": "key_prefix"
    },
    deployment={
        "docker_image": "hyeve/cache-service:1.0.0",
        "base_url": "http://cache-service",
        "redis_url": "redis://cache-backend",
        "health_check": "/health",
        "config_keys": ["REDIS_URL", "DEFAULT_TTL", "MAX_MEMORY"]
    },
    status=ServiceStatus.ACTIVE,
    created_at=datetime.now(UTC)
)

# Notification Service Manifest
NOTIFICATION_SERVICE_MANIFEST = ServiceManifest(
    name="notification-service",
    version="1.0.0",
    provider="hyeve-core",
    description="Multi-channel notification delivery with templates",
    subscription_tiers={
        "basic": [
            "send_email",
            "send_sms"
        ],
        "premium": [
            "send_email",
            "send_sms",
            "push_notifications",
            "notification_templates",
            "delivery_tracking"
        ],
        "enterprise": [
            "send_email",
            "send_sms",
            "push_notifications",
            "notification_templates",
            "delivery_tracking",
            "tenant_branding",
            "notification_preferences",
            "bulk_notifications",
            "delivery_analytics"
        ]
    },
    endpoints={
        "send_email": {
            "method": "POST",
            "path": "/notifications/email",
            "input_schema": "schemas/send_email_request.json",
            "description": "Send email notification"
        },
        "send_sms": {
            "method": "POST",
            "path": "/notifications/sms",
            "input_schema": "schemas/send_sms_request.json",
            "description": "Send SMS notification"
        },
        "bulk_notifications": {
            "method": "POST",
            "path": "/notifications/bulk",
            "description": "Send bulk notifications"
        }
    },
    dependencies={
        "required": ["audit-service"],
        "optional": ["dal-service", "event-service", "cache-service"]
    },
    integration={
        "dal_tables": ["notification_templates", "delivery_log", "user_preferences"],
        "events_published": [
            "notification.sent",
            "notification.delivered",
            "notification.failed"
        ],
        "events_consumed": [
            "user.created",
            "auth.failed",
            "system.alert"
        ],
        "providers": ["smtp", "twilio", "firebase"]
    },
    deployment={
        "docker_image": "hyeve/notification-service:1.0.0",
        "base_url": "http://notification-service",
        "health_check": "/health",
        "config_keys": ["SMTP_CONFIG", "TWILIO_CONFIG", "FIREBASE_CONFIG"]
    },
    status=ServiceStatus.ACTIVE,
    created_at=datetime.now(UTC)
)

# Registration Service Manifest (Business domain)
REGISTRATION_SERVICE_MANIFEST = ServiceManifest(
    name="registration-service",
    version="1.0.0",
    provider="hyeve-business",
    description="User and tenant registration with onboarding workflows",
    subscription_tiers={
        "basic": [
            "register_user",
            "verify_email"
        ],
        "premium": [
            "register_user",
            "verify_email",
            "tenant_onboarding",
            "bulk_registration",
            "registration_analytics"
        ],
        "enterprise": [
            "register_user",
            "verify_email",
            "tenant_onboarding",
            "bulk_registration",
            "registration_analytics",
            "custom_workflows",
            "integration_apis",
            "compliance_validation",
            "advanced_verification"
        ]
    },
    endpoints={
        "register_user": {
            "method": "POST",
            "path": "/registration/user",
            "input_schema": "schemas/register_user_request.json",
            "description": "Register new user account"
        },
        "tenant_onboarding": {
            "method": "POST",
            "path": "/registration/tenant",
            "input_schema": "schemas/tenant_onboarding_request.json",
            "description": "Complete tenant onboarding process"
        }
    },
    dependencies={
        "required": ["auth-service", "dal-service", "audit-service", "notification-service"],
        "optional": ["event-service", "cache-service"]
    },
    integration={
        "dal_tables": ["registrations", "verification_codes", "onboarding_progress"],
        "events_published": [
            "user.registered",
            "tenant.onboarded",
            "registration.completed"
        ],
        "events_consumed": [
            "user.verified",
            "payment.confirmed"
        ],
        "workflows": ["user_onboarding", "tenant_setup", "compliance_check"]
    },
    deployment={
        "docker_image": "hyeve/registration-service:1.0.0",
        "base_url": "http://registration-service",
        "health_check": "/health",
        "config_keys": ["VERIFICATION_TTL", "ONBOARDING_STEPS"]
    },
    status=ServiceStatus.ACTIVE,
    created_at=datetime.now(UTC)
)

# All core service manifests
CORE_SERVICE_MANIFESTS = [
    AUTH_SERVICE_MANIFEST,
    DAL_SERVICE_MANIFEST,
    AUDIT_SERVICE_MANIFEST,
    EVENT_SERVICE_MANIFEST,
    CACHE_SERVICE_MANIFEST,
    NOTIFICATION_SERVICE_MANIFEST,
    REGISTRATION_SERVICE_MANIFEST
]


def bootstrap_registry(registry):
    """Bootstrap registry with core service manifests"""

    print("Bootstrapping Service Registry with Core Services")
    print("=" * 50)

    for manifest in CORE_SERVICE_MANIFESTS:
        success = registry.register_service(manifest)

        if success:
            print(f"✅ Registered: {manifest.name} v{manifest.version}")

            # Show subscription tiers
            for tier, capabilities in manifest.subscription_tiers.items():
                print(f"   {tier}: {len(capabilities)} capabilities")
        else:
            print(f"❌ Failed to register: {manifest.name}")

    print("\nBootstrap Complete!")
    print(f"Total services: {len(registry.services)}")

    return registry


if __name__ == "__main__":
    from .registry import ServiceRegistry

    # Create registry and bootstrap
    registry = ServiceRegistry()
    bootstrap_registry(registry)

    # Show summary
    print("\nRegistry Summary:")
    for service_name, manifest in registry.services.items():
        total_caps = sum(len(caps) for caps in manifest.subscription_tiers.values())
        print(f"  {service_name}: {total_caps} total capabilities")
