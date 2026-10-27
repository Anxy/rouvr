from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog

from backend.workflows.temporal.objective_cycle_activities import (
    CANONICAL_ACTIVITIES,
)
from backend.workflows.temporal.objective_cycle_contracts import (
    OBJECTIVE_CYCLE_WORKFLOW_TYPE,
    ObjectiveCycleWorkflowInput,
    objective_cycle_workflow_id,
)
from backend.workflows.temporal.objective_cycle_workflow import (
    AixObjectiveCycleWorkflow,
)

from .queue_strategy import QueueBinding, build_queue_binding

# Final single-path architecture: exactly one registered workflow. The legacy
# v2 workflow path is retired; it remains on disk under backend/legacy/ as
# retained evidence only and is never imported or registered here.
_WORKFLOW_NAME = OBJECTIVE_CYCLE_WORKFLOW_TYPE
_REGISTERED_WORKFLOW_CLASSES = [
    AixObjectiveCycleWorkflow,
]
_ACTIVITY_NAMES = [f"objective_cycle.{name}" for name in CANONICAL_ACTIVITIES]


@dataclass(frozen=True)
class TemporalSdk:
    client_cls: Any
    worker_cls: Any


@dataclass(frozen=True)
class _StarterSurface:
    """Typed Temporal start/recovery surface (real SDK 1.32.0).

    Every safety argument is explicit: the reuse policy, the conflict
    policy, the typed already-started error, and the history/status
    enums used by exact recovery. Anything missing refuses instead of
    silently dropping safety arguments.
    """

    reuse_reject_duplicate: Any
    conflict_fail: Any
    already_started: Any
    event_started: Any
    status_running: Any
    status_terminal: frozenset


def _require_starter_surface() -> _StarterSurface:
    """Resolve the typed start/recovery surface or fail closed.

    The documented namespace is temporalio.common for both policies
    (supported by real 1.32.0 and worker 1.10 alike), so no generation
    is labeled unsupported by default. Only a genuinely missing piece
    of the surface raises TEMPORAL_SDK_SURFACE_UNSUPPORTED here
    instead of starting with silently dropped safety arguments.
    """
    try:
        from temporalio.api.enums.v1 import EventType, WorkflowExecutionStatus
        from temporalio.common import WorkflowIDConflictPolicy, WorkflowIDReusePolicy
        from temporalio.exceptions import WorkflowAlreadyStartedError
    except Exception as exc:
        raise RuntimeError("TEMPORAL_SDK_SURFACE_UNSUPPORTED") from exc
    return _StarterSurface(
        reuse_reject_duplicate=WorkflowIDReusePolicy.REJECT_DUPLICATE,
        conflict_fail=WorkflowIDConflictPolicy.FAIL,
        already_started=WorkflowAlreadyStartedError,
        event_started=EventType.EVENT_TYPE_WORKFLOW_EXECUTION_STARTED,
        status_running=WorkflowExecutionStatus.WORKFLOW_EXECUTION_STATUS_RUNNING,
        status_terminal=frozenset(
            {
                WorkflowExecutionStatus.WORKFLOW_EXECUTION_STATUS_COMPLETED,
                WorkflowExecutionStatus.WORKFLOW_EXECUTION_STATUS_FAILED,
                WorkflowExecutionStatus.WORKFLOW_EXECUTION_STATUS_CANCELED,
                WorkflowExecutionStatus.WORKFLOW_EXECUTION_STATUS_TERMINATED,
                WorkflowExecutionStatus.WORKFLOW_EXECUTION_STATUS_CONTINUED_AS_NEW,
                WorkflowExecutionStatus.WORKFLOW_EXECUTION_STATUS_TIMED_OUT,
            }
        ),
    )


def detect_temporal_sdk() -> TemporalSdk | None:
    try:  # pragma: no cover - optional dependency
        from temporalio.client import Client
        from temporalio.worker import Worker

        return TemporalSdk(client_cls=Client, worker_cls=Worker)
    except Exception:
        return None


