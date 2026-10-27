from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from backend.hyeve_aix_service.config import settings
from backend.services.aix_enveloped_run_service import DEFAULT_PROFILES_DIR, execute_enveloped_run
from backend.legacy.orphaned_services.aix_writeback_service import writeback_run_result
from backend.services.devexecutor_compact_carrier_service import (
    CompactCarrierTranslationError,
    CompactCarrierValidationError,
    resolve_compact_or_steps,
)
from backend.services.devexecutor_planner_bridge_service import PlannerBridgeError, plan_steps_via_llm
from backend.shared.request_header_utils import build_service_headers

LOGGER = logging.getLogger("hyeve_aix_service.runtime_pool")


def _parse_problem_code(response: httpx.Response) -> str | None:
    try:
        payload = response.json()
        detail = payload.get("detail")
        if isinstance(detail, dict):
            code = detail.get("code")
            if isinstance(code, str) and code.strip():
                return code.strip().lower()
    except Exception:
        return None
    return None


class OpsBusLeaseClient:
    def __init__(self, *, base_url: str, token: str, tenant_id: str = "default", timeout_seconds: float = 10.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._tenant_id = tenant_id
        self._client = httpx.Client(timeout=timeout_seconds)
        self._headers = build_service_headers(
            service_token=token,
            tenant_id=tenant_id,
            require_auth=True,
            require_tenant=True,
        )

    def close(self) -> None:
        self._client.close()

    def heartbeat_agent(self, *, agent_id: str, agent_type: str, service_role: str, capabilities: list[str]) -> None:
        response = self._client.post(
            f"{self._base_url}/api/v1/agents/heartbeat",
            headers=self._headers,
            json={
                "agent_id": agent_id,
                "agent_type": agent_type,
                "service_role": service_role,
                "capabilities_json": capabilities,
            },
        )
        response.raise_for_status()

    def claim_next_run_lease(
        self,
        *,
        project_id: str,
        lease_seconds: int,
        source_type: str | None,
    ) -> dict[str, Any] | None:
        response = self._client.post(
            f"{self._base_url}/api/v1/runs/lease/claim",
            headers={**self._headers, "X-Project-ID": project_id},
            json={
                "project_id": project_id,
                "lease_seconds": lease_seconds,
                "source_type": source_type,
            },
        )
        if response.status_code == 404 and _parse_problem_code(response) == "run_claim_empty":
            return None
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data")
        if not isinstance(data, dict):
            raise RuntimeError("Invalid claim response payload")
        return data

    def renew_run_lease(self, *, project_id: str, run_id: str, lease_seconds: int) -> None:
        response = self._client.post(
            f"{self._base_url}/api/v1/runs/{run_id}/lease/renew",
            headers={**self._headers, "X-Project-ID": project_id},
            json={"project_id": project_id, "lease_seconds": lease_seconds},
        )
        response.raise_for_status()

    def next_step_number(self, *, project_id: str, run_id: str) -> int:
        response = self._client.get(
            f"{self._base_url}/api/v1/runs/{run_id}/steps",
            headers={**self._headers, "X-Project-ID": project_id},
            params={"project_id": project_id, "limit": 1000, "offset": 0},
        )
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data")
        if not isinstance(data, dict):
            return 1
        items = data.get("items")
        if not isinstance(items, list):
            return 1
        return len(items) + 1

    def append_run_step(
        self,
        *,
        project_id: str,
        run_id: str,
        step_number: int,
        status_value: str,
        message: str,
        details: dict[str, Any],
    ) -> None:
        response = self._client.post(
            f"{self._base_url}/api/v1/runs/{run_id}/steps",
            headers={**self._headers, "X-Project-ID": project_id},
            json={
                "project_id": project_id,
                "schema_version": "step_current",
                "step_number": int(step_number),
                "step_type": "agent_execution",
                "status": status_value,
                "message": message,
                "details": details,
            },
        )
        response.raise_for_status()


ExecutionCallback = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass(slots=True)
class RuntimePoolConfig:
    project_id: str
    max_workers: int
    source_type: str | None
    lease_seconds: int
    poll_seconds: float
    agent_type: str
    service_role: str
    capabilities: list[str]


class RuntimePoolManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._threads: list[threading.Thread] = []
        self._running = False
        self._config: RuntimePoolConfig | None = None
        self._processed_runs = 0
        self._failed_runs = 0

    def status(self) -> dict[str, Any]:
        with self._lock:
            config = self._config
            return {
                "running": self._running,
                "worker_count": len(self._threads),
                "project_id": config.project_id if config else None,
                "source_type": config.source_type if config else None,
                "lease_seconds": config.lease_seconds if config else None,
                "poll_seconds": config.poll_seconds if config else None,
                "processed_runs": self._processed_runs,
                "failed_runs": self._failed_runs,
            }

    def start(
        self,
        *,
        project_id: str,
        max_workers: int,
        source_type: str | None,
        lease_seconds: int,
        poll_seconds: float,
        agent_type: str,
        service_role: str,
        capabilities: list[str],
        lease_client: OpsBusLeaseClient,
        executor: ExecutionCallback,
    ) -> dict[str, Any]:
        with self._lock:
            if self._running:
                return self.status()
            self._running = True
            self._stop_event.clear()
            self._threads = []
            self._processed_runs = 0
            self._failed_runs = 0
            self._config = RuntimePoolConfig(
                project_id=project_id,
                max_workers=max_workers,
                source_type=source_type,
                lease_seconds=lease_seconds,
                poll_seconds=poll_seconds,
                agent_type=agent_type,
                service_role=service_role,
                capabilities=capabilities,
            )
            for index in range(max_workers):
                worker_name = f"{agent_type}-{index + 1}"
                thread = threading.Thread(
                    target=self._worker_loop,
                    kwargs={
                        "worker_name": worker_name,
                        "lease_client": lease_client,
                        "executor": executor,
                    },
                    daemon=True,
                    name=f"runtime-pool-{worker_name}",
                )
                thread.start()
                self._threads.append(thread)
        return self.status()

    def stop(self, *, timeout_seconds: float = 5.0) -> dict[str, Any]:
        with self._lock:
            if not self._running:
                return self.status()
            self._stop_event.set()
            threads = list(self._threads)
        for thread in threads:
            thread.join(timeout=timeout_seconds)
        with self._lock:
            self._threads = []
            self._running = False
            self._config = None
        return self.status()

    def _worker_loop(self, *, worker_name: str, lease_client: OpsBusLeaseClient, executor: ExecutionCallback) -> None:
        while not self._stop_event.is_set():
            config = self._config
            if config is None:
                time.sleep(0.2)
                continue
            try:
                lease_client.heartbeat_agent(
                    agent_id=worker_name,
                    agent_type=config.agent_type,
                    service_role=config.service_role,
                    capabilities=config.capabilities,
                )
            except Exception as exc:
                LOGGER.warning("runtime_pool_heartbeat_failed", extra={"worker": worker_name, "error": str(exc)})
                time.sleep(config.poll_seconds)
                continue

            claimed = None
            try:
                claimed = lease_client.claim_next_run_lease(
                    project_id=config.project_id,
                    lease_seconds=config.lease_seconds,
                    source_type=config.source_type,
                )
            except Exception as exc:
                LOGGER.warning("runtime_pool_claim_failed", extra={"worker": worker_name, "error": str(exc)})
                time.sleep(config.poll_seconds)
                continue

            if not claimed:
                time.sleep(config.poll_seconds)
                continue

            self._execute_claimed_run(
                worker_name=worker_name,
                config=config,
                lease_client=lease_client,
                claimed=claimed,
                executor=executor,
            )

    def _execute_claimed_run(
        self,
        *,
        worker_name: str,
        config: RuntimePoolConfig,
        lease_client: OpsBusLeaseClient,
        claimed: dict[str, Any],
        executor: ExecutionCallback,
    ) -> None:
        run_id = str(claimed.get("id") or "")
        if not run_id:
            return

        keepalive_stop = threading.Event()

        def _keepalive() -> None:
            interval = max(1.0, min(float(config.lease_seconds) / 2.0, 30.0))
            while not keepalive_stop.wait(interval):
                try:
                    lease_client.renew_run_lease(
                        project_id=config.project_id,
                        run_id=run_id,
                        lease_seconds=config.lease_seconds,
                    )
                    lease_client.heartbeat_agent(
                        agent_id=worker_name,
                        agent_type=config.agent_type,
                        service_role=config.service_role,
                        capabilities=config.capabilities,
                    )
                except Exception as exc:
                    LOGGER.warning("runtime_pool_renew_failed", extra={"run_id": run_id, "error": str(exc)})
                    break

        renewer = threading.Thread(target=_keepalive, daemon=True, name=f"runtime-renew-{run_id}")
        renewer.start()

        result: dict[str, Any]
        try:
            result = executor(claimed)
        except Exception as exc:
            result = {"ok": False, "summary": f"executor error: {exc}", "error": str(exc)}
        finally:
            keepalive_stop.set()
            renewer.join(timeout=2.0)

        ok = bool(result.get("ok"))
        status_value = "completed" if ok else "failed"
        message = str(result.get("summary") or ("execution completed" if ok else "execution failed"))

        try:
            step_number = lease_client.next_step_number(project_id=config.project_id, run_id=run_id)
            lease_client.append_run_step(
                project_id=config.project_id,
                run_id=run_id,
                step_number=step_number,
                status_value=status_value,
                message=message,
                details={
                    "worker": worker_name,
                    "executor_result": result,
                },
            )
        except Exception as exc:
            LOGGER.warning("runtime_pool_append_step_failed", extra={"run_id": run_id, "error": str(exc)})
            ok = False

        with self._lock:
            self._processed_runs += 1
            if not ok:
                self._failed_runs += 1


runtime_pool_manager = RuntimePoolManager()


def _coerce_task_number(source_id: str | None) -> int:
    raw = str(source_id or "").strip()
    if raw.isdigit():
        return max(1, int(raw))
    return 1


def _extract_devexecutor_payload(claimed_run: dict[str, Any]) -> dict[str, Any]:
    payload = claimed_run.get("dispatch_payload")
    if not isinstance(payload, dict):
        return {}
    inner = payload.get("devexecutor")
    if isinstance(inner, dict):
        return inner
    return payload


def _resolve_canonical_planner_packet(
    *, planner_payload: dict[str, Any] | None, metadata: dict[str, Any] | None
) -> dict[str, Any] | None:
    planner_packet = planner_payload.get("planner_packet") if isinstance(planner_payload, dict) else None
    if isinstance(planner_packet, dict) and planner_packet:
        return planner_packet
    metadata_packet = metadata.get("planner_packet") if isinstance(metadata, dict) else None
    if isinstance(metadata_packet, dict) and metadata_packet:
        return metadata_packet
    return None


def default_claim_executor(claimed_run: dict[str, Any]) -> dict[str, Any]:
    run_id = str(claimed_run.get("id") or "")
    project_id = str(claimed_run.get("project_id") or "")
    correlation_id = str(claimed_run.get("correlation_id") or f"runtime-{run_id}")
    source_id = str(claimed_run.get("source_id") or "")
    payload = _extract_devexecutor_payload(claimed_run)

    workspace_root = str(payload.get("workspace_root") or settings.RUNTIME_POOL_DEFAULT_WORKSPACE_ROOT)
    workspace = Path(workspace_root)
    if not workspace.exists():
        return {
            "ok": False,
            "summary": f"workspace_root does not exist: {workspace_root}",
            "workspace_root": workspace_root,
        }

    steps = payload.get("steps")
    if not isinstance(steps, list):
        steps = []
    compact_carrier = payload.get("compact_carrier")
    planner_payload = payload.get("planner") if isinstance(payload.get("planner"), dict) else None
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else None
    try:
        resolved_steps, translator_diag = resolve_compact_or_steps(
            steps=steps,
            compact_carrier=compact_carrier if isinstance(compact_carrier, dict) else None,
        )
    except (CompactCarrierValidationError, CompactCarrierTranslationError) as exc:
        return {"ok": False, "summary": f"compact carrier translation failed: {exc}", "error": str(exc)}
    planner_diag: dict[str, Any] = {"planner_used": False}
    if not resolved_steps and planner_payload:
        planner_packet = _resolve_canonical_planner_packet(planner_payload=planner_payload, metadata=metadata)
        if not planner_packet:
            return {
                "ok": False,
                "summary": "planner execution requires canonical planner_packet in planner payload or metadata",
                "error": "PLANNER_PACKET_REQUIRED",
                "planner": {"planner_used": True, "error": "PLANNER_PACKET_REQUIRED"},
            }
        try:
            resolved_steps, planner_diag = plan_steps_via_llm(
                tenant_id="default",
                project_id=project_id or "unknown",
                correlation_id=correlation_id,
                workspace_root=str(workspace),
                prompt=str(planner_payload.get("prompt") or ""),
                planner_packet=planner_packet,
                planner_mode=str((dict(planner_packet or {})).get("planner_mode") or "generic"),
                provider=str(planner_payload.get("provider") or "slavx"),
                model=(
                    str(planner_payload.get("model"))
                    if planner_payload.get("model") is not None and str(planner_payload.get("model")).strip()
                    else None
                ),
                temperature=(
                    float(planner_payload.get("temperature"))
                    if planner_payload.get("temperature") is not None
                    else None
                ),
                max_steps=(
                    int(planner_payload.get("max_steps")) if planner_payload.get("max_steps") is not None else None
                ),
                authorization=(
                    f"Bearer {settings.RUNTIME_PLANNER_AUTH_TOKEN or settings.OPSBUS_API_TOKEN}"
                    if (settings.RUNTIME_PLANNER_AUTH_TOKEN or settings.OPSBUS_API_TOKEN)
                    and not str(settings.RUNTIME_PLANNER_AUTH_TOKEN or settings.OPSBUS_API_TOKEN)
                    .lower()
                    .startswith("bearer ")
                    else (settings.RUNTIME_PLANNER_AUTH_TOKEN or settings.OPSBUS_API_TOKEN or None)
                ),
            )
        except (ValueError, PlannerBridgeError) as exc:
            return {
                "ok": False,
                "summary": f"planner bridge failed: {exc}",
                "error": str(exc),
                "translator": translator_diag,
                "planner": {"planner_used": True, "error": str(exc)},
            }
    if not resolved_steps:
        return {
            "ok": False,
            "summary": "execution plan is empty",
            "error": "EMPTY_EXECUTION_PLAN",
            "translator": translator_diag,
            "planner": planner_diag,
        }
    task_number = _coerce_task_number(source_id)
    omp_payload = {
        "envelope": {
            "specversion": "1.0",
            "id": f"evt-runtime-pool-{uuid4().hex[:24]}",
            "type": "com.aix.task.state.queued",
            "source": "aix.runtime_pool",
            "time": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "datacontenttype": "application/json",
            "trace_id": correlation_id,
            "data": {
                "packet": {
                    "schema_name": "aix_execution_packet_current",
                    "schema_version": "current",
                    "packet_id": f"pkt-{uuid4().hex[:16]}",
                    "correlation_id": correlation_id,
                    "causation_id": run_id or f"run-{uuid4().hex[:12]}",
                    "tenant_id": "default",
                    "project_id": project_id or "unknown",
                    "idempotency_key": run_id or None,
                    "sender": {
                        "agent_id": "aix-runtime-pool",
                        "role": "executor",
                        "provider": "internal",
                        "model": "runtime-pool",
                    },
                    "target": {"agent_id": "devexecutor-runtime", "role": "executor"},
                    "step": {
                        "index": task_number,
                        "status": "queued",
                        "summary": "queued from runtime-pool claim",
                        "evidence_refs": [],
                    },
                },
                "execution": {
                    "task_id": source_id or None,
                    "task_number": task_number,
                    "workspace_root": str(workspace),
                    "repo_key": str(payload.get("repo_key") or "hyeve-aix-service"),
                    "explicit_profile_id": payload.get("explicit_profile_id"),
                    "steps": resolved_steps,
                    "compact_carrier": compact_carrier if isinstance(compact_carrier, dict) else None,
                    "max_runtime_seconds": float(
                        payload.get("max_runtime_seconds") or settings.RUNTIME_POOL_MAX_RUNTIME_SECONDS
                    ),
                    "max_patch_attempts": int(
                        payload.get("max_patch_attempts") or settings.RUNTIME_POOL_MAX_PATCH_ATTEMPTS
                    ),
                    "idempotency_key": run_id or None,
                    "profiles_dir": DEFAULT_PROFILES_DIR,
                },
            },
        },
    }
    result = execute_enveloped_run(
        omp_payload,
        ingress="runtime_pool.opsbus_claim",
        auth_ctx={"source": "runtime_pool"},
    )
    writeback = asyncio.run(
        writeback_run_result(
            result,
            tenant_id="default",
            actor_id=str(settings.RUNTIME_POOL_AGENT_TYPE or "aix-runtime-pool"),
        )
    )
    ok = result.status == "completed" and result.error is None
    summary = f"devexecutor {result.status}"
    if result.error is not None:
        summary = f"{summary}: {result.error.code}"
    return {
        "ok": ok,
        "summary": summary,
        "devexecutor_run_id": result.run_id,
        "transcript_count": int(result.evidence.get("transcript_count") or 0),
        "translator": translator_diag,
        "planner": planner_diag,
        "schema_id": result.schema_id,
        "status": result.status,
        "error": (result.error.to_dict() if result.error is not None else None),
        "writeback": writeback.to_dict(),
        "telemetry": result.telemetry,
        "evidence_refs": list(result.evidence_refs),
        "evidence": result.evidence,
        "replayed": bool(result.replayed),
    }
