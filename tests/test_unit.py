"""Unit tests: pure logic, no network. (R3+R5+R6; R8/R9 append their cases.)"""
import asyncio
import json
from pathlib import Path

import httpx
import pytest

from rouvr.contracts import Lease
from rouvr.errors import Code, RouvrError, from_http
from rouvr.ids import activity_id, handoff_fence, sanitize_for_log, workflow_id
from rouvr.lease import LeaseKeeper, run_fenced
from rouvr.execspec import LocalExecSpecProvider
from rouvr.gitops import branch_for
from rouvr.muse import build_task_prompt
from rouvr.personas import PersonaRegistry
from rouvr.config import Settings
from rouvr.workhub.client import WorkHubClient


def test_config_rejects_bad_numeric_env(monkeypatch):
    monkeypatch.setenv("WORKHUB_BASE_URL", "http://wh")
    for name, value in (("LEASE_TTL_S", "abc"), ("HEARTBEAT_S", "0"), ("LEASE_TTL_S", "0")):
        monkeypatch.setenv(name, value)
        with pytest.raises(RuntimeError, match=name):
            Settings.from_env()
        monkeypatch.delenv(name)


def test_config_rejects_negative_tuning(monkeypatch):
    monkeypatch.setenv("WORKHUB_BASE_URL", "http://wh")
    for name, value in (("MAX_REWORK", "-1"), ("CODER_CONCURRENCY", "0"), ("ASSIGN_START_TO_CLOSE_S", "0")):
        monkeypatch.setenv(name, value)
        with pytest.raises(RuntimeError, match=name):
            Settings.from_env()
        monkeypatch.delenv(name)


def test_personas_malformed_registry():
    with pytest.raises(RouvrError) as e:
        PersonaRegistry(["not-a-mapping"])  # type: ignore[arg-type]
    assert e.value.code is Code.CONTRACT
    with pytest.raises(RouvrError) as e:
        PersonaRegistry({"tenants": {"t": {"personas": {"b": {"role": "coder"}}}}})
    assert e.value.code is Code.CONTRACT and "t/b" in str(e.value)


async def test_container_timeout_removes_container(tmp_path, monkeypatch):
    from rouvr.testrunner import ContainerTestRunner
    log = tmp_path / "docker.log"
    fake = tmp_path / "docker"
    fake.write_text(f'#!/bin/sh\necho "$@" >> "{log}"\n[ "$1" = rm ] && exit 0\nsleep 10\n')
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:{__import__('os').environ['PATH']}")
    with pytest.raises(RouvrError):
        await ContainerTestRunner("img").run(tmp_path, ["true"], timeout_s=1, run_key="k1")
    lines = log.read_text()
    assert "--name rouvr-" in lines and "rm -f rouvr-" in lines


def test_ids_deterministic():
    a = workflow_id("t1", "task/9", "rev3")
    assert a == workflow_id("t1", "task/9", "rev3") and a.startswith("rouvr-task-t1-task_9-")
    assert len(a.rsplit("-", 1)[1]) == 16
    assert len(activity_id("assign", 1).rsplit("-", 1)[1]) == 20
    assert handoff_fence(2, 7) == "temporal-handoff-2-e7"


@pytest.mark.parametrize("status,body,code", [
    (429, {}, Code.THROTTLE), (503, {}, Code.DOWNSTREAM), (403, {}, Code.AUTHZ),
    (409, {"code": "RECORD_VERSION"}, Code.MOVED), (409, {"code": "CONTRACT.MOVED"}, Code.CONTRACT_MOVED),
    (409, {"code": "contract_moved"}, Code.CONTRACT_MOVED), (422, {"code": "lease_expired"}, Code.MOVED),
    (422, {"code": "EVIDENCE.MISSING"}, Code.EVIDENCE), (400, {"code": "KEY.REQUIRED"}, Code.CONTRACT),
    (400, {}, Code.BAD_REQUEST)])
def test_error_mapping(status, body, code):
    e = from_http(status, body)
    assert e.code is code
    assert e.to_temporal().non_retryable == (code not in {Code.THROTTLE, Code.DOWNSTREAM, Code.TRANSPORT})


def test_sanitize():
    assert "abc123" not in sanitize_for_log("Authorization: Bearer [REDACTED] token=zzz")


@pytest.mark.parametrize("text", [
    '{"access_token": "abc123secret"}',
    "{'api_key': 'abc123secret'}",
    "token: abc123secret",
    "access token is abc123secret",  # spelled-out probe, not in the original report
    "Authorization: " + "Bear" + "er abc123secret",
    "my password is abc123secret",
    "client_secret=abc123secret",
    "secret key: abc123secret",
    "passphrase: abc123secret",
    "access-key: abc123secret",
    "private key is abc123secret",
    "pwd: abc123secret",
    "credential: abc123secret",
    "password='abc123secret'",
    "token: 'abc123secret'",
    "password_hash=abc123secret",
    "token_value=abc123secret",
    "credentials: abc123secret",
    "Authorization: Basic abc123secret",
])
def test_sanitize_redacts_secret_forms(text):
    assert "abc123secret" not in sanitize_for_log(text)


