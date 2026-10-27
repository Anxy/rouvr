"""Wiring providers for the Session Factory (Wave 15 wiring slice).

Providers read shared instances from ``request.app.state`` (fleet
convention per ``objective_cycle_router.get_cycle_api_backend``):

- ``app.state.session_redis`` — redis client (``redis==5.0.8``).
- ``app.state.session_run_store`` — ``RunStore`` implementation
  persisting ``aix_runs`` rows (existing table, no migration).

Absent wiring fails closed 501 (same contract as the skeleton).
Tests inject fakes via ``configure_*`` (mirrors the
``configure_log_path`` test pattern of the amendment gate).
"""

from __future__ import annotations

from typing import Any, Protocol

from fastapi import HTTPException, Request, status


class RunStore(Protocol):
    """Persist one ``aix_runs`` record (see ``capsule.build_run_record``)."""

    def insert_run(self, record: dict[str, Any]) -> None: ...


class ActivationCoordinator(Protocol):
    async def accept_selection(self, event: Any) -> dict[str, Any]: ...
    async def accept_result(self, event: Any) -> dict[str, Any]: ...
    async def accept_verdict(self, event: Any) -> dict[str, Any]: ...


class ActivationEventBridge(Protocol):
    async def enqueue(
        self, event_id: str, event_kind: str, payload: dict[str, Any]
    ) -> None: ...


class ActivationAuthenticator(Protocol):
    async def __call__(self, authorization: str) -> Any: ...


class BindingVerifier(Protocol):
    async def __call__(self, binding: dict[str, Any]) -> dict[str, Any]: ...


_REDIS_ATTR = "session_redis"
_RUN_STORE_ATTR = "session_run_store"
_ACTIVATION_COORDINATOR_ATTR = "session_activation_coordinator"
_ACTIVATION_BRIDGE_ATTR = "session_activation_bridge"
_ACTIVATION_AUTHENTICATOR_ATTR = "session_activation_authenticator"


def configure_redis(app: Any, client: Any) -> None:
    """Attach a redis client to the app (tests / wiring bootstrap)."""
    app.state.session_redis = client


def configure_run_store(app: Any, store: RunStore) -> None:
    """Attach a run store to the app (tests / wiring bootstrap)."""
    app.state.session_run_store = store


def configure_activation_coordinator(
    app: Any, coordinator: ActivationCoordinator
) -> None:
    app.state.session_activation_coordinator = coordinator


def configure_activation_ingress(
    app: Any, bridge: ActivationEventBridge, authenticator: ActivationAuthenticator
) -> None:
    app.state.session_activation_bridge = bridge
    app.state.session_activation_authenticator = authenticator


def configure_binding_verifier(app: Any, verifier: BindingVerifier) -> None:
    app.state.session_binding_verifier = verifier


def get_binding_verifier(request: Request) -> BindingVerifier:
    verifier = getattr(request.app.state, "session_binding_verifier", None)
    if verifier is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="SESSION_BINDING_VERIFIER_NOT_WIRED",
        )
    return verifier


def get_activation_bridge(request: Request) -> ActivationEventBridge:
    bridge = getattr(request.app.state, _ACTIVATION_BRIDGE_ATTR, None)
    if bridge is None:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="SESSION_ACTIVATION_INGRESS_NOT_WIRED",
        )
    return bridge


def get_activation_authenticator(request: Request) -> ActivationAuthenticator:
    authenticator = getattr(request.app.state, _ACTIVATION_AUTHENTICATOR_ATTR, None)
    if authenticator is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="SESSION_ACTIVATION_AUTH_NOT_WIRED",
        )
    return authenticator


def get_activation_coordinator(request: Request) -> ActivationCoordinator:
    coordinator = getattr(request.app.state, _ACTIVATION_COORDINATOR_ATTR, None)
    if coordinator is None:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="SESSION_ACTIVATION_NOT_WIRED",
        )
    return coordinator


def get_activation_store(request: Request) -> Any:
    store = getattr(request.app.state, "session_activation_store", None)
    if store is None:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="SESSION_ACTIVATION_STORE_NOT_WIRED",
        )
    return store


def get_redis_client(request: Request) -> Any:
    client = getattr(request.app.state, _REDIS_ATTR, None)
    if client is None:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="SESSION_REDIS_NOT_WIRED",
        )
    return client


def get_run_store(request: Request) -> RunStore:
    store = getattr(request.app.state, _RUN_STORE_ATTR, None)
    if store is None:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="SESSION_RUN_STORE_NOT_WIRED",
        )
    return store
