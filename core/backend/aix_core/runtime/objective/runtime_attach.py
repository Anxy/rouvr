"""Attach objective-cycle security policy + durable API backend to FastAPI app.state.

Fail-closed by default: if configuration is incomplete, app.state fields stay unset and
the mounted router continues to return 503 (existing contract).
"""

from __future__ import annotations

import math
import os
from typing import Any
from urllib.parse import urlparse, urlunparse

import structlog

from backend.aix_core.runtime.objective.api_security import CycleApiSecurityPolicy
from backend.aix_core.runtime.objective.durable_api_backend import DurableObjectiveCycleApiBackend
from backend.hyeve_aix_service.config import FrameworkFlagsSnapshot
from backend.aix_core.runtime.objective.repository import (
    ObjectiveCycleRepository,
    ObjectiveCycleRepositoryError,
    resolve_test_database_url,
)

logger = structlog.get_logger("hyeve_aix_service.objective_cycle.bootstrap")


def _as_asyncpg_url(database_url: str) -> str:
    return (
        database_url.replace("postgresql+asyncpg://", "postgresql://")
        .replace("postgres+asyncpg://", "postgresql://")
    )


def _remap_docker_db_host(database_url: str) -> str:
    """Map compose DNS names to localhost when running outside Docker."""
    raw = _as_asyncpg_url(database_url)
    parsed = urlparse(raw)
    if parsed.hostname not in {"db", "postgres", "postgresql"}:
        return raw
    host = "127.0.0.1"
    netloc = f"{parsed.username}:{parsed.password}@{host}:{parsed.port or 5432}"
    return urlunparse(parsed._replace(netloc=netloc))


def build_objective_cycle_security_policy(settings: object) -> CycleApiSecurityPolicy | None:
    secret = str(getattr(settings, "OBJECTIVE_CYCLE_GATEWAY_SECRET", "") or "").strip()
    if not secret:
        secret = str(os.getenv("HYEVE_AIX_SERVICE_OBJECTIVE_CYCLE_GATEWAY_SECRET", "") or "").strip()
    if not secret:
        return None
    gateways_raw = str(getattr(settings, "OBJECTIVE_CYCLE_ALLOWED_GATEWAYS", "") or "").strip()
    if not gateways_raw:
        gateways_raw = str(os.getenv("HYEVE_AIX_SERVICE_OBJECTIVE_CYCLE_ALLOWED_GATEWAYS", "aix-bff") or "aix-bff")
    allowed = tuple(item.strip() for item in gateways_raw.split(",") if item.strip())
    max_age = int(getattr(settings, "OBJECTIVE_CYCLE_ASSERTION_MAX_AGE_SECONDS", 300) or 300)
    return CycleApiSecurityPolicy(
        secret=secret.encode("utf-8"),
        allowed_gateway_ids=allowed,
        max_assertion_age_seconds=max_age,
    )