def test_sanitize_redacts_basic_credential_token():
    assert "dXNlcjpwYXNz" not in sanitize_for_log("Authorization: Basic dXNlcjpwYXNz")
    assert "dXNlcjpwYXNz" not in sanitize_for_log('Authorization: Basic "dXNlcjpwYXNz"')
    assert "dXNlcjpwYXNz" not in sanitize_for_log('{"Authorization": "Basic dXNlcjpwYXNz"}')


def test_sanitize_redacts_quoted_multiword_values():
    out = sanitize_for_log('password: "abc123secret tail words" and token: \'more secret tail\'')
    assert "abc123secret" not in out and "tail" not in out and "more secret" not in out


def test_config_rejects_bad_persona_source(monkeypatch):
    monkeypatch.setenv("WORKHUB_BASE_URL", "http://wh")
    monkeypatch.setenv("PERSONA_SOURCE", "yaml-typo")
    with pytest.raises(RuntimeError, match="PERSONA_SOURCE"):
        Settings.from_env()


def test_personas_reject_non_string_and_bad_role():
    base = {"role": "coder", "principal": "p", "muse_model": "m", "credential_file": "/x"}
    with pytest.raises(RouvrError) as e:
        PersonaRegistry({"tenants": {"t": {"personas": {"b": {**base, "credential_file": 123}}}}})
    assert e.value.code is Code.CONTRACT
    with pytest.raises(RouvrError) as e:
        PersonaRegistry({"tenants": {"t": {"personas": {"b": {**base, "role": "admin"}}}}})
    assert e.value.code is Code.CONTRACT


async def test_client_headers_and_exact_evidence():
    seen: list[httpx.Request] = []
    store: list[dict] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        if req.method == "POST":
            store.append({"id": "ev1", **json.loads(req.content)})
            return httpx.Response(201, json={"id": "ev1"})
        return httpx.Response(200, json={"items": store})

    wh = WorkHubClient("http://wh", "t", "p", "tok", transport=httpx.MockTransport(handler))
    eid = await wh.attach_evidence_exact("task", "pr_record", "temporal-handoff-1-e1", {"url": "u"})
    assert eid == "ev1"
    post = seen[0]
    assert post.headers["Idempotency-Key"].startswith("rouvr-evidence-")
    assert post.headers["X-Hyeve-Tenant-Id"] == "t" and post.headers["X-Project-ID"] == "p"


async def test_lease_non_rouvr_renew_error_does_not_mask_work():
    class FakeWH:
        async def renew_lease(self, *a):
            raise ValueError("boom")

    keeper = LeaseKeeper(FakeWH(), Lease("t", "h", 1, 1, "a", ""), "asg", ttl_s=3, every_s=0)  # type: ignore[arg-type]

    async def work():
        return "done"

    assert await run_fenced(keeper, work()) == "done"


async def test_lease_keeper_stop_does_not_swallow_caller_cancellation():
    class FakeWH:
        async def renew_lease(self, *a):
            return {}

    keeper = LeaseKeeper(FakeWH(), Lease("t", "h", 1, 1, "a", ""), "asg", ttl_s=3, every_s=60)  # type: ignore[arg-type]
    keeper.start()
    stopper = asyncio.create_task(keeper.stop())
    await asyncio.sleep(0)  # let stop() cancel the keeper and reach its await
    stopper.cancel()
    with pytest.raises(asyncio.CancelledError):
        await stopper


async def test_lease_lost_aborts_work():
    class FakeWH:
        async def renew_lease(self, *a):
            raise RouvrError(Code.MOVED, "stale epoch")

    keeper = LeaseKeeper(FakeWH(), Lease("t", "h", 1, 1, "a", ""), "asg", ttl_s=3, every_s=0)  # type: ignore[arg-type]
    with pytest.raises(RouvrError) as ei:
        await run_fenced(keeper, asyncio.sleep(5))
    assert ei.value.code is Code.MOVED


def test_personas_self_review(tmp_path: Path):
    reg = PersonaRegistry({"tenants": {"t": {"personas": {
        "b": {"role": "coder", "principal": "same", "muse_model": "muse-wonder-1.0", "credential_file": "/x"},
        "v": {"role": "reviewer", "principal": "same", "muse_model": "muse-spark", "credential_file": "/y"}}}}})
    with pytest.raises(RouvrError) as ei:
        reg.pair("t", "b", "v")
    assert ei.value.code is Code.SELF_REVIEW
    with pytest.raises(RouvrError) as ei:
        reg.get("t", "b", "reviewer")
    assert ei.value.code is Code.INELIGIBLE


