CREATE TABLE IF NOT EXISTS aix_model_gateway_policies (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    project_id TEXT,
    policy_key TEXT NOT NULL,
    task_type TEXT NOT NULL,
    risk_level TEXT NOT NULL,
    model_policy TEXT NOT NULL,
    default_provider TEXT NOT NULL,
    escalation_provider TEXT,
    max_retry_before_escalate INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active',
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_by TEXT NOT NULL,
    updated_by TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at TIMESTAMPTZ
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_aix_model_gateway_policies_scoped_key
    ON aix_model_gateway_policies (tenant_id, project_id, policy_key);
CREATE INDEX IF NOT EXISTS idx_aix_model_gateway_policies_lookup
    ON aix_model_gateway_policies (tenant_id, task_type, risk_level, model_policy, status);

CREATE TABLE IF NOT EXISTS aix_model_provider_bindings (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    project_id TEXT,
    binding_key TEXT NOT NULL,
    provider TEXT NOT NULL,
    model_id TEXT NOT NULL,
    model_name TEXT NOT NULL,
    route_class TEXT NOT NULL,
    is_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_by TEXT NOT NULL,
    updated_by TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at TIMESTAMPTZ
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_aix_model_provider_bindings_scoped
    ON aix_model_provider_bindings (tenant_id, project_id, binding_key, provider, model_id, route_class);
CREATE INDEX IF NOT EXISTS idx_aix_model_provider_bindings_provider
    ON aix_model_provider_bindings (tenant_id, provider, is_enabled);

CREATE TABLE IF NOT EXISTS aix_model_health_status (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    project_id TEXT,
    provider TEXT NOT NULL,
    model_id TEXT NOT NULL,
    model_name TEXT NOT NULL,
    health_status TEXT NOT NULL,
    last_checked_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    latency_ms INTEGER,
    error_rate_pct NUMERIC(8,4),
    health_source TEXT NOT NULL DEFAULT 'system',
    details_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_by TEXT NOT NULL,
    updated_by TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at TIMESTAMPTZ
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_aix_model_health_status_scoped
    ON aix_model_health_status (tenant_id, project_id, provider, model_id);
CREATE INDEX IF NOT EXISTS idx_aix_model_health_status_lookup
    ON aix_model_health_status (tenant_id, provider, health_status, last_checked_at);

CREATE TABLE IF NOT EXISTS aix_escalation_policies (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    project_id TEXT,
    policy_key TEXT NOT NULL,
    task_type TEXT NOT NULL,
    risk_level TEXT NOT NULL,
    retry_threshold INTEGER NOT NULL DEFAULT 1,
    escalate_to_provider TEXT NOT NULL,
    block_on_provider_unhealthy BOOLEAN NOT NULL DEFAULT TRUE,
    status TEXT NOT NULL DEFAULT 'active',
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_by TEXT NOT NULL,
    updated_by TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at TIMESTAMPTZ
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_aix_escalation_policies_scoped_key
    ON aix_escalation_policies (tenant_id, project_id, policy_key);
CREATE INDEX IF NOT EXISTS idx_aix_escalation_policies_lookup
    ON aix_escalation_policies (tenant_id, task_type, risk_level, status);

CREATE TABLE IF NOT EXISTS aix_gateway_decisions (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    project_id TEXT,
    correlation_id TEXT NOT NULL,
    workhub_task_id TEXT,
    workhub_task_number INTEGER,
    opsbus_run_id TEXT,
    task_type TEXT NOT NULL,
    risk_level TEXT NOT NULL,
    model_policy TEXT NOT NULL,
    selected_provider TEXT,
    selected_model_id TEXT,
    selected_model_name TEXT,
    decision_status TEXT NOT NULL,
    decision_reason TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0,
    escalation_attempted BOOLEAN NOT NULL DEFAULT FALSE,
    latency_ms INTEGER,
    tokens_used INTEGER,
    estimated_cost NUMERIC(18,8),
    decision_payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_by TEXT NOT NULL,
    updated_by TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_aix_gateway_decisions_corr
    ON aix_gateway_decisions (tenant_id, correlation_id, created_at);
CREATE INDEX IF NOT EXISTS idx_aix_gateway_decisions_task
    ON aix_gateway_decisions (tenant_id, workhub_task_number, created_at);
CREATE INDEX IF NOT EXISTS idx_aix_gateway_decisions_run
    ON aix_gateway_decisions (tenant_id, opsbus_run_id, created_at);

CREATE TABLE IF NOT EXISTS outbox (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    record_version INTEGER NOT NULL DEFAULT 1,
    project_id TEXT,
    event_type TEXT NOT NULL,
    payload JSONB NOT NULL,
    correlation_id TEXT,
    causation_id TEXT,
    idempotency_key TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    published_at TIMESTAMPTZ,
    custom_data JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by TEXT,
    updated_by TEXT,
    deleted_at TIMESTAMPTZ,
    UNIQUE (tenant_id, id)
);

CREATE TABLE IF NOT EXISTS inbox (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    record_version INTEGER NOT NULL DEFAULT 1,
    project_id TEXT,
    event_type TEXT NOT NULL,
    payload JSONB NOT NULL,
    correlation_id TEXT,
    causation_id TEXT,
    processing_status TEXT NOT NULL DEFAULT 'received',
    error_message TEXT,
    processed_at TIMESTAMPTZ,
    custom_data JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by TEXT,
    updated_by TEXT,
    deleted_at TIMESTAMPTZ,
    UNIQUE (tenant_id, id)
);

CREATE TABLE IF NOT EXISTS idempotency_keys (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    record_version INTEGER NOT NULL DEFAULT 1,
    project_id TEXT,
    idempotency_key TEXT NOT NULL,
    request_hash TEXT,
    response_payload JSONB,
    status_code INTEGER,
    expires_at TIMESTAMPTZ,
    custom_data JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by TEXT,
    updated_by TEXT,
    deleted_at TIMESTAMPTZ,
    UNIQUE (tenant_id, idempotency_key)
);

CREATE TABLE IF NOT EXISTS admin_audit_log (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    record_version INTEGER NOT NULL DEFAULT 1,
    project_id TEXT,
    actor_sub TEXT NOT NULL,
    actor_roles JSONB NOT NULL DEFAULT '[]'::jsonb,
    action TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    resource_id TEXT,
    request_id TEXT,
    metadata_json JSONB DEFAULT '{}'::jsonb,
    custom_data JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by TEXT,
    updated_by TEXT,
    deleted_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS service_config (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    record_version INTEGER NOT NULL DEFAULT 1,
    project_id TEXT,
    config_key TEXT NOT NULL,
    config_value JSONB NOT NULL,
    custom_data JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by TEXT,
    updated_by TEXT,
    deleted_at TIMESTAMPTZ,
    UNIQUE (tenant_id, project_id, config_key)
);

CREATE TABLE IF NOT EXISTS feature_flags (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    record_version INTEGER NOT NULL DEFAULT 1,
    project_id TEXT,
    flag_key TEXT NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT FALSE,
    rules_json JSONB DEFAULT '{}'::jsonb,
    custom_data JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by TEXT,
    updated_by TEXT,
    deleted_at TIMESTAMPTZ,
    UNIQUE (tenant_id, project_id, flag_key)
);
