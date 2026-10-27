from __future__ import annotations

import asyncio
import os
from pathlib import Path

from backend.aix_core.runtime.objective.runtime_attach import (
    build_objective_cycle_api_backend,
)
from backend.hyeve_aix_service.config import reload_settings
from backend.infrastructure.temporal.runtime import TemporalRuntimeManager
from backend.aix_event_consumer.admission import DefaultAdmissionPolicy
from backend.aix_event_consumer.consumer import AixEventConsumer
from backend.aix_event_consumer.cursor import DurableCursor
from backend.aix_event_consumer.production import OpsBusHttpEventSource, TemporalClientAdapter
from backend.workflows.temporal.objective_cycle_activities import bind_backend_factory


async def _main() -> int:
    os.environ.setdefault("HYEVE_AIX_SERVICE_TEMPORAL_ENABLED", "true")
    os.environ.setdefault("HYEVE_AIX_SERVICE_TEMPORAL_AUTO_START_WORKER", "false")
    # PLAT-D2 single ownership: activities must observe the FINAL dispatch
    # result, not an async-accepted claim. The HTTP backend keeps AWAIT=0
    # (responsive edge); the worker executes synchronously inside the
    # activity's start-to-close/heartbeat budget so verify_step never races
    # an in-flight executor.
    os.environ.setdefault("HYEVE_AIX_SERVICE_OBJECTIVE_CYCLE_AWAIT_EXECUTION", "1")
    settings = reload_settings()
    # Single canonical executor: dispatch_step / verify_step activities run
    # through the same backend command seam the objective-cycle router uses.
    backend = await build_objective_cycle_api_backend(settings)

    async def _factory() -> object:
        if backend is None:
            raise RuntimeError("OBJECTIVE_CYCLE_BACKEND_NOT_CONFIGURED")
        return backend

    bind_backend_factory(_factory)
    manager = TemporalRuntimeManager(settings=settings)
    consumer_task = None
    if bool(getattr(settings, "AIX_EVENT_CONSUMER_ENABLED", False)):
        token = str(getattr(settings, "OPSBUS_API_TOKEN", "") or "")
        tenant_id = str(getattr(settings, "AIX_EVENT_CONSUMER_TENANT_ID", "") or "")
        source = OpsBusHttpEventSource(
            base_url=str(settings.OPSBUS_API_URL), token=token, tenant_id=tenant_id,
            stream_key=str(getattr(settings, "AIX_EVENT_CONSUMER_STREAM_KEY", "") or "") or None,
        )
        consumer = AixEventConsumer(
            event_source=source,
            admission=DefaultAdmissionPolicy(allowed_tenants=(tenant_id,)),
            cursor=DurableCursor(Path(str(getattr(settings, "AIX_EVENT_CONSUMER_CURSOR_PATH", "/var/lib/hyeve-aix/event-consumer.json")))),
            temporal=TemporalClientAdapter(manager=manager, loop=asyncio.get_running_loop()),
        )

        async def _consume_forever() -> None:
            interval = float(getattr(settings, "AIX_EVENT_CONSUMER_POLL_SECONDS", 2.0))
            while True:
                await asyncio.to_thread(consumer.run_once)
                await asyncio.sleep(interval)

        consumer_task = asyncio.create_task(_consume_forever())
    try:
        await manager.run_worker_forever()
    finally:
        if consumer_task is not None:
            consumer_task.cancel()
            await asyncio.gather(consumer_task, return_exceptions=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
