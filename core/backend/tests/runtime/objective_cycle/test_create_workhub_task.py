"""001P-W12: AIX creates WorkHub parent tasks on admit (governed local exception)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from backend.aix_core.runtime.executor.real_execute import load_workhub_task, run_real_execute
from backend.aix_core.runtime.objective.durable_api_backend import DurableObjectiveCycleApiBackend


class _FakeClient:
    def __init__(self, *, task_id: str = "wh-created-1", task_number: int = 4242) -> None:
        self.calls: list[dict[str, Any]] = []
        self.task_id = task_id
        self.task_number = task_number

    async def create_task(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return {
            "id": self.task_id,
            "task_number": self.task_number,
            "title": kwargs.get("title"),
            "status": "queued",
        }


@pytest.mark.asyncio
async def test_admit_creates_workhub_task_when_exception_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient()

    def _build(_settings: object | None = None, **_kwargs: Any) -> _FakeClient:
        return fake

    monkeypatch.setattr(
        "backend.aix_core.runtime.objective.durable_api_backend.build_workhub_client",
        _build,
    )
    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess",
        create_workhub_tasks=True,
        real_execute_enabled=False,
        require_workhub_load=False,
    )
    admit = await backend.command(
        operation="admit_objective",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=None,
        body={
            "workhub_task_id": "auto",
            "objective": "Build a registration form",
            "task_class": "bounded_repair",
            "definition_of_done": ("form validates",),
            "repository_id": "aix-frontend",
            "allowed_files": (),
            "project_id": "bh-migrated-7-aix",
            "create_workhub_task": True,
        },
        idempotency_key="admit-create-1",
        expected_version=None,
    )
    assert admit["ok"] is True
    assert admit["workhub_task_created"] is True
    assert admit["aix_creates_workhub_tasks"] is True
    assert admit["workhub_task_id"] == "wh-created-1"
    assert admit["task_number"] == 4242
    assert admit["workhub_create_idempotency_key"] == "aix-admit-create:admit-create-1"
    assert admit["workhub_lifecycle_authority"] is False
    assert admit["production_go"] is False
    assert admit["exception"] == "001P-W12"
    assert len(fake.calls) == 1
    assert fake.calls[0]["project_id"] == "bh-migrated-7-aix"
    assert fake.calls[0]["tenant_id"] == "tenant-1"
    assert fake.calls[0]["idempotency_key"] == "aix-admit-create:admit-create-1"
    assert fake.calls[0]["metadata_json"]["exception"] == "001P-W12"
    assert fake.calls[0]["metadata_json"]["tenant_id"] == "tenant-1"


@pytest.mark.asyncio
async def test_admit_does_not_overwrite_real_task_id_when_create_requested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeClient()
    monkeypatch.setattr(
        "backend.aix_core.runtime.objective.durable_api_backend.build_workhub_client",
        lambda *_a, **_k: fake,
    )
    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess",
        create_workhub_tasks=True,
        real_execute_enabled=False,
        require_workhub_load=False,
    )
    admit = await backend.command(
        operation="admit_objective",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=None,
        body={
            "workhub_task_id": "wh-existing-real",
            "objective": "Use existing parent task",
            "task_class": "bounded_repair",
            "definition_of_done": ("done",),
            "repository_id": "aix",
            "allowed_files": (),
            "project_id": "proj-1",
            "create_workhub_task": True,
        },
        idempotency_key="admit-keep-real",
        expected_version=None,
    )
    assert admit["ok"] is True
    assert admit["workhub_task_id"] == "wh-existing-real"
    assert admit["workhub_task_created"] is False
    assert fake.calls == []


@pytest.mark.asyncio
async def test_admit_retries_env_token_after_caller_403(monkeypatch: pytest.MonkeyPatch) -> None:
    class _AuthAwareClient:
        def __init__(self, *, accept: bool) -> None:
            self.accept = accept
            self.calls = 0

        async def create_task(self, **kwargs: Any) -> dict[str, Any]:
            self.calls += 1
            if not self.accept:
                request = httpx.Request("POST", "http://workhub/api/v1/tasks")
                response = httpx.Response(403, request=request, text="Insufficient role")
                raise httpx.HTTPStatusError("403 Forbidden", request=request, response=response)
            return {"id": "wh-service-1", "task_number": 7, "title": kwargs.get("title")}

    clients: list[_AuthAwareClient] = []

    def _build(_settings: object | None = None, *, bearer_authorization: str | None = None) -> _AuthAwareClient:
        client = _AuthAwareClient(accept=bearer_authorization is None)
        clients.append(client)
        return client

    monkeypatch.setattr(
        "backend.aix_core.runtime.objective.durable_api_backend.build_workhub_client",
        _build,
    )
    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess",
        create_workhub_tasks=True,
        real_execute_enabled=False,
        require_workhub_load=False,
    )
    admit = await backend.command(
        operation="admit_objective",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=None,
        body={
            "workhub_task_id": "auto",
            "objective": "Build a registration form",
            "task_class": "bounded_repair",
            "definition_of_done": ("form validates",),
            "repository_id": "aix-frontend",
            "allowed_files": (),
            "project_id": "bh-migrated-7-aix",
            "create_workhub_task": True,
            "caller_authorization": "Bearer user-no-manager",
        },
        idempotency_key="admit-create-fallback",
        expected_version=None,
    )
    assert admit["ok"] is True
    assert admit["workhub_task_id"] == "wh-service-1"
    assert len(clients) == 2
    assert clients[0].calls == 1
    assert clients[1].calls == 1


@pytest.mark.asyncio
async def test_admit_does_not_create_when_flag_off() -> None:
    backend = DurableObjectiveCycleApiBackend(mode="inprocess", create_workhub_tasks=False)
    admit = await backend.command(
        operation="admit_objective",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=None,
        body={
            "workhub_task_id": "wh-existing",
            "objective": "Existing only",
            "task_class": "bounded_repair",
            "definition_of_done": ("done",),
            "repository_id": "aix",
            "allowed_files": (),
            "project_id": "proj",
            "create_workhub_task": True,
        },
        idempotency_key="admit-nocreate",
        expected_version=None,
    )
    assert admit["ok"] is True
    assert admit["workhub_task_created"] is False
    assert admit["aix_creates_workhub_tasks"] is False
    assert admit["workhub_task_id"] == "wh-existing"


@pytest.mark.asyncio
@pytest.mark.parametrize("marker", ["auto", "create", "new", "AUTO"])
async def test_admit_rejects_unresolved_placeholder_when_create_disabled(marker: str) -> None:
    """Create markers must not leak into admitted objectives when create is off."""
    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess",
        create_workhub_tasks=False,
        real_execute_enabled=True,
        require_workhub_load=True,
    )
    admit = await backend.command(
        operation="admit_objective",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=None,
        body={
            "workhub_task_id": marker,
            "objective": "Build ERP under proposed/erp-system/",
            "task_class": "bounded_repair",
            "definition_of_done": ("done",),
            "repository_id": "aix",
            "allowed_files": (),
            "project_id": "01KYCPDEPJ9STR36FB8P8DT31F",
            "create_workhub_task": True,
        },
        idempotency_key=f"admit-placeholder-{marker}",
        expected_version=None,
    )
    assert admit["ok"] is False
    assert admit["error"]["code"] == "WORKHUB_TASK_ID_UNRESOLVED"
    assert admit["aix_creates_workhub_tasks"] is False
    assert admit["workhub_lifecycle_authority"] is False


@pytest.mark.asyncio
async def test_admit_rejects_placeholder_returned_by_workhub_create(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _BadIdClient:
        async def create_task(self, **kwargs: Any) -> dict[str, Any]:
            return {"id": "auto", "task_number": 1, "title": kwargs.get("title")}

    monkeypatch.setattr(
        "backend.aix_core.runtime.objective.durable_api_backend.build_workhub_client",
        lambda *_a, **_k: _BadIdClient(),
    )
    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess",
        create_workhub_tasks=True,
        real_execute_enabled=False,
        require_workhub_load=False,
    )
    admit = await backend.command(
        operation="admit_objective",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=None,
        body={
            "workhub_task_id": "auto",
            "objective": "Create task",
            "task_class": "bounded_repair",
            "definition_of_done": ("done",),
            "repository_id": "aix",
            "allowed_files": (),
            "project_id": "proj-1",
            "create_workhub_task": True,
        },
        idempotency_key="admit-create-bad-id",
        expected_version=None,
    )
    assert admit["ok"] is False
    assert admit["error"]["code"] == "WORKHUB_TASK_ID_UNRESOLVED"


@pytest.mark.asyncio
async def test_admit_duplicate_create_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(task_id="wh-idem-1", task_number=99)
    monkeypatch.setattr(
        "backend.aix_core.runtime.objective.durable_api_backend.build_workhub_client",
        lambda *_a, **_k: fake,
    )
    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess",
        create_workhub_tasks=True,
        real_execute_enabled=False,
        require_workhub_load=False,
    )
    body = {
        "workhub_task_id": "auto",
        "objective": "Idempotent create",
        "task_class": "bounded_repair",
        "definition_of_done": ("done",),
        "repository_id": "aix",
        "allowed_files": (),
        "project_id": "proj-1",
        "create_workhub_task": True,
    }
    first = await backend.command(
        operation="admit_objective",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=None,
        body=body,
        idempotency_key="admit-dup-1",
        expected_version=None,
    )
    second = await backend.command(
        operation="admit_objective",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=None,
        body=body,
        idempotency_key="admit-dup-1",
        expected_version=None,
    )
    assert first["ok"] is True
    assert second["ok"] is True
    assert first["objective_id"] == second["objective_id"]
    assert first["workhub_task_id"] == second["workhub_task_id"] == "wh-idem-1"
    assert len(fake.calls) == 1


@pytest.mark.asyncio
async def test_admit_rejects_tenant_override_mismatch() -> None:
    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess",
        create_workhub_tasks=True,
        real_execute_enabled=False,
        require_workhub_load=False,
    )
    admit = await backend.command(
        operation="admit_objective",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=None,
        body={
            "tenant_id": "tenant-other",
            "workhub_task_id": "auto",
            "objective": "Cross tenant attempt",
            "task_class": "bounded_repair",
            "definition_of_done": ("done",),
            "repository_id": "aix",
            "allowed_files": (),
            "project_id": "proj-1",
            "create_workhub_task": True,
        },
        idempotency_key="admit-tenant-mismatch",
        expected_version=None,
    )
    assert admit["ok"] is False
    assert admit["error"]["code"] == "TENANT_OVERRIDE_FORBIDDEN"


@pytest.mark.asyncio
async def test_admit_surfaces_workhub_create_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FailClient:
        async def create_task(self, **kwargs: Any) -> dict[str, Any]:
            request = httpx.Request("POST", "http://workhub/api/v1/tasks")
            response = httpx.Response(500, request=request, text="boom")
            raise httpx.HTTPStatusError("500 Server Error", request=request, response=response)

    monkeypatch.setattr(
        "backend.aix_core.runtime.objective.durable_api_backend.build_workhub_client",
        lambda *_a, **_k: _FailClient(),
    )
    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess",
        create_workhub_tasks=True,
        real_execute_enabled=False,
        require_workhub_load=False,
    )
    admit = await backend.command(
        operation="admit_objective",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=None,
        body={
            "workhub_task_id": "auto",
            "objective": "Create fails",
            "task_class": "bounded_repair",
            "definition_of_done": ("done",),
            "repository_id": "aix",
            "allowed_files": (),
            "project_id": "proj-1",
            "create_workhub_task": True,
        },
        idempotency_key="admit-create-fail",
        expected_version=None,
    )
    assert admit["ok"] is False
    assert admit["error"]["code"] == "WORKHUB_TASK_CREATE_FAILED"
    assert admit["exception"] == "001P-W12"
    assert admit["workhub_lifecycle_authority"] is False
    assert admit["production_go"] is False


@pytest.mark.asyncio
async def test_created_task_id_propagates_admit_to_contract_load(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Created parent id is what contract-load sees — never auto."""
    fake = _FakeClient(task_id="wh-prop-42", task_number=42)
    monkeypatch.setattr(
        "backend.aix_core.runtime.objective.durable_api_backend.build_workhub_client",
        lambda *_a, **_k: fake,
    )
    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess",
        create_workhub_tasks=True,
        real_execute_enabled=True,
        require_workhub_load=True,
    )
    admit = await backend.command(
        operation="admit_objective",
        tenant_id="tenant-1",
        actor_id="human-1",
        resource_id=None,
        body={
            "workhub_task_id": "auto",
            "objective": "Propagate WorkHub parent id",
            "task_class": "bounded_repair",
            "definition_of_done": ("done",),
            "repository_id": "aix",
            "allowed_files": (),
            "project_id": "proj-prop",
            "create_workhub_task": True,
        },
        idempotency_key="admit-prop-1",
        expected_version=None,
    )
    assert admit["ok"] is True
    assert admit["workhub_task_id"] == "wh-prop-42"
    obj = backend._objectives[admit["objective_id"]]  # noqa: SLF001
    assert obj.workhub_task_id == "wh-prop-42"

    load_calls: list[str] = []

    class _LoadClient:
        async def get_task_by_number(self, **kwargs: Any) -> dict[str, Any] | None:
            raise AssertionError("task_number path should not run")

        async def get_aix_adapter_read_contract(
            self, **kwargs: Any
        ) -> tuple[bool, str | None, dict[str, Any] | None]:
            load_calls.append(str(kwargs.get("task_id")))
            return True, None, {"id": kwargs.get("task_id"), "contract": "ok"}

    task, err = await load_workhub_task(
        client=_LoadClient(),  # type: ignore[arg-type]
        project_id="proj-prop",
        workhub_task_id=obj.workhub_task_id,
        task_number=None,
        tenant_id="tenant-1",
    )
    assert err is None
    assert task is not None
    assert task["id"] == "wh-prop-42"
    assert load_calls == ["wh-prop-42"]

    # run_real_execute uses the same identity field the dispatcher passes from the objective.
    async def _fake_load(**kwargs: Any) -> tuple[dict[str, Any], None]:
        load_calls.append(f"exec:{kwargs.get('workhub_task_id')}")
        return {"id": kwargs.get("workhub_task_id")}, None

    class _ExecClient:
        service_token = "test-token"

    monkeypatch.setattr(
        "backend.aix_core.runtime.executor.real_execute.load_workhub_task",
        _fake_load,
    )
    monkeypatch.setattr(
        "backend.aix_core.runtime.executor.real_execute.build_workhub_client",
        lambda *_a, **_k: _ExecClient(),
    )
    result = await run_real_execute(
        tenant_id="tenant-1",
        cycle_id="cyc_prop_1",
        objective_id=admit["objective_id"],
        objective="Propagate WorkHub parent id",
        workhub_task_id=obj.workhub_task_id,
        project_id="proj-prop",
        task_number=None,
        repository_id="aix",
        require_workhub_load=True,
        workspace_base=tmp_path / "ws",
        demo_capability=True,
    )
    assert result.workhub_loaded is True
    assert "exec:wh-prop-42" in load_calls
    assert all("auto" not in c for c in load_calls)
