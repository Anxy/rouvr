"""rouvr/muse.py — Muse Code CLI headless wrapper (single launcher; ports hyeve-lane aix_muse_worker.py).
[V] argv used today by hyeve-lane `_task_loop_child_argv` (workflows.py:2033):
    muse exec --json --provider meta --session-id <sid> --prompt-file <f> --workspace <ws> --max-model-steps 40
Rouvr mints the sid (uuid4) per attempt and records it BEFORE launch (session-binding row).
R2 VERIFIED (docs/spikes/muse-resume.md): re-running with the same --session-id resumes prior
context, so RESUME_SUPPORTED defaults true. Events carry no session_id; the passed sid is authoritative."""
from __future__ import annotations

import asyncio
import json
import os
import signal
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from rouvr.errors import Code, RouvrError
from rouvr.ids import sanitize_for_log

FLAGS = {"json": "--json", "provider": "--provider", "session": "--session-id", "prompt_file": "--prompt-file",
         "workspace": "--workspace", "max_steps": "--max-model-steps", "model": "--model"}  # all in `muse exec --help`
PROVIDER = "meta"
MAX_MODEL_STEPS = 40


@dataclass
class MuseTurn:
    session_id: str
    exit_code: int
    events: int
    last_message: str


class MuseLauncher:
    def __init__(self, bin_path: str = "muse", kill_grace_s: float = 10.0) -> None:
        self.bin, self.grace = bin_path, kill_grace_s

    def argv(self, prompt_file: Path, model: str, cwd: Path, session_id: str) -> list[str]:
        argv = [self.bin, "exec", FLAGS["json"], FLAGS["provider"], PROVIDER, FLAGS["session"], session_id,
               FLAGS["prompt_file"], str(prompt_file), FLAGS["workspace"], str(cwd),
               FLAGS["max_steps"], str(MAX_MODEL_STEPS)]
        return argv if not model else argv + [FLAGS["model"], model]  # "" = CLI default model

    async def run(self, *, prompt: str, model: str, cwd: Path, env: dict[str, str], timeout_s: int,
                  resume: str | None = None, on_event: Callable[[dict], None] = lambda e: None,
                  cancel: asyncio.Event | None = None) -> MuseTurn:
        sid = resume or str(uuid.uuid4())  # resume => same sid (R2); else fresh sid per leg
        prompt_file = cwd.parent / f".rouvr-prompt-{sid}.md"   # outside the repo worktree
        prompt_file.write_text(prompt)
        proc = await asyncio.create_subprocess_exec(
            *self.argv(prompt_file, model, cwd, sid), cwd=str(cwd),
            env={"PATH": os.environ.get("PATH", ""), "HOME": str(cwd), **env},
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, start_new_session=True)
        session_id, events, last = sid, 0, ""

        async def pump() -> None:
            nonlocal session_id, events, last
            assert proc.stdout is not None
            async for raw in proc.stdout:
                line = raw.decode(errors="replace").strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                events += 1
                session_id = str(ev.get("session_id") or session_id)
                ptype = ev.get("payload_type") or ev.get("type")  # real envelope: payload_type + payload.*
                payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else ev
                text = str(payload.get("text") or payload.get("result") or "")
                if ptype == "run.terminal.completed" and text:
                    last = sanitize_for_log(text)  # authoritative final message
                elif ptype == "run.output.delta" and text:
                    last = sanitize_for_log(last + text)  # streaming chunks append
                elif ptype in ("message", "result") and text:
                    last = sanitize_for_log(text)
                on_event(ev)

        pump_t = asyncio.ensure_future(pump())
        waiters = {pump_t}
        cancel_t = asyncio.ensure_future(cancel.wait()) if cancel else None
        if cancel_t:
            waiters.add(cancel_t)
        try:
            done, _ = await asyncio.wait(waiters, timeout=timeout_s, return_when=asyncio.FIRST_COMPLETED)
            if pump_t not in done:
                await self._kill(proc)
                raise RouvrError(Code.CANCELLED if cancel_t in done else Code.DOWNSTREAM,
                                 "muse turn cancelled" if cancel_t in done else "muse turn timed out",
                                 details={"session_id": session_id})
            rc = await proc.wait()
        finally:
            if cancel_t:
                cancel_t.cancel()
        if rc != 0:
            err = sanitize_for_log((await proc.stderr.read()).decode(errors="replace")) if proc.stderr else ""
            raise RouvrError(Code.DOWNSTREAM, f"muse exit {rc}: {err}", details={"session_id": session_id})
        if not session_id:
            raise RouvrError(Code.CONTRACT, "muse produced no session_id")
        return MuseTurn(session_id=session_id, exit_code=rc, events=events, last_message=last)

    async def _kill(self, proc: asyncio.subprocess.Process) -> None:
        if proc.returncode is not None:
            return
        os.killpg(proc.pid, signal.SIGTERM)
        try:
            await asyncio.wait_for(proc.wait(), self.grace)
        except asyncio.TimeoutError:
            os.killpg(proc.pid, signal.SIGKILL)
            await proc.wait()


def criterion_lines(criteria: list) -> list[str]:
    """Render canonical {id, text} criteria; bare strings pass through (unit fixtures)."""
    return [f"- {c['id']}: {c['text']}" if isinstance(c, dict) else f"- {c}" for c in criteria]


def build_task_prompt(task: dict, spec_repo: str, branch: str, rework_notes: str) -> str:
    parts = [f"Task {task.get('number', task.get('id'))}: {task.get('title', '')}", "",
             str(task.get("description") or ""), "", "Acceptance criteria:"]
    parts += criterion_lines(task.get("acceptance_criteria") or [])
    parts += ["", f"Repository {spec_repo}, work on branch {branch}. Commit and push; do not merge.",
              "Run the project's tests before finishing.",
              'When done, write submit.json in the workspace root: {"pr_url": "<PR url>", '
              '"claims": [{"criterion_id": "<id>"}, ...]} listing EVERY criterion id above — no submission, no credit.']
    if rework_notes:
        parts += ["", "Reviewer requested changes:", rework_notes]
    return "\n".join(parts)


VERDICT_FILE = "verdict.json"
VERDICTS = ("approve", "request_changes", "reject")


def build_verifier_prompt(task: dict, diff: str, tests_summary: str) -> str:
    """Criteria + diff + R8 summary. The builder's report is never included."""
    parts = [f"Review task {task.get('number', task.get('id'))}: {task.get('title', '')}", "",
             "Acceptance criteria:"]
    parts += criterion_lines(task.get("acceptance_criteria") or [])
    parts += ["", f"Test runs: {tests_summary}", "", "PR diff:", diff[:12000],
              "", 'Write verdict.json in the working directory: {"verdict": "approve|request_changes|reject",',
              '"notes": "<one paragraph>"}. Approve only if every criterion is met by the diff and tests.']
    return "\n".join(parts)


def read_verdict(cwd: Path) -> tuple[str, str]:
    """(verdict, notes). Raises CONTRACT when the verifier output is unusable (fail closed)."""
    path = cwd / VERDICT_FILE
    if not path.exists():
        raise RouvrError(Code.CONTRACT, "verifier wrote no verdict.json")
    try:
        data = json.loads(path.read_text())
    except ValueError as e:
        raise RouvrError(Code.CONTRACT, "verdict.json is not valid JSON") from e
    if not isinstance(data, dict) or data.get("verdict") not in VERDICTS:
        raise RouvrError(Code.CONTRACT, "verdict.json needs verdict approve|request_changes|reject")
    return str(data["verdict"]), str(data.get("notes") or "")
