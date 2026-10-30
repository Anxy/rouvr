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
from rouvr.workhub.client import WorkHubClient


def test_ids_deterministic():
    a = workflow_id("t1", "task/9", "rev3")
    assert a == workflow_id("t1", "task/9", "rev3") and a.startswith("rouvr-task-t1-task_9-")
    assert len(a.rsplit("-", 1)[1]) == 16
    assert len(activity_id("assign", 1).rsplit("-", 1)[1]) == 20
    assert handoff_fence(2, 7) == "temporal-handoff-2-e7"


@pytest.mark.parametrize("status,body,code", [
    (429, {}, Code.THROTTLE), (503, {}, Code.DOWNSTREAM), (403, {}, Code.AUTHZ),
    (409, {"code": "RECORD_VERSION"}, Code.MOVED), (409, {"code": "CONTRACT.MOVED"}, Code.CONTRACT_MOVED),
    (422, {"code": "EVIDENCE.MISSING"}, Code.EVIDENCE), (400, {"code": "KEY.REQUIRED"}, Code.CONTRACT),
    (400, {}, Code.BAD_REQUEST)])
def test_error_mapping(status, body, code):
    e = from_http(status, body)
    assert e.code is code
    assert e.to_temporal().non_retryable == (code not in {Code.THROTTLE, Code.DOWNSTREAM, Code.TRANSPORT})


def test_sanitize():
    assert "abc123" not in sanitize_for_log("Authorization: Bearer [REDACTED] token=zzz")


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
