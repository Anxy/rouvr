"""rouvr/activities.py — Temporal activities. All side effects live here; workflows stay deterministic.

R5: _wrap + assign_task ownership skeleton (reclaim-on-retry, claim, attempt,
handoff evidence, lease heartbeats). The coder-turn interior lands in R6
(_run_coder_turn), verify/run_tests/close in R8/R9.

Return annotations are load-bearing: the SDK deserializes activity results into
them, and the workflow uses attribute access."""
from __future__ import annotations

import asyncio
import functools
import json
import os
import tempfile
from pathlib import Path

from temporalio import activity

from rouvr.cheatscan import scan_diff
from rouvr.contracts import (AssignInput, AssignResult, Binding, CloseResult, OpsInput, OpsResult,
                             TaskWorkflowInput, TestResult, VerifyResult)
from rouvr.errors import Code, RouvrError
from rouvr.gate import (SUBMIT_ATTEMPT_TYPE, GateEvidence, count_prior_submits, evaluate,
                        read_submission_file)
from rouvr.gitops import (branch_for, clone_at, diff_range, ensure_pr, pr_facts, prepare_workspace,
                          workspace_path)
from rouvr.hiddentests import (assert_coder_clean, fetch_hidden_suite, hidden_literals,
                               parse_criterion_outcomes)
from rouvr.host import Host
from rouvr.ids import handoff_fence
from rouvr.lease import (LeaseKeeper, claim_assignment_and_lease, open_or_reuse_attempt, resume_or_claim,
                         run_fenced)
from rouvr.messages import MESSAGE_TYPE, messages_for, read_message_file
from rouvr.muse import build_task_prompt, build_reviewer_prompt, read_verdict
from rouvr.ops import OPS_EXECUTOR
from rouvr.receipts import build_links, load_private_key, sign_links


def _ops_ledger():
    """Sync ledger client for the ops leg (same endpoints demo/morning_report.py uses)."""
    import demo.morning_report as m
    return m.ledger_client(os.environ.get("ROUVR_LEDGER_URL"))


def _ops_post(c, path: str, body: dict) -> dict:
    r = c.post(path, json=body)
    if r.status_code >= 400:
        raise RouvrError(Code.DOWNSTREAM, f"ops ledger POST {path}: {r.status_code} {str(r.text)[:200]}")
    return r.json()