def _env_flag(name: str, *, default: bool) -> bool:
    raw = str(os.getenv(name, "") or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _muse_setting(settings: object, name: str, env_name: str, default: str) -> str:
    raw = str(getattr(settings, name, "") or "").strip()
    if raw:
        return raw
    return str(os.getenv(env_name, "") or "").strip() or default


def _build_muse_runtime(
    settings: object,
) -> tuple[Any, Any, bool, str]:
    """Build the Muse session transport + activation lookup when configured.

    Returns (session_port, binding_lookup, bound, reason). Everything is
    opt-in and fail-closed: any missing piece leaves the runtime unbound and
    Muse-routed steps refuse at dispatch with MUSE_RUNTIME_NOT_BOUND instead
    of falling back to another executor. Lazy imports keep this module free
    of hard session-factory/transport dependencies when disabled.
    """
    from backend.aix_core.framework.execution.muse_execute_adapter import (
        MuseCapabilityPolicy,
    )
    from backend.aix_core.framework.execution.muse_session import StdioMspSessionPort

    enabled = bool(getattr(settings, "MUSE_SESSION_ENABLED", False))
    if not enabled:
        enabled = _env_flag("HYEVE_AIX_SERVICE_MUSE_SESSION_ENABLED", default=False)
    if not enabled:
        return None, None, False, "MUSE_SESSION_DISABLED"
    if not bool(getattr(settings, "SESSION_ACTIVATION_STORE_ENABLED", False)):
        return None, None, False, "MUSE_SESSION_NO_ACTIVATION_STORE"
    database_url = str(getattr(settings, "DATABASE_URL", "") or "").strip()
    lease_owner = str(getattr(settings, "SESSION_ACTIVATION_LEASE_OWNER", "") or "").strip()
    if not database_url or not lease_owner:
        return None, None, False, "MUSE_SESSION_NO_ACTIVATION_CONFIG"
    try:
        lease_seconds = int(getattr(settings, "SESSION_ACTIVATION_RELEASE_LEASE_SECONDS", 30) or 30)
    except (TypeError, ValueError):
        return None, None, False, "MUSE_SESSION_BAD_LEASE_CONFIG"
    try:
        from backend.aix_core.framework.execution.muse_execute_adapter import (
            MuseCapabilityPolicy,
            validate_capability_policy,
        )

        # Prove the configured posture at startup so a forbidden posture
        # fails here, never at first dispatch.
        validate_capability_policy(MuseCapabilityPolicy(
            approval_mode=_muse_setting(
                settings, "MUSE_SESSION_APPROVAL_MODE",
                "HYEVE_AIX_SERVICE_MUSE_SESSION_APPROVAL_MODE", "denyUnmatched",
            ),
            permission_profile=_muse_setting(
                settings, "MUSE_SESSION_PERMISSION_PROFILE",
                "HYEVE_AIX_SERVICE_MUSE_SESSION_PERMISSION_PROFILE", ":read-only",
            ),
        ))
    except ValueError as exc:
        return None, None, False, f"MUSE_SESSION_POLICY_REFUSED:{exc}"
    muse_bin = _muse_setting(
        settings, "MUSE_SESSION_BIN", "HYEVE_AIX_SERVICE_MUSE_SESSION_BIN", ""
    )
    try:
        session_port = StdioMspSessionPort(muse_bin=muse_bin or None)
    except Exception as exc:  # noqa: BLE001 - binary absence leaves runtime unbound
        logger.warning("objective_cycle.muse_session.unbound", reason=f"{type(exc).__name__}:{exc}")
        return None, None, False, "MUSE_SESSION_NO_BINARY"
    try:
        from backend.session_factory.activation_store import PostgresActivationStore

        binding_lookup = PostgresActivationStore(
            database_url,
            lease_owner=lease_owner,
            lease_seconds=lease_seconds if lease_seconds >= 1 else 30,
        )
    except ValueError as exc:
        return None, None, False, f"MUSE_SESSION_STORE_REFUSED:{exc}"
    return session_port, binding_lookup, True, "MUSE_SESSION_BOUND"


def _build_muse_preflight_verifier(settings: object) -> tuple[Any, bool, str]:
    """Build the credentialed owner-side preflight verifier when configured.

    Returns (verifier, bound, reason). Uses only the supplied settings
    object (WORKHUB_API_URL / WORKHUB_API_TOKEN /
    WORKHUB_API_TIMEOUT_SECONDS) over the maintained WorkHubTaskClient —
    no caller auth, no token minting, no credential file, no second HTTP
    stack. Any gap leaves the verifier unbound with a stable non-secret
    reason, and Muse routes keep refusing MUSE_PREFLIGHT_UNBOUND instead
    of running without preflight. Reasons never contain secret material.
    """
    from backend.aix_core.framework.integration.workhub.client import (
        WorkHubTaskClient,
    )
    from backend.session_factory.muse_preflight import WorkHubPreflightVerifier

    base_url = str(getattr(settings, "WORKHUB_API_URL", "") or "").strip()
    if not base_url:
        return None, False, "MUSE_PREFLIGHT_NO_WORKHUB_URL"
    token = str(getattr(settings, "WORKHUB_API_TOKEN", "") or "").strip()
    if not token:
        return None, False, "MUSE_PREFLIGHT_NO_WORKHUB_TOKEN"
    try:
        timeout = float(getattr(settings, "WORKHUB_API_TIMEOUT_SECONDS", 15) or 15)
    except (TypeError, ValueError):
        return None, False, "MUSE_PREFLIGHT_BAD_WORKHUB_TIMEOUT"
    if not math.isfinite(timeout) or timeout <= 0:
        return None, False, "MUSE_PREFLIGHT_BAD_WORKHUB_TIMEOUT"
    try:
        client = WorkHubTaskClient(
            base_url=base_url,
            service_token=token,
            timeout_seconds=timeout,
        )
        verifier = WorkHubPreflightVerifier(transport=client)  # type: ignore[arg-type]
    except Exception as exc:  # noqa: BLE001 - construction failure leaves preflight unbound
        logger.warning(
            "objective_cycle.muse_preflight.unbound",
            reason="MUSE_PREFLIGHT_CLIENT_REFUSED",
            error_type=type(exc).__name__,
        )
        return None, False, "MUSE_PREFLIGHT_CLIENT_REFUSED"
    return verifier, True, "MUSE_PREFLIGHT_BOUND"


def _real_execute_flags(settings: object) -> tuple[bool, bool, bool, bool]:
    enabled = bool(getattr(settings, "OBJECTIVE_CYCLE_REAL_EXECUTE", False))
    if not enabled:
        enabled = _env_flag("HYEVE_AIX_SERVICE_OBJECTIVE_CYCLE_REAL_EXECUTE", default=False)
    require_load = bool(getattr(settings, "OBJECTIVE_CYCLE_REQUIRE_WORKHUB_LOAD", True))
    env_req = os.getenv("HYEVE_AIX_SERVICE_OBJECTIVE_CYCLE_REQUIRE_WORKHUB_LOAD")
    if env_req is not None and str(env_req).strip() != "":
        require_load = str(env_req).strip().lower() not in {"0", "false", "no", "off"}
    create_tasks = bool(getattr(settings, "OBJECTIVE_CYCLE_CREATE_WORKHUB_TASK", False))
    if not create_tasks:
        create_tasks = _env_flag("HYEVE_AIX_SERVICE_OBJECTIVE_CYCLE_CREATE_WORKHUB_TASK", default=False)
    mark_done = bool(getattr(settings, "OBJECTIVE_CYCLE_MARK_WORKHUB_DONE", False))
    if not mark_done:
        mark_done = _env_flag("HYEVE_AIX_SERVICE_OBJECTIVE_CYCLE_MARK_WORKHUB_DONE", default=False)
    return enabled, require_load, create_tasks, mark_done


async def build_objective_cycle_api_backend(settings: object) -> DurableObjectiveCycleApiBackend | None:
    allow_inprocess = bool(getattr(settings, "OBJECTIVE_CYCLE_ALLOW_INPROCESS_LEDGER", False))
    prefer_postgres = bool(getattr(settings, "OBJECTIVE_CYCLE_PREFER_POSTGRES", True))
    real_execute_enabled, require_workhub_load, create_workhub_tasks, mark_workhub_done = _real_execute_flags(settings)
    framework_flags = FrameworkFlagsSnapshot.from_settings(settings)
    muse_session_port, muse_binding_lookup, muse_bound, muse_reason = _build_muse_runtime(settings)
    # Production preflight composition (lane-ops #23): the credentialed
    # owner-side verifier is built only when the session transport/store
    # prerequisites are already valid. Otherwise — and on any settings or
    # construction gap — the verifier stays unbound and Muse routes keep
    # refusing MUSE_PREFLIGHT_UNBOUND instead of running without preflight.
    if muse_bound:
        muse_preflight_verifier, preflight_bound, preflight_reason = _build_muse_preflight_verifier(settings)
    else:
        muse_preflight_verifier, preflight_bound, preflight_reason = (
            None, False, "MUSE_PREFLIGHT_SESSION_UNBOUND",
        )
    logger.info(
        "objective_cycle.muse_session",
        bound=muse_bound,
        reason=muse_reason,
        preflight_bound=preflight_bound,
        preflight_reason=preflight_reason,
    )

    database_url = resolve_test_database_url()
    if not database_url:
        database_url = str(getattr(settings, "DATABASE_URL", "") or "").strip() or None
    if database_url and prefer_postgres:
        remapped = _remap_docker_db_host(database_url)
        try:
            repo = ObjectiveCycleRepository(remapped, ensure_schema=True)
            import asyncpg

            from backend.aix_core.runtime.verification.gap import ensure_gaps_schema

            pool = await asyncpg.create_pool(remapped, min_size=1, max_size=5)
            async with pool.acquire() as conn:
                await ensure_gaps_schema(conn)
            gap_store_path = str(os.getenv("AIX_GAP_STORE_PATH") or "").strip() or None
            logger.info(
                "objective_cycle.backend.postgres_ready",
                real_execute_enabled=real_execute_enabled,
                create_workhub_tasks=create_workhub_tasks,
                mark_workhub_done=mark_workhub_done,
            )
            backend = DurableObjectiveCycleApiBackend(
                mode="postgres",
                repository=repo,
                real_execute_enabled=real_execute_enabled,
                require_workhub_load=require_workhub_load,
                create_workhub_tasks=create_workhub_tasks,
                mark_workhub_done=mark_workhub_done,
                settings=settings,
                muse_session_port=muse_session_port,
                muse_binding_lookup=muse_binding_lookup,
                muse_preflight_verifier=muse_preflight_verifier,
                framework_flags=framework_flags,
                gap_store_pg_conn=pool,
                gap_store_database_url=remapped,
                gap_store_path=gap_store_path,
            )
            backend.muse_session_status = {
                "bound": muse_bound,
                "reason": muse_reason,
                "preflight_bound": preflight_bound,
                "preflight_reason": preflight_reason,
            }
            return backend
        except (ObjectiveCycleRepositoryError, OSError, TimeoutError) as exc:
            # Wave 2 fail-closed: DATABASE_URL is configured but Postgres is
            # unreachable. Silently falling through to file state would fork
            # the state authority (PG vs JSON divergence). Block startup
            # unless explicitly offline (AIX_OFFLINE=1, Wave 2 JSON-only path).
            offline = bool(getattr(settings, "AIX_OFFLINE", False))
            logger.warning(
                "objective_cycle.backend.postgres_unavailable",
                error_type=type(exc).__name__,
                allow_inprocess=allow_inprocess,
                offline=offline,
            )
            if not offline:
                raise ObjectiveCycleRepositoryError(
                    f"DATABASE_URL is configured but Postgres unreachable "
                    f"({type(exc).__name__}); refusing silent file fallback "
                    f"(set AIX_OFFLINE=1 for the JSON-only path)"
                ) from exc

    if allow_inprocess:
        projection_store = None
        projection_dir = str(os.getenv("OBJECTIVE_CYCLE_PROJECTION_DIR") or "").strip()
        if projection_dir:
            from backend.aix_core.runtime.objective.durable_aggregate.replay import (
                JsonFileProjectionStore,
            )

            projection_store = JsonFileProjectionStore(projection_dir)
        gap_store_path = str(os.getenv("AIX_GAP_STORE_PATH") or "").strip() or None
        logger.info(
            "objective_cycle.backend.inprocess_enabled",
            real_execute_enabled=real_execute_enabled,
            create_workhub_tasks=create_workhub_tasks,
            mark_workhub_done=mark_workhub_done,
        )
        backend = DurableObjectiveCycleApiBackend(
            mode="inprocess",
            real_execute_enabled=real_execute_enabled,
            require_workhub_load=require_workhub_load,
            create_workhub_tasks=create_workhub_tasks,
            mark_workhub_done=mark_workhub_done,
            settings=settings,
            muse_session_port=muse_session_port,
            muse_binding_lookup=muse_binding_lookup,
            muse_preflight_verifier=muse_preflight_verifier,
            framework_flags=framework_flags,
            projection_store=projection_store,
            gap_store_path=gap_store_path,
        )
        backend.muse_session_status = {
            "bound": muse_bound,
            "reason": muse_reason,
            "preflight_bound": preflight_bound,
            "preflight_reason": preflight_reason,
        }
        return backend

    logger.warning("objective_cycle.backend.not_configured")
    return None


async def attach_objective_cycle_runtime(app: Any, settings: object) -> dict[str, Any]:
    """Attach policy + backend to app.state. Returns bootstrap status payload."""
    status: dict[str, Any] = {
        "security_policy": False,
        "api_backend": False,
        "backend_mode": None,
        "real_execute_enabled": False,
        "create_workhub_tasks": False,
        "mark_workhub_done": False,
        "workhub_lifecycle_authority": False,
        "aix_creates_workhub_tasks": False,
        "aix_marks_workhub_done": False,
        "production_go": False,
        "exception": None,
    }
    policy = build_objective_cycle_security_policy(settings)
    if policy is not None:
        app.state.objective_cycle_security_policy = policy
        status["security_policy"] = True

    backend = await build_objective_cycle_api_backend(settings)
    if backend is not None:
        app.state.objective_cycle_api_backend = backend
        status["api_backend"] = True
        status["backend_mode"] = backend.mode
        muse_status = getattr(backend, "muse_session_status", None) or {}
        status["muse_session_bound"] = bool(muse_status.get("bound", False))
        status["muse_session_reason"] = str(muse_status.get("reason", "MUSE_SESSION_UNKNOWN"))
        # Complete Muse execution binding needs the session transport AND
        # the credentialed preflight verifier. Session binding alone must
        # never read as production-ready Muse execution.
        status["muse_preflight_bound"] = bool(muse_status.get("preflight_bound", False))
        status["muse_preflight_reason"] = str(
            muse_status.get("preflight_reason", "MUSE_PREFLIGHT_UNKNOWN")
        )
        status["muse_execution_bound"] = bool(
            status["muse_session_bound"] and status["muse_preflight_bound"]
        )
        status["await_execution"] = bool(getattr(backend, "_await_execution", True))
        status["real_execute_enabled"] = bool(backend.real_execute_enabled)
        status["create_workhub_tasks"] = bool(backend.create_workhub_tasks)
        status["mark_workhub_done"] = bool(backend.mark_workhub_done)
        status["aix_creates_workhub_tasks"] = bool(backend.create_workhub_tasks)
        status["aix_marks_workhub_done"] = bool(backend.mark_workhub_done)
        status["workhub_lifecycle_authority"] = bool(backend.mark_workhub_done)
        if backend.mark_workhub_done:
            status["exception"] = "001P-W13"
        elif backend.create_workhub_tasks:
            status["exception"] = "001P-W12"

    app.state.objective_cycle_bootstrap = status
    logger.info("objective_cycle.bootstrap", **status)
    return status