def test_credentials_refuse_bad_perms(tmp_path: Path):
    from rouvr.personas import Persona
    loose = tmp_path / "loose.json"
    loose.write_text('{"meta_api_key": "x"}')
    loose.chmod(0o644)
    with pytest.raises(RouvrError) as ei:
        Persona("b", "coder", "p", "m", loose).credentials()
    assert ei.value.code is Code.AUTHZ
    tight = tmp_path / "tight.json"
    tight.write_text('{"meta_api_key": "x"}')
    tight.chmod(0o600)
    assert Persona("b", "coder", "p", "m", tight).credentials() == {"meta_api_key": "x"}


def test_branch_for():
    assert branch_for("task-9", 2) == "rouvr/task-9/a2"


def test_build_task_prompt_has_essentials():
    p = build_task_prompt({"id": "7", "title": "Fix it", "description": "d",
                           "acceptance_criteria": ["a1", "a2"]}, "org/repo", "rouvr/7/a1", "")
    assert "Fix it" in p and "- a1" in p and "rouvr/7/a1" in p and "do not merge" in p


async def test_local_exec_spec(tmp_path: Path):
    d = tmp_path / "t"
    d.mkdir()
    (d / "p.yaml").write_text("repo: org/repo\nbase_branch: main\ntest_command: [make, test]\n")
    spec = await LocalExecSpecProvider(tmp_path).get("t", {"project_id": "p"})
    assert (spec.repo, spec.base_branch, spec.test_command) == ("org/repo", "main", ["make", "test"])
    assert len(spec.digest) == 64


@pytest.mark.parametrize("spec_text", [
    "repo: org/repo\nbase_branch: main\ntest_command: pytest -q\n",
    "repo: org/repo\nbase_branch: main\ntest_command: [pytest, 1]\n",
    "repo: org/repo\nbase_branch: main\ntest_command: [pytest]\nallowed_paths: src\n",
    "repo: org/repo\nbase_branch: main\ntest_command: ['']\n",
    "repo: org/repo\nbase_branch: main\ntest_command: ['  ']\n",
])
async def test_local_exec_spec_rejects_non_list_of_strings(tmp_path: Path, spec_text):
    d = tmp_path / "t"
    d.mkdir()
    (d / "p.yaml").write_text(spec_text)
    with pytest.raises(RouvrError) as e:
        await LocalExecSpecProvider(tmp_path).get("t", {"project_id": "p"})
    assert e.value.code is Code.CONTRACT


async def test_container_names_unique_run_id_deterministic(tmp_path, monkeypatch):
    from rouvr.testrunner import ContainerTestRunner
    log = tmp_path / "docker.log"
    fake = tmp_path / "docker"
    fake.write_text(f'#!/bin/sh\necho "$@" >> "{log}"\nexit 0\n')
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:{__import__('os').environ['PATH']}")
    r1 = await ContainerTestRunner("img").run(tmp_path, ["true"], timeout_s=5, run_key="k1")
    r2 = await ContainerTestRunner("img").run(tmp_path, ["true"], timeout_s=5, run_key="k1")
    names = [ln.split("--name ", 1)[1].split()[0] for ln in log.read_text().splitlines()]
    assert r1.run_id == r2.run_id and len(set(names)) == 2


@pytest.mark.parametrize("spec_text", [
    "repo: 123\nbase_branch: main\ntest_command: [pytest]\n",
    "repo: org/repo\nbase_branch: [main]\ntest_command: [pytest]\n",
    "repo: org/repo\nbase_branch: main\ntest_command: [pytest]\ntimeout_s: soon\n",
    "repo: org/repo\nbase_branch: main\ntest_command: [pytest]\ntimeout_s: 0\n",
    "repo: org/repo\nbase_branch: main\ntest_command: [pytest]\ntimeout_s: -5\n",
    "repo: org/repo\nbase_branch: main\ntest_command: []\n",
    "42\n",
    "repo: ''\nbase_branch: main\ntest_command: [pytest]\n",
    "repo: org/repo\nbase_branch: '  '\ntest_command: [pytest]\n",
])
async def test_local_exec_spec_rejects_bad_scalars(tmp_path: Path, spec_text):
    d = tmp_path / "t"
    d.mkdir()
    (d / "p.yaml").write_text(spec_text)
    with pytest.raises(RouvrError) as e:
        await LocalExecSpecProvider(tmp_path).get("t", {"project_id": "p"})
    assert e.value.code is Code.CONTRACT


@pytest.mark.parametrize("tenant,project", [("../escape", "p"), ("t", "../escape"), ("t", ".hidden"), ("t", "a/b")])
async def test_local_exec_spec_rejects_traversal(tmp_path: Path, tenant, project):
    with pytest.raises(RouvrError) as e:
        await LocalExecSpecProvider(tmp_path).get(tenant, {"project_id": project})
    assert e.value.code is Code.CONTRACT
