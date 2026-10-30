"""rouvr/activities.py — Temporal activities. All side effects live here; workflows stay deterministic.

R5: _wrap + assign_task ownership skeleton (reclaim-on-retry, claim, attempt,
handoff evidence, lease heartbeats). The builder-turn interior lands in R6
(_run_builder_turn), verify/run_tests/close in R8/R9.

Return annotations are load-bearing: the SDK deserializes activity results into
them, and the workflow uses attribute access."""
from __future__ import annotations

import asyncio
import functools
import tempfile
from pathlib import Path

from temporalio import activity

from rouvr.cheatscan import scan_diff
from rouvr.contracts import (AssignInput, AssignResult, Binding, CloseResult, TaskWorkflowInput, TestResult,
                             VerifyResult)
from rouvr.errors import Code, RouvrError
from rouvr.gate import (SUBMIT_ATTEMPT_TYPE, GateEvidence, count_prior_submits, evaluate,
                        read_submission_file)
from rouvr.gitops import (branch_for, clone_at, diff_range, ensure_pr, pr_facts, prepare_workspace,
                          workspace_path)
from rouvr.hiddentests import (assert_builder_clean, fetch_hidden_suite, hidden_literals,
                               parse_criterion_outcomes)
from rouvr.host import Host
from rouvr.ids import handoff_fence
from rouvr.lease import (LeaseKeeper, claim_assignment_and_lease, open_or_reuse_attempt, resume_or_claim,
                         run_fenced)
from rouvr.messages import MESSAGE_TYPE, messages_for, read_message_file
from rouvr.muse import build_task_prompt, build_verifier_prompt, read_verdict
from rouvr.receipts import build_links, load_private_key, sign_links


def _wrap(fn):
    """Convert RouvrError -> ApplicationError with the right non_retryable flag."""
    @functools.wraps(fn)
    async def inner(*a, **kw):
        try:
            return await fn(*a, **kw)
        except RouvrError as e:
            raise e.to_temporal() from e
    return inner