class TemporalRuntimeManager:
    def __init__(
        self,
        *,
        settings: object,
        sdk_loader: Callable[[], TemporalSdk | None] = detect_temporal_sdk,
    ) -> None:
        self._settings = settings
        self._binding: QueueBinding = build_queue_binding(settings)
        self._sdk_loader = sdk_loader
        self._logger = structlog.get_logger("hyeve_aix_service.temporal.runtime")
        self._worker_task: asyncio.Task[None] | None = None
        self._worker_instance: Any | None = None
        self._startup_status: dict[str, Any] = {}
        self._worker_status: dict[str, Any] = {
            "state": "not_started",
            "attempt": 0,
            "last_error": None,
            "last_error_type": None,
            "last_failure_at": None,
            "last_ready_at": None,
            "retry_delay_seconds": 2.0,
            "queue": self._binding.execution_queue,
            "workflow": _WORKFLOW_NAME,
        }

    @property
    def binding(self) -> QueueBinding:
        return self._binding

    def bootstrap_metadata(self) -> dict[str, Any]:
        temporal_sdk_available = self._sdk_loader() is not None
        return {
            "enabled": bool(getattr(self._settings, "TEMPORAL_ENABLED", False)),
            "auto_start_worker": bool(
                getattr(self._settings, "TEMPORAL_AUTO_START_WORKER", False)
            ),
            "temporal_sdk_available": temporal_sdk_available,
            "address": str(
                getattr(self._settings, "TEMPORAL_ADDRESS", "127.0.0.1:7233")
            ),
            "namespace": self._binding.namespace,
            "task_queues": {
                "plan": self._binding.plan_queue,
                "execution": self._binding.execution_queue,
                "verification": self._binding.verification_queue,
            },
        }

    @staticmethod
    def _utcnow() -> str:
        return datetime.now(UTC).isoformat()

    def _set_worker_status(self, **updates: Any) -> None:
        self._worker_status.update(updates)

    def _emit_worker_lifecycle(self, event: str, **payload: Any) -> None:
        self._logger.info(
            event,
            queue=self._binding.execution_queue,
            workflow=_WORKFLOW_NAME,
            namespace=self._binding.namespace,
            **payload,
        )

    def _worker_task_state(self) -> str:
        if self._worker_task is None:
            return "not_started"
        if self._worker_task.cancelled():
            return "cancelled"
        if self._worker_task.done():
            return "done"
        return "running"

    def runtime_status(self) -> dict[str, Any]:
        status = dict(self._startup_status or self.bootstrap_metadata())
        worker_status = dict(self._worker_status)
        worker_status["task_state"] = self._worker_task_state()
        if (
            self._worker_task is not None
            and self._worker_task.done()
            and not self._worker_task.cancelled()
        ):
            exc = self._worker_task.exception()
            if exc is not None:
                worker_status["task_exception_type"] = type(exc).__name__
                worker_status["task_exception"] = str(exc)
        status["worker"] = worker_status
        return status

    def _handle_worker_task_completion(self, task: asyncio.Task[None]) -> None:
        if task.cancelled():
            self._set_worker_status(state="stopped")
            self._emit_worker_lifecycle(
                "temporal.worker_stopped",
                reason_code="CANCELLED",
            )
            self._logger.info(
                "temporal.worker_task_stopped",
                queue=self._binding.execution_queue,
                workflow=_WORKFLOW_NAME,
                reason_code="CANCELLED",
            )
            return
        exc = task.exception()
        if exc is None:
            self._set_worker_status(state="stopped")
            self._emit_worker_lifecycle(
                "temporal.worker_stopped",
                reason_code="WORKER_RUN_RETURNED",
            )
            self._logger.warning(
                "temporal.worker_task_exited",
                queue=self._binding.execution_queue,
                workflow=_WORKFLOW_NAME,
                reason_code="WORKER_RUN_RETURNED",
            )
            return
        self._set_worker_status(
            state="failed",
            last_error=str(exc),
            last_error_type=type(exc).__name__,
            last_failure_at=self._utcnow(),
        )
        self._emit_worker_lifecycle(
            "temporal.worker_failed",
            reason_code=type(exc).__name__,
            error=str(exc),
        )
        self._logger.error(
            "temporal.worker_task_failed",
            queue=self._binding.execution_queue,
            workflow=_WORKFLOW_NAME,
            reason_code=type(exc).__name__,
            error=str(exc),
        )

    async def startup(self) -> dict[str, Any]:
        metadata = self.bootstrap_metadata()
        self._logger.info("temporal.worker_bootstrap_start", **metadata)
        self._emit_worker_lifecycle(
            "temporal.worker_queue_binding",
            plan_queue=self._binding.plan_queue,
            execution_queue=self._binding.execution_queue,
            verification_queue=self._binding.verification_queue,
        )
        if not metadata["enabled"]:
            self._startup_status = {"mode": "disabled", **metadata}
            return self._startup_status

        if not metadata["temporal_sdk_available"]:
            self._startup_status = {
                "mode": "degraded",
                "reason": "TEMPORAL_SDK_NOT_INSTALLED",
                **metadata,
            }
            self._logger.warning("temporal.bootstrap.degraded", **self._startup_status)
            return self._startup_status

        if not metadata["auto_start_worker"]:
            self._set_worker_status(state="configured")
            self._startup_status = {"mode": "configured", **metadata}
            self._logger.info("temporal.bootstrap.configured", **self._startup_status)
            return self._startup_status

        self._worker_task = asyncio.create_task(self.run_worker_forever())
        self._worker_task.add_done_callback(self._handle_worker_task_completion)
        self._set_worker_status(state="starting")
        self._startup_status = {"mode": "running", **metadata}
        self._logger.info("temporal.bootstrap.running", **self._startup_status)
        return self._startup_status

    async def shutdown(self) -> None:
        if self._worker_task is None:
            return
        self._worker_task.cancel()
        try:
            await self._worker_task
        except asyncio.CancelledError:
            pass
        finally:
            self._worker_task = None
            self._worker_instance = None

    async def run_worker_forever(self) -> None:
        sdk = self._sdk_loader()
        if sdk is None:
            raise RuntimeError("TEMPORAL_SDK_NOT_INSTALLED")
        retry_delay = float(self._worker_status.get("retry_delay_seconds") or 2.0)
        attempt = 0
        while True:
            attempt += 1
            self._set_worker_status(state="connecting", attempt=attempt)
            self._logger.info(
                "temporal.worker_connecting",
                address=str(
                    getattr(self._settings, "TEMPORAL_ADDRESS", "127.0.0.1:7233")
                ),
                namespace=self._binding.namespace,
                queue=self._binding.execution_queue,
                workflow=_WORKFLOW_NAME,
                attempt=attempt,
            )
            try:
                client = await sdk.client_cls.connect(
                    str(getattr(self._settings, "TEMPORAL_ADDRESS", "127.0.0.1:7233")),
                    namespace=self._binding.namespace,
                )
                workflows = list(_REGISTERED_WORKFLOW_CLASSES)
                activities = list(CANONICAL_ACTIVITIES.values())
                self._emit_worker_lifecycle(
                    "temporal.worker_workflow_registration",
                    attempt=attempt,
                    workflows=[workflow.__name__ for workflow in workflows],
                )
                self._emit_worker_lifecycle(
                    "temporal.worker_activity_registration",
                    attempt=attempt,
                    activities=list(_ACTIVITY_NAMES),
                )
                self._worker_instance = sdk.worker_cls(
                    client,
                    task_queue=self._binding.execution_queue,
                    workflows=workflows,
                    activities=activities,
                    identity=str(
                        getattr(
                            self._settings,
                            "TEMPORAL_WORKER_IDENTITY",
                            "hyeve-aix-worker",
                        )
                    ),
                )
                self._set_worker_status(
                    state="ready",
                    attempt=attempt,
                    last_error=None,
                    last_error_type=None,
                    last_ready_at=self._utcnow(),
                )
                self._logger.info(
                    "temporal.worker_ready",
                    queue=self._binding.execution_queue,
                    workflow=_WORKFLOW_NAME,
                    attempt=attempt,
                    activities=list(_ACTIVITY_NAMES),
                )
                await self._worker_instance.run()
                self._worker_instance = None
                self._set_worker_status(state="exited", attempt=attempt)
                self._logger.warning(
                    "temporal.worker_exited",
                    queue=self._binding.execution_queue,
                    workflow=_WORKFLOW_NAME,
                    attempt=attempt,
                    reason_code="WORKER_RUN_RETURNED",
                )
            except asyncio.CancelledError:
                self._worker_instance = None
                self._set_worker_status(state="stopped")
                raise
            except Exception as exc:
                self._worker_instance = None
                self._set_worker_status(
                    state="retry_wait",
                    attempt=attempt,
                    last_error=str(exc),
                    last_error_type=type(exc).__name__,
                    last_failure_at=self._utcnow(),
                )
                self._logger.error(
                    "temporal.worker_retry_scheduled",
                    queue=self._binding.execution_queue,
                    workflow=_WORKFLOW_NAME,
                    attempt=attempt,
                    reason_code=type(exc).__name__,
                    error=str(exc),
                    retry_delay_seconds=retry_delay,
                )
                self._emit_worker_lifecycle(
                    "temporal.worker_error",
                    attempt=attempt,
                    reason_code=type(exc).__name__,
                    error=str(exc),
                    retry_delay_seconds=retry_delay,
                )
            await asyncio.sleep(retry_delay)

    async def _recover_exact_workflow(
        self,
        client: Any,
        surface: _StarterSurface,
        *,
        workflow_id: str,
        expected: dict[str, Any],
        pinned_run_id: str | None,
    ) -> str:
        """Recover only the exact original workflow; never adopt or restart.

        Pins the exact run from the typed collision (or a first
        describe when the collision carries none), rebinds a run-pinned
        handle, and verifies ID/type/queue/run plus the started
        original_execution_run_id before trusting anything — a
        close/restart race cannot combine metadata from one run with
        input from another. Reads only the bounded first history page
        and inspects the first start event (event_id 1, STARTED),
        decodes its actual input with the public client data converter,
        validates the canonical model, and exact-compares every
        normalized workflow field (candidate/plan/start_request/
        correlation included). Missing, unreadable, or ambiguous
        history — or any transport error — holds/refuses; nothing here
        is interpreted as absence and no second start follows. A
        changed intent refuses adoption; an exact intent on a closed
        run refuses terminal replay without starting again, preserving
        history/intent for reconciliation.
        """
        get_handle = getattr(client, "get_workflow_handle", None)
        if not callable(get_handle):
            raise RuntimeError("TEMPORAL_SDK_SURFACE_UNSUPPORTED")
        pinned = str(pinned_run_id or "")
        if not pinned:
            try:
                alias_description = await get_handle(workflow_id).describe()
            except Exception as exc:
                raise RuntimeError(
                    "TEMPORAL_EXISTING_WORKFLOW_UNREADABLE"
                ) from exc
            pinned = str(getattr(alias_description, "run_id", "") or "")
            if not pinned:
                raise RuntimeError("TEMPORAL_EXISTING_WORKFLOW_UNREADABLE")
        handle = get_handle(workflow_id, run_id=pinned)
        describe = getattr(handle, "describe", None)
        fetch_page = getattr(handle, "fetch_history_events", None)
        if not callable(describe) or not callable(fetch_page):
            raise RuntimeError("TEMPORAL_SDK_SURFACE_UNSUPPORTED")
        try:
            description = await describe()
        except Exception as exc:
            raise RuntimeError("TEMPORAL_EXISTING_WORKFLOW_UNREADABLE") from exc
        observed_id = str(getattr(description, "id", "") or "")
        observed_type = str(getattr(description, "workflow_type", "") or "")
        observed_queue = str(getattr(description, "task_queue", "") or "")
        if (
            observed_id != workflow_id
            or observed_type != _WORKFLOW_NAME
            or observed_queue != self._binding.execution_queue
        ):
            raise RuntimeError("TEMPORAL_EXISTING_WORKFLOW_IDENTITY_MISMATCH")
        observed_run = str(getattr(description, "run_id", "") or "")
        if not observed_run:
            raise RuntimeError("TEMPORAL_EXISTING_WORKFLOW_UNREADABLE")
        if observed_run != pinned:
            raise RuntimeError("TEMPORAL_EXISTING_WORKFLOW_IDENTITY_MISMATCH")
        try:
            first_page = fetch_page(page_size=1)
        except Exception as exc:
            raise RuntimeError(
                "TEMPORAL_EXISTING_WORKFLOW_HISTORY_UNREADABLE"
            ) from exc
        if not hasattr(first_page, "__aiter__"):
            raise RuntimeError("TEMPORAL_SDK_SURFACE_UNSUPPORTED")
        first = None
        fetch_error: Exception | None = None
        try:
            async for event in first_page:
                first = event
                break
        except Exception as exc:
            fetch_error = exc
        aclose = getattr(first_page, "aclose", None)
        if callable(aclose):
            closed = aclose()
            if asyncio.iscoroutine(closed):
                await closed
        if fetch_error is not None:
            raise RuntimeError(
                "TEMPORAL_EXISTING_WORKFLOW_HISTORY_UNREADABLE"
            ) from fetch_error
        if first is None:
            raise RuntimeError("TEMPORAL_EXISTING_WORKFLOW_HISTORY_UNREADABLE")
        if getattr(first, "event_id", None) != 1:
            raise RuntimeError("TEMPORAL_EXISTING_WORKFLOW_HISTORY_UNREADABLE")
        if getattr(first, "event_type", None) != surface.event_started:
            raise RuntimeError("TEMPORAL_EXISTING_WORKFLOW_HISTORY_UNREADABLE")
        started_attributes = getattr(
            first, "workflow_execution_started_event_attributes", None
        )
        original_run = str(
            getattr(started_attributes, "original_execution_run_id", "") or ""
        )
        if not original_run:
            raise RuntimeError("TEMPORAL_EXISTING_WORKFLOW_HISTORY_UNREADABLE")
        if original_run != pinned:
            raise RuntimeError("TEMPORAL_EXISTING_WORKFLOW_IDENTITY_MISMATCH")
        payloads = getattr(
            getattr(started_attributes, "input", None), "payloads", None
        )
        if payloads is None or len(list(payloads)) != 1:
            raise RuntimeError("TEMPORAL_EXISTING_WORKFLOW_HISTORY_UNREADABLE")
        converter = getattr(client, "data_converter", None)
        decode = getattr(converter, "decode", None)
        if not callable(decode):
            raise RuntimeError("TEMPORAL_SDK_SURFACE_UNSUPPORTED")
        try:
            decoded = decode(payloads)
            if asyncio.iscoroutine(decoded):
                decoded = await decoded
        except Exception as exc:
            raise RuntimeError(
                "TEMPORAL_EXISTING_WORKFLOW_HISTORY_UNREADABLE"
            ) from exc
        if not isinstance(decoded, list) or len(decoded) != 1:
            raise RuntimeError("TEMPORAL_EXISTING_WORKFLOW_HISTORY_UNREADABLE")
        try:
            original = ObjectiveCycleWorkflowInput.model_validate(decoded[0])
        except Exception as exc:
            raise RuntimeError(
                "TEMPORAL_EXISTING_WORKFLOW_HISTORY_UNREADABLE"
            ) from exc
        if original.model_dump(mode="json") != expected:
            raise RuntimeError("TEMPORAL_EXISTING_WORKFLOW_INPUT_CONFLICT")
        status = getattr(description, "status", None)
        if status == surface.status_running:
            return pinned
        if status in surface.status_terminal:
            raise RuntimeError("TEMPORAL_WORKFLOW_ALREADY_TERMINAL")
        raise RuntimeError("TEMPORAL_EXISTING_WORKFLOW_STATUS_AMBIGUOUS")

    async def canonical_start(self, *, raw: dict[str, Any]) -> dict[str, Any]:
        """Start the single canonical workflow on the execution queue.

        Entry point for ``canonical_start -> aix.execution.current ->
        AixObjectiveCycleWorkflow``. The payload is validated against
        :class:`ObjectiveCycleWorkflowInput` before anything is started, so
        malformed starts fail closed here and never reach Temporal.

        The start is explicit ``REJECT_DUPLICATE`` with conflict ``FAIL``:
        one selection owns one workflow/run. A typed already-started
        collision recovers only the exact original workflow (verified
        ID/type/queue/run plus first-history input, exact-compared
        field by field); anything else refuses. There is no TypeError
        positional fallback: an SDK that rejects the safety arguments
        fails closed instead of starting unsafely.
        """
        workflow_input = ObjectiveCycleWorkflowInput.model_validate(raw)
        sdk = self._sdk_loader()
        if sdk is None:
            raise RuntimeError("TEMPORAL_SDK_NOT_INSTALLED")
        surface = _require_starter_surface()
        client = await sdk.client_cls.connect(
            str(getattr(self._settings, "TEMPORAL_ADDRESS", "127.0.0.1:7233")),
            namespace=self._binding.namespace,
        )
        workflow_id = objective_cycle_workflow_id(
            tenant_id=workflow_input.tenant_id,
            objective_id=workflow_input.objective_id,
            cycle_id=workflow_input.cycle_id,
        )
        self._logger.info(
            "temporal.workflow_start_requested",
            workflow=_WORKFLOW_NAME,
            workflow_id=workflow_id,
            queue=self._binding.execution_queue,
            tenant_id=workflow_input.tenant_id,
            objective_id=workflow_input.objective_id,
            cycle_id=workflow_input.cycle_id,
        )
        normalized = workflow_input.model_dump(mode="json")
        try:
            handle = await client.start_workflow(
                _WORKFLOW_NAME,
                workflow_input.model_dump(),
                id=workflow_id,
                task_queue=self._binding.execution_queue,
                id_reuse_policy=surface.reuse_reject_duplicate,
                id_conflict_policy=surface.conflict_fail,
            )
        except surface.already_started as exc:
            collision_workflow = str(getattr(exc, "workflow_id", "") or "")
            if collision_workflow and collision_workflow != workflow_id:
                raise RuntimeError(
                    "TEMPORAL_EXISTING_WORKFLOW_IDENTITY_MISMATCH"
                ) from exc
            run_id = await self._recover_exact_workflow(
                client,
                surface,
                workflow_id=workflow_id,
                expected=normalized,
                pinned_run_id=str(getattr(exc, "run_id", "") or "") or None,
            )
            recovered = True
        else:
            run_id = str(
                getattr(handle, "result_run_id", "")
                or getattr(handle, "run_id", "")
                or ""
            )
            if not run_id:
                raise RuntimeError("TEMPORAL_START_RECEIPT_AMBIGUOUS")
            recovered = False
        result = {
            "workflow": _WORKFLOW_NAME,
            "workflow_id": workflow_id,
            "run_id": run_id,
            "task_queue": self._binding.execution_queue,
            "namespace": self._binding.namespace,
            "already_started": recovered,
        }
        self._logger.info("temporal.workflow_started", **result)
        return result