def _evidence_payload(e: dict) -> dict | list | str | None:
    body = e.get("body")
    if isinstance(body, str):
        try:
            return json.loads(body)
        except ValueError:
            return None
    return body


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
        if task.get("tenant") != wf.tenant_id or task.get("project") != wf.project_id:
            raise RouvrError(Code.SCOPE,  # S022: workflows execute only inside their own partition
                             f"binding left its scope: workflow {wf.tenant_id}/{wf.project_id}"
                             f" vs task {task.get('tenant')}/{task.get('project')}")
        b, v = self.h.personas.role_pair(wf.tenant_id)  # raises SELF_REVIEW; binding names no personas
        return Binding(task_id=wf.task_id, coder_persona=b.name, reviewer_persona=v.name,
                       contract_rev=wf.contract_rev, record_version=int(task["record_version"]))

    @activity.defn(name="assign_task")
    @_wrap
    async def assign_task(self, inp: AssignInput) -> AssignResult:
        s, wf = self.h.settings, inp.wf
        wh = self.h.workhub_for(None)
        principal = self.h.personas.get(wf.tenant_id, inp.binding.coder_persona, "coder").principal
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
            return await run_fenced(keeper, self._run_coder_turn(inp, lease, fence, state))
        except asyncio.CancelledError:
            raise RouvrError(Code.CANCELLED, "activity cancelled")

    async def _run_coder_turn(self, inp: AssignInput, lease, fence: str, state: dict) -> AssignResult:
        s, wf = self.h.settings, inp.wf
        persona = self.h.personas.get(wf.tenant_id, inp.binding.coder_persona, "coder")
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
        # No git credential for the coder: it commits locally; push/PR happen only
        # after a supervisor grant (user is asked by the supervisor, nobody else).
        env = {"META_API_KEY": creds.get("meta_api_key", ""), "GH_TOKEN": ""}

        def on_event(e: dict) -> None:
            if e.get("session_id"):
                state["session_id"] = str(e["session_id"])

        peer = messages_for(await wh.list_evidence(wf.task_id), "coder")
        prompt = build_task_prompt(task, spec.repo, branch, inp.rework_notes, peer)
        if persona.instructions:
            prompt = f"{persona.instructions}\n\n---\n\n{prompt}"
        turn = await self.h.muse.run(
            prompt=prompt,
            model=persona.muse_model, cwd=ws, env=env, timeout_s=spec.timeout_s,
            resume=resume, on_event=on_event)
        if (msg := read_message_file(ws, "reviewer")):
            ev.append(await wh.attach_evidence_exact(
                wf.task_id, MESSAGE_TYPE, f"{fence}:msg-reviewer",
                {"from": "coder", "to": "reviewer", "text": msg}))
        ev.append(await wh.attach_evidence_exact(wf.task_id, "muse_session", fence,
                                                 {"session_id": turn.session_id, "events": turn.events}))
        if turn.session_id:
            ev.append(await wh.attach_evidence_exact(
                wf.task_id, "peer_session", f"{fence}:peer",
                {"role": "coder", "session_id": turn.session_id, "workspace": str(ws)}))
        grant = await self._await_git_grant(wf.task_id, fence, branch, state, ev, wh)
        await self._push_branch(ws, branch, grant)
        pr_url, head = await ensure_pr(ws, spec.repo, spec.base_branch, branch,
                                       f"[Rouvr] {task.get('title', wf.task_id)}",
                                       {"GH_TOKEN": grant})
        ev.append(await wh.attach_evidence_exact(wf.task_id, "pr_record", fence,
                                                 {"url": pr_url, "head_sha": head, "branch": branch}))
        if inp.attempt_no > 1:  # S013: close the superseded attempt's PR
            from rouvr.gitops import close_superseded_pr
            prev = branch_for(wf.task_id, inp.attempt_no - 1)
            try:
                closed = await close_superseded_pr(spec.repo, prev, pr_url, {"GH_TOKEN": grant})
                note = {"closed_pr": closed, "superseded_by": pr_url} if closed \
                    else {"closed_pr": "", "note": "no open PR on previous branch"}
            except Exception as e:  # failure is evidence, never silent
                note = {"close_failed": f"{type(e).__name__}: {e}"[:200], "prev_branch": prev}
            ev.append(await wh.attach_evidence_exact(wf.task_id, "pr_superseded",
                                                     f"{fence}:pr-superseded", note))
        # No record_result here: one result per attempt, written by run_tests as
        # "tested" (the build facts live in pr_record evidence). A second core
        # for the same attempt 409s on both backends (R10 rehearsal finding).
        return AssignResult(attempt_id=lease.attempt_id, lease_epoch=lease.lease_epoch,
                            muse_session_id=turn.session_id, branch=branch, pr_url=pr_url, head_sha=head,
                            evidence_ids=ev)

    def _git_grant(self, task_id: str) -> str:
        """Supervisor-written grant file (0600, never in WorkHub): {github_token}."""
        p = self.h.settings.workspace_root / f".git-grant-{task_id}.json"
        try:
            if p.stat().st_mode & 0o077:
                return ""
            return str(json.loads(p.read_text()).get("github_token") or "")
        except (OSError, ValueError):
            return ""

    async def _await_git_grant(self, task_id: str, fence: str, branch: str,
                               state: dict, ev: list, wh) -> str:
        """No credential, no push: request one from the supervisor and wait.

        The coder never holds a git credential and never talks to the user;
        the request lands as WorkHub evidence, the supervisor polls it, asks
        the user, and writes the grant file. Heartbeats keep the lease alive."""
        if (token := self._git_grant(task_id)):
            return token
        ev.append(await wh.attach_evidence_exact(
            task_id, "credential_request", f"{fence}:git-grant",
            {"from": "coder", "to": "supervisor",
             "reason": "git credential needed to push branch and open PR", "branch": branch}))
        for _ in range(90):  # ~30 min at 20 s, inside the 1800 s activity timeout
            await asyncio.sleep(20)
            activity.heartbeat(state)
            if (token := self._git_grant(task_id)):
                return token
        raise RouvrError(Code.DOWNSTREAM, "awaiting supervisor git credential grant",
                         details={"task_id": task_id})

    async def _push_branch(self, ws, branch: str, token: str) -> None:
        from rouvr.gitops import clone_env
        env = clone_env()
        env["GH_TOKEN"] = token  # push auth is the granted token only, never worker env
        # (gh credential helper serves GH_TOKEN), never the host's stored git credentials.
        args = ["git"] + (["-c", "credential.helper=!gh auth git-credential"] if token else []) \
            + ["push", "origin", branch]
        proc = await asyncio.create_subprocess_exec(
            *args, cwd=str(ws), env=env,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        _, err = await proc.communicate()
        if proc.returncode != 0:
            raise RouvrError(Code.DOWNSTREAM,
                             f"git push failed: {err.decode(errors='replace')[:200]}")

    @activity.defn(name="run_tests")
    @_wrap
    async def run_tests(self, wf: TaskWorkflowInput, built: AssignResult) -> TestResult:
        s, h = self.h.settings, self.h
        wh = self.h.workhub_for(None)  # system evidence under the orchestrator token
        task = await wh.get_task(wf.task_id)
        spec = await h.exec_specs.get(wf.tenant_id, task)
        hidden_dir, manifest = fetch_hidden_suite(s.hidden_tests_root, wf.task_id)
        assert_coder_clean(workspace_path(s.workspace_root, built.branch), hidden_dir, manifest)
        fence = f"{built.attempt_id}:run-tests"
        with tempfile.TemporaryDirectory(prefix="rouvr-verify-") as tmp:
            clone = await clone_at(spec.repo, built.head_sha, spec.base_branch, Path(tmp) / "src")
            target = clone / "_rouvr_hidden"
            target.mkdir()  # hidden tests enter the fresh clone only — never the coder workspace
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
        _, reviewer = h.personas.role_pair(wf.tenant_id)  # SELF_REVIEW re-checked at the verify claim
        wh = self.h.workhub_for(reviewer)  # reviewer-identity reads + writes; coder never touches these
        # Supervisor-visible sync: entering review moves the task to needs_review.
        await self._close_to(wf.task_id, "needs_review", "verify entered",
                             ("state", wf.task_id, built.attempt_id))
        task = await wh.get_task(wf.task_id)
        spec = await h.exec_specs.get(wf.tenant_id, task)
        rows = await wh.list_evidence(wf.task_id)
        ws = workspace_path(s.workspace_root, built.branch)
        pr_branch, ci = await pr_facts(built.pr_url, {"GH_TOKEN": self._git_grant(wf.task_id)})
        facts = GateEvidence(
            expected_branch=built.branch, pr_url=built.pr_url, pr_head_branch=pr_branch, ci_status=ci,
            criteria=tuple(c["id"] if isinstance(c, dict) else str(c)
                           for c in task.get("acceptance_criteria") or []),
            evidence_refs=frozenset({tests.evidence_id, f"testrun:{tests.run_id}"}),
            prior_submits=count_prior_submits(rows))  # task-wide: 3 judged rejects block (gate.py)
        submission = read_submission_file(ws)
        if submission and not submission.get("__malformed__"):
            # The system creates the PR after the coder turn (supervisor grant
            # flow): its URL is authoritative, the coder cannot know it.
            submission["pr_url"] = built.pr_url
        gv = evaluate(submission, facts)
        fence = f"{built.attempt_id}:verify"
        if gv.verdict in ("blocked", "failed"):
            ev = await wh.attach_evidence_exact(wf.task_id, "reviewer_report", fence,
                                                {"verdict": "reject", "gate_detail": gv.verdict,
                                                 "feedback": gv.feedback})
            return VerifyResult("reject", gv.feedback or gv.verdict, reviewer.principal, ev)
        if gv.verdict == "reject":
            # Deterministic per attempt: a Temporal retry re-attaches the same
            # fence and attach_evidence_exact dedups, instead of minting
            # submit-{n+1} and burning another of the 3 submit rounds.
            await wh.attach_evidence_exact(wf.task_id, SUBMIT_ATTEMPT_TYPE, f"{fence}:submit",
                                           {"verdict": "request_changes", "feedback": gv.feedback})
            ev = await wh.attach_evidence_exact(wf.task_id, "reviewer_report", fence,
                                                {"verdict": "request_changes", "feedback": gv.feedback})
            return VerifyResult("request_changes", gv.feedback or "gate rejected", reviewer.principal, ev)
        # Reviewer diff comes from a fresh clone at head_sha, never the coder
        # workspace: mutating the coder ws after submit cannot change review.
        with tempfile.TemporaryDirectory(prefix="rouvr-review-") as rtmp:
            rclone = await clone_at(spec.repo, built.head_sha, spec.base_branch, Path(rtmp) / "src")
            diff = await diff_range(rclone, f"origin/{spec.base_branch}", built.head_sha)
        vdir = ws.parent / f"{ws.name}__verify"
        vdir.mkdir(parents=True, exist_ok=True)
        for stale in ("verdict.json", "message-coder.json"):
            (vdir / stale).unlink(missing_ok=True)
        creds = reviewer.credentials()
        vprompt = build_reviewer_prompt(task, diff, tests.summary,
                                        messages_for(rows, "reviewer"))
        if reviewer.instructions:
            vprompt = f"{reviewer.instructions}\n\n---\n\n{vprompt}"
        vturn = await h.muse.run(prompt=vprompt,
                         model=reviewer.muse_model,
                         cwd=vdir, env={"META_API_KEY": creds.get("meta_api_key", "")}, timeout_s=spec.timeout_s)
        if vturn.session_id:
            await wh.attach_evidence_exact(wf.task_id, "peer_session", f"{fence}:peer",
                                           {"role": "reviewer", "session_id": vturn.session_id,
                                            "workspace": str(vdir)})
        if (msg := read_message_file(vdir, "coder")):
            await wh.attach_evidence_exact(wf.task_id, MESSAGE_TYPE, f"{fence}:msg-coder",
                                           {"from": "reviewer", "to": "coder", "text": msg})
        try:
            verdict, notes = read_verdict(vdir)
        except RouvrError as e:  # unreadable verdict fails closed: block + escalate, never approve
            ev = await wh.attach_evidence_exact(wf.task_id, "reviewer_report", fence,
                                                {"verdict": "reject", "verdict_error": str(e)})
            return VerifyResult("reject", f"reviewer output unusable: {e}", reviewer.principal, ev)
        await wh.attach_evidence_exact(wf.task_id, SUBMIT_ATTEMPT_TYPE, f"{fence}:submit",
                                       {"verdict": verdict})
        ev = await wh.attach_evidence_exact(wf.task_id, "reviewer_report", fence,
                                            {"verdict": verdict, "notes": notes})
        return VerifyResult(verdict, notes, reviewer.principal, ev)  # verdict in VERDICTS by read_verdict

    # Next hop toward done. Stage map: GET /api/v1/tasks/transitions/help; ledger: TRANSITIONS in app.py.
    _HOPS = {"queued": "running", "running": "needs_review", "needs_review": "done",
              "submitted_for_review": "done", "blocked": "queued", "waiting_on_human": "running",
              "needs_enrichment": "running", "needs_human_intervention": "running",
              "execution_failed": "running", "open": "claimed", "claimed": "needs_review"}

    def _revoke_grant(self, task_id: str, to_state: str) -> None:
        # Terminal states retire the per-task git grant: a plaintext token
        # must not outlive the task it was issued for. Best-effort, never
        # fails the close. Non-terminal hops (needs_review) keep it.
        root = getattr(getattr(self.h, "settings", None), "workspace_root", None)
        if to_state in ("done", "blocked", "failed") and root is not None:
            from rouvr.gitops import revoke_git_grant
            revoke_git_grant(root, task_id)

    async def _close_to(self, task_id: str, to_state: str, reason: str, key: tuple) -> CloseResult:
        wh = self.h.workhub_for(None)  # closer principal (canary closeout used the coordinator JWT)
        for hop in range(6):  # walk legal hops; every hop is CAS + read back
            task = await wh.get_task(task_id)
            cur = task.get("status") or task.get("state")
            if cur == to_state:
                self._revoke_grant(task_id, to_state)
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
        wh = self.h.workhub_for(None)
        await wh.attach_evidence_exact(wf.task_id, "escalation", f"escalate:{attempt_id}:{reason}",
                                       {"reason": reason, "attempt_id": attempt_id})
        return True

    @activity.defn(name="sign_receipt")
    @_wrap
    async def sign_receipt(self, wf: TaskWorkflowInput, built: AssignResult, tests: TestResult,
                           v: VerifyResult) -> dict:
        s = self.h.settings
        links = build_links(task_id=wf.task_id, contract_rev=wf.contract_rev, attempt_id=built.attempt_id,
                            muse_session_id=built.muse_session_id, pr_url=built.pr_url, head_sha=built.head_sha,
                            test_run_id=tests.run_id, test_evidence_id=tests.evidence_id,
                            verdict=v.verdict, reviewer=v.reviewer_principal, verify_evidence_id=v.evidence_id)
        signed = sign_links(links, load_private_key(s.receipt_key_path), s.receipt_key_id)
        return {"digest": signed.digest, "signature": signed.signature, "key_id": signed.key_id}

    # Ops leg (T3): the morning_report flow as activities. Every step keys off
    # run_key, so a kill mid-step resumes without double-send or double-claim.

    @activity.defn(name="ops_prepare")
    @_wrap
    async def ops_prepare(self, inp: OpsInput) -> dict:
        c = _ops_ledger()
        items = c.get("/v1/tasks").json()["items"]
        match = [t for t in items if t["title"] == inp.run_key and t.get("tenant") == inp.tenant
                 and t.get("project") == inp.project]
        if match:
            tid = str(match[0]["id"])
        else:
            tid = str(_ops_post(c, "/v1/tasks", {"title": inp.run_key, "intent": inp.instruction,
                                                "tenant": inp.tenant, "project": inp.project})["id"])
        task = c.get(f"/v1/tasks/{tid}").json()
        out = {"task_id": tid, "state": task["state"], "message_id": "", "sink": ""}
        if task["state"] == "done":  # rerun after completion: hand back the receipt
            for e in task.get("evidence") or []:
                if e.get("kind") == "ops_receipt" and isinstance(_evidence_payload(e), dict):
                    out["message_id"] = _evidence_payload(e).get("message_id", "")
                    out["sink"] = _evidence_payload(e).get("sink", "")
        return out

    @activity.defn(name="ops_compile")
    @_wrap
    async def ops_compile(self, inp: OpsInput, task_id: str) -> dict:
        import demo.morning_report as m
        c = _ops_ledger()
        contract = m.compile_contract(inp.instruction)  # revoked/invalid raises before any claim
        _ops_post(c, f"/v1/tasks/{task_id}/contracts", contract)  # ledger dedups by sha
        return contract

    @activity.defn(name="ops_claim")
    @_wrap
    async def ops_claim(self, inp: OpsInput, task_id: str) -> dict:
        c = _ops_ledger()
        r = c.post(f"/v1/tasks/{task_id}/transition",
                   json={"to": "claimed", "idempotency_key": f"{inp.run_key}:claim",
                         "owner": OPS_EXECUTOR})
        if r.status_code == 409 and r.json().get("detail", "").startswith("illegal transition"):
            pass  # already past claimed (retry after kill): the key replay or a later state
        elif r.status_code >= 400:
            raise RouvrError(Code.DOWNSTREAM, f"ops claim: {r.status_code} {str(r.text)[:200]}")
        items = c.get("/v1/tasks").json()["items"]
        figures = {s: sum(1 for t in items if t["state"] == s) for s in ("done", "blocked", "failed")}
        day = inp.run_key.split(":", 1)[-1]
        return {"figures": figures,
                "report": f"Lane report for {day}\n" + json.dumps({"figures": figures}, sort_keys=True)}

    @activity.defn(name="ops_deliver")
    @_wrap
    async def ops_deliver(self, inp: OpsInput, task_id: str, sink: str, report: str) -> dict:
        import demo.morning_report as m
        c = _ops_ledger()
        sent = m.deliver(report, inp.run_key, sink)  # file-exists check: kill+retry sends once
        _ops_post(c, f"/v1/tasks/{task_id}/evidence",
                  {"kind": "delivery", "body": {"sink": sink, **sent}})
        return sent

    @activity.defn(name="ops_verify")
    @_wrap
    async def ops_verify(self, inp: OpsInput, task_id: str, sink: str, msg_id: str) -> dict:
        import demo.morning_report as m
        from backend.rouvr_models.providers import get_provider
        from rouvr.ops import OPS_EXECUTOR, OPS_JUDGE, check_readback
        c = _ops_ledger()
        items = c.get("/v1/tasks").json()["items"]
        figures = {s: sum(1 for t in items if t["state"] == s) for s in ("done", "blocked", "failed")}
        health = c.get("/health").status_code == 200
        readback, figures_match = check_readback(m.OUTBOX, msg_id, figures)
        verdict = "pass" if (readback and figures_match and health) else "fail"
        _ops_post(c, f"/v1/tasks/{task_id}/evidence",
                  {"kind": "reviewer",
                   "body": {"sink": sink, "verdict": verdict, "readback": readback,
                            "figures_match": figures_match, "health": health,
                            "judge_principal": OPS_JUDGE, "executor": OPS_EXECUTOR,
                            "model": get_provider().name}})
        return {"verdict": verdict, "figures": figures}

    @activity.defn(name="ops_close")
    @_wrap
    async def ops_close(self, inp: OpsInput, task_id: str, verdict: str, sink: str, msg_id: str,
                        figures: dict) -> OpsResult:
        from rouvr.ops import OPS_DIGEST_DONE
        c = _ops_ledger()
        if verdict == "pass":
            _ops_post(c, f"/v1/tasks/{task_id}/transition",
                      {"to": "done", "idempotency_key": f"{inp.run_key}:done"})
            _ops_post(c, f"/v1/tasks/{task_id}/evidence",
                      {"kind": "ops_receipt",
                       "body": {"sink": sink, "message_id": msg_id, "figures": figures,
                                "digest": OPS_DIGEST_DONE}})
            return OpsResult("done", OPS_DIGEST_DONE, task_id, msg_id, sink)
        _ops_post(c, f"/v1/tasks/{task_id}/transition",
                  {"to": "failed", "idempotency_key": f"{inp.run_key}:fail"})
        return OpsResult("failed", "ESCALATED: verification failed on all sinks", task_id, msg_id, sink)

    @activity.defn(name="ops_block")
    @_wrap
    async def ops_block(self, inp: OpsInput, task_id: str, reason: str) -> OpsResult:
        c = _ops_ledger()
        _ops_post(c, f"/v1/tasks/{task_id}/evidence",
                  {"kind": "escalation", "body": {"reason": reason, "run_key": inp.run_key}})
        _ops_post(c, f"/v1/tasks/{task_id}/transition",
                  {"to": "blocked", "idempotency_key": f"{inp.run_key}:park"})
        return OpsResult("blocked", f"ESCALATED: {reason}", task_id)