class Activities:
    def __init__(self, host: Host) -> None:
        self.h = host

    @activity.defn(name="verify_binding")
    @_wrap
    async def verify_binding(self, wf: TaskWorkflowInput) -> Binding:
        wh = self.h.workhub_for(None)
        task = await wh.get_task(wf.task_id)
        if str(task.get("contract_rev")) != wf.contract_rev:
            raise RouvrError(Code.CONTRACT_MOVED, "contract revision changed since start")
        b, v = self.h.personas.role_pair(wf.tenant_id)  # raises SELF_REVIEW; binding names no personas
        return Binding(task_id=wf.task_id, builder_persona=b.name, verifier_persona=v.name,
                       contract_rev=wf.contract_rev, record_version=int(task["record_version"]))

    @activity.defn(name="assign_task")
    @_wrap
    async def assign_task(self, inp: AssignInput) -> AssignResult:
        s, wf = self.h.settings, inp.wf
        wh = self.h.workhub_for(None)
        principal = self.h.personas.get(wf.tenant_id, inp.binding.builder_persona, "builder").principal
        if activity.info().attempt > 1:  # retry after worker death: renew if live, else reclaim + claim (R5)
            task = await wh.get_task(wf.task_id)
            number = task.get("task_number") or wf.task_id  # ledger rows: the id is the number
            lease, assignment_id = await resume_or_claim(wh, wf.task_id, int(number), principal,
                                                         inp.attempt_no, s.lease_ttl_s)
        else:
            lease, assignment_id = await claim_assignment_and_lease(wh, wf.task_id, principal,
                                                                    inp.attempt_no, s.lease_ttl_s)
        lease = await open_or_reuse_attempt(wh, lease)
        fence = handoff_fence(inp.attempt_no, lease.lease_epoch)
        info = activity.info()
        await wh.attach_evidence_exact(wf.task_id, "temporal_handoff", fence,
                                       {"workflow_id": info.workflow_id, "run_id": info.workflow_run_id,
                                        "attempt_id": lease.attempt_id})
        state = {"session_id": "", "beat": 0, "attempt_id": lease.attempt_id}

        def beat(n: int) -> None:
            state["beat"] = n
            activity.heartbeat(state)  # Temporal heartbeat (timeout 90 s) rides on lease renewals

        keeper = LeaseKeeper(wh, lease, assignment_id, s.lease_ttl_s, s.heartbeat_s, on_beat=beat)
        activity.heartbeat(state)
        try:
            return await run_fenced(keeper, self._run_builder_turn(inp, lease, fence, state))
        except asyncio.CancelledError:
            raise RouvrError(Code.CANCELLED, "activity cancelled")

    async def _run_builder_turn(self, inp: AssignInput, lease, fence: str, state: dict) -> AssignResult:
        s, wf = self.h.settings, inp.wf
        persona = self.h.personas.get(wf.tenant_id, inp.binding.builder_persona, "builder")
        wh = self.h.workhub_for(persona)
        task = await wh.get_task(wf.task_id)
        spec = await self.h.exec_specs.get(wf.tenant_id, task)
        ev = [await wh.attach_evidence_exact(wf.task_id, "execution_spec", fence,
                                             {"digest": spec.digest, "repo": spec.repo})]
        branch = branch_for(wf.task_id, inp.attempt_no)
        # Resume: heartbeat details carry the session id from a previous activity attempt (R2: RESUME).
        details = activity.info().heartbeat_details
        resume = inp.resume_session_id or (details[0].get("session_id") if details else None)
        if resume and not s.resume_supported:
            resume = None
        ws = await prepare_workspace(s.workspace_root, spec.repo, spec.base_branch, branch)
        creds = persona.credentials()
        # META_API_KEY: verified name headless `muse` honors (R6 probe; MUSE_API_KEY is ignored).
        env = {"META_API_KEY": creds.get("meta_api_key", ""), "GH_TOKEN": creds.get("github_token", "")}

        def on_event(e: dict) -> None:
            if e.get("session_id"):
                state["session_id"] = str(e["session_id"])

        peer = messages_for(await wh.list_evidence(wf.task_id), "builder")
        turn = await self.h.muse.run(
            prompt=build_task_prompt(task, spec.repo, branch, inp.rework_notes, peer),
            model=persona.muse_model, cwd=ws, env=env, timeout_s=spec.timeout_s,
            resume=resume, on_event=on_event)
        if (msg := read_message_file(ws, "verifier")):
            ev.append(await wh.attach_evidence_exact(
                wf.task_id, MESSAGE_TYPE, f"{fence}:msg-verifier",
                {"from": "builder", "to": "verifier", "text": msg}))
        ev.append(await wh.attach_evidence_exact(wf.task_id, "muse_session", fence,
                                                 {"session_id": turn.session_id, "events": turn.events}))
        pr_url, head = await ensure_pr(ws, spec.repo, spec.base_branch, branch,
                                       f"[Rouvr] {task.get('title', wf.task_id)}", env)
        ev.append(await wh.attach_evidence_exact(wf.task_id, "pr_record", fence,
                                                 {"url": pr_url, "head_sha": head, "branch": branch}))
        # No record_result here: one result per attempt, written by run_tests as
        # "tested" (the build facts live in pr_record evidence). A second core
        # for the same attempt 409s on both backends (R10 rehearsal finding).
        return AssignResult(attempt_id=lease.attempt_id, lease_epoch=lease.lease_epoch,
                            muse_session_id=turn.session_id, branch=branch, pr_url=pr_url, head_sha=head,
                            evidence_ids=ev)

    @activity.defn(name="run_tests")
    @_wrap
    async def run_tests(self, wf: TaskWorkflowInput, built: AssignResult) -> TestResult:
        s, h = self.h.settings, self.h
        wh = self.h.workhub_for(None)  # system evidence under the orchestrator token
        task = await wh.get_task(wf.task_id)
        spec = await h.exec_specs.get(wf.tenant_id, task)
        hidden_dir, manifest = fetch_hidden_suite(s.hidden_tests_root, wf.task_id)
        assert_builder_clean(workspace_path(s.workspace_root, built.branch), hidden_dir, manifest)
        fence = f"{built.attempt_id}:run-tests"
        with tempfile.TemporaryDirectory(prefix="rouvr-verify-") as tmp:
            clone = await clone_at(spec.repo, built.head_sha, spec.base_branch, Path(tmp) / "src")
            target = clone / "_rouvr_hidden"
            target.mkdir()  # hidden tests enter the fresh clone only — never the builder workspace
            for n in manifest.files:
                (target / n).write_bytes((hidden_dir / n).read_bytes())
            # `python -m` puts the clone on sys.path so hidden tests import repo code.
            repo_run = await h.tests.run(clone, spec.test_command, spec.timeout_s, f"{fence}:repo")
            hidden_run = await h.tests.run(clone, ["python", "-m", "pytest", "_rouvr_hidden", "-v"],
                                           spec.timeout_s, f"{fence}:hidden")
            diff = await diff_range(clone, f"origin/{spec.base_branch}", built.head_sha)
        outcomes = parse_criterion_outcomes(hidden_run.output_tail)
        flags = scan_diff(diff, hidden_literals=hidden_literals(hidden_dir))
        criteria_ok = bool(outcomes) and all(outcomes.values())
        passed = repo_run.passed and hidden_run.passed and criteria_ok and not flags
        summary = (f"repo={'pass' if repo_run.passed else 'fail'} "
                   f"hidden={sum(outcomes.values())}/{len(outcomes)} "
                   f"cheat_flags={len(flags)}")
        ev = await wh.attach_evidence_exact(wf.task_id, "test_run", fence,
                                            {"run_id": f"{repo_run.run_id}+{hidden_run.run_id}",
                                             "passed": passed, "summary": summary,
                                             "criteria": outcomes,
                                             "flags": [f.__dict__ for f in flags],
                                             "repo_tail": repo_run.output_tail,
                                             "hidden_tail": hidden_run.output_tail})
        await wh.record_result(wf.task_id, built.attempt_id, built.lease_epoch,
                               {"status": "tested", "passed": passed, "summary": summary})
        return TestResult(run_id=f"{repo_run.run_id}+{hidden_run.run_id}", passed=passed,
                          summary=summary, evidence_id=ev)

    @activity.defn(name="verify")
    @_wrap
    async def verify(self, wf: TaskWorkflowInput, b: Binding, built: AssignResult, tests: TestResult) -> VerifyResult:
        s, h = self.h.settings, self.h
        _, verifier = h.personas.role_pair(wf.tenant_id)  # SELF_REVIEW re-checked at the verify claim
        wh = self.h.workhub_for(verifier)  # verifier-identity reads + writes; builder never touches these
        # Supervisor-visible sync: entering review moves the task to needs_review.
        await self._close_to(wf.task_id, "needs_review", "verify entered",
                             ("state", wf.task_id, built.attempt_id))
        task = await wh.get_task(wf.task_id)
        spec = await h.exec_specs.get(wf.tenant_id, task)
        rows = await wh.list_evidence(wf.task_id)
        ws = workspace_path(s.workspace_root, built.branch)
        pr_branch, ci = await pr_facts(built.pr_url, {"GH_TOKEN": verifier.credentials().get("github_token", "")})
        facts = GateEvidence(
            expected_branch=built.branch, pr_url=built.pr_url, pr_head_branch=pr_branch, ci_status=ci,
            criteria=tuple(c["id"] if isinstance(c, dict) else str(c)
                           for c in task.get("acceptance_criteria") or []),
            evidence_refs=frozenset({tests.evidence_id, f"testrun:{tests.run_id}"}),
            prior_submits=count_prior_submits(rows))
        gv = evaluate(read_submission_file(ws), facts)
        fence = f"{built.attempt_id}:verify"
        if gv.verdict in ("blocked", "failed"):
            ev = await wh.attach_evidence_exact(wf.task_id, "verifier_report", fence,
                                                {"gate": gv.verdict, "feedback": gv.feedback})
            return VerifyResult("reject", gv.feedback or gv.verdict, verifier.principal, ev)
        if gv.verdict == "reject":
            await wh.attach_evidence_exact(wf.task_id, SUBMIT_ATTEMPT_TYPE, f"{fence}:submit-{facts.prior_submits + 1}",
                                           {"gate": "reject", "feedback": gv.feedback})
            ev = await wh.attach_evidence_exact(wf.task_id, "verifier_report", fence,
                                                {"gate": "reject", "feedback": gv.feedback})
            return VerifyResult("request_changes", gv.feedback or "gate rejected", verifier.principal, ev)
        await wh.attach_evidence_exact(wf.task_id, SUBMIT_ATTEMPT_TYPE, f"{fence}:submit-{facts.prior_submits + 1}",
                                       {"gate": "approved"})
        diff = await diff_range(ws, f"origin/{spec.base_branch}", built.head_sha)
        vdir = ws.parent / f"{ws.name}__verify"
        vdir.mkdir(parents=True, exist_ok=True)
        creds = verifier.credentials()
        await h.muse.run(prompt=build_verifier_prompt(task, diff, tests.summary,
                                                      messages_for(rows, "verifier")),
                         model=verifier.muse_model,
                         cwd=vdir, env={"META_API_KEY": creds.get("meta_api_key", "")}, timeout_s=spec.timeout_s)
        if (msg := read_message_file(vdir, "builder")):
            await wh.attach_evidence_exact(wf.task_id, MESSAGE_TYPE, f"{fence}:msg-builder",
                                           {"from": "verifier", "to": "builder", "text": msg})
        try:
            verdict, notes = read_verdict(vdir)
        except RouvrError as e:  # unreadable verdict fails closed: block + escalate, never approve
            ev = await wh.attach_evidence_exact(wf.task_id, "verifier_report", fence, {"gate": "approved",
                                                                                       "verdict_error": str(e)})
            return VerifyResult("reject", f"verifier output unusable: {e}", verifier.principal, ev)
        ev = await wh.attach_evidence_exact(wf.task_id, "verifier_report", fence,
                                            {"gate": "approved", "verdict": verdict, "notes": notes})
        return VerifyResult(verdict, notes, verifier.principal, ev)  # verdict in VERDICTS by read_verdict

    # Next hop toward done. Stage map: GET /api/v1/tasks/transitions/help; ledger: TRANSITIONS in app.py.
    _HOPS = {"queued": "running", "running": "needs_review", "needs_review": "done",
              "submitted_for_review": "done", "blocked": "queued", "waiting_on_human": "running",
              "needs_enrichment": "running", "needs_human_intervention": "running",
              "execution_failed": "running", "open": "claimed", "claimed": "needs_review"}

    async def _close_to(self, task_id: str, to_state: str, reason: str, key: tuple) -> CloseResult:
        wh = self.h.workhub_for(None)  # closer principal (canary closeout used the coordinator JWT)
        for hop in range(6):  # walk legal hops; every hop is CAS + read back
            task = await wh.get_task(task_id)
            cur = task.get("status") or task.get("state")
            if cur == to_state:
                return CloseResult(final_state=cur, record_version=int(task["record_version"]))
            nxt = to_state if to_state == "blocked" and cur not in ("done", "archived") else self._HOPS.get(str(cur))
            if nxt is None:
                raise RouvrError(Code.EVIDENCE, f"no transition {cur!r} -> {to_state!r}")
            await wh.transition_batch([{"task_id": task_id, "status": nxt,
                                        "expected_record_version": int(task["record_version"]),
                                        "reason": reason}], (*key, hop))
        raise RouvrError(Code.EVIDENCE, f"close did not converge on {to_state} in 6 hops")

    @activity.defn(name="close_task")
    @_wrap
    async def close_task(self, wf: TaskWorkflowInput, b: Binding, attempt_id: str) -> CloseResult:
        return await self._close_to(wf.task_id, "done", f"approved on {attempt_id}", ("close", wf.task_id, attempt_id))

    @activity.defn(name="mark_blocked")
    @_wrap
    async def mark_blocked(self, wf: TaskWorkflowInput, reason: str) -> CloseResult:
        return await self._close_to(wf.task_id, "blocked", reason, ("block", wf.task_id, reason))

    @activity.defn(name="escalate")
    @_wrap
    async def escalate(self, wf: TaskWorkflowInput, reason: str, attempt_id: str) -> bool:
        raise NotImplementedError("P2")

    @activity.defn(name="sign_receipt")
    @_wrap
    async def sign_receipt(self, wf: TaskWorkflowInput, built: AssignResult, tests: TestResult,
                           v: VerifyResult) -> dict:
        s = self.h.settings
        links = build_links(task_id=wf.task_id, contract_rev=wf.contract_rev, attempt_id=built.attempt_id,
                            muse_session_id=built.muse_session_id, pr_url=built.pr_url, head_sha=built.head_sha,
                            test_run_id=tests.run_id, test_evidence_id=tests.evidence_id,
                            verdict=v.verdict, verifier=v.verifier_principal, verify_evidence_id=v.evidence_id)
        signed = sign_links(links, load_private_key(s.receipt_key_path), s.receipt_key_id)
        return {"digest": signed.digest, "signature": signed.signature, "key_id": signed.key_id}
