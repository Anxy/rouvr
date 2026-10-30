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


def sessions_root() -> Path:
    xdg = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(xdg) / "muse" / "sessions"


def discover_session_id(cwd: Path, root: Path | None = None) -> str:
    """Newest muse session whose log names cwd as workspace_root, else ''.

    Interactive muse mints its own UUID; the session store
    (~/.local/share/muse/sessions/YYYY/MM/DD/<uuid>/session.jsonl) is the
    only place it is observable, and it is the address native
    `muse session-message send --target` needs."""
    root = root or sessions_root()
    if not root.exists():
        return ""
    needle = str(cwd).encode()  # logs escape quotes; the raw path is the stable marker
    days = sorted(root.glob("*/*/*"), reverse=True)[:2]
    candidates = sorted((d for day in days for d in day.iterdir() if d.is_dir()),
                        key=lambda d: d.stat().st_mtime, reverse=True)
    for d in candidates[:12]:
        log = d / "session.jsonl"
        try:
            with log.open("rb") as fh:
                if needle in fh.read(2_000_000):
                    return d.name
        except OSError:
            continue
    return ""


def persona_role(prompt: str) -> str:
    head = prompt[:600]
    return "Reviewer" if "Persona: Reviewer" in head else "Coder" if "Persona: Coder" in head else "Agent"


def ready_line(prompt: str) -> str:
    """Startup greeting a persona window shows first, like the supervisor's."""
    import re
    role = persona_role(prompt)
    m = re.search(r"(?:Review task|Task) (\d+): ([^\n]+)", prompt)
    if m:
        verb = "Reviewing" if role == "Reviewer" else "Working on"
        return f"{role} ready. {verb} task {m.group(1)}: {m.group(2)[:80]}"
    return f"{role} ready."


def chat_command(title: str, cwd: Path, prompt_file: Path, model: str,
                 bin_path: str = "muse", role: str = "Agent") -> list[str] | None:
    """Argv opening an interactive muse chat session in a new terminal window.

    Returns None when no display/terminal is available (caller falls back to
    headless exec). The chat is seeded with a short pointer to the prompt file
    (full prompts/diffs are too long for argv) and runs with the real HOME so
    the user's muse login applies. Completion = the user finishes the task
    in chat and exits; the workflow then reads submit.json/verdict.json."""
    import shlex
    import shutil
    if not os.environ.get("DISPLAY"):
        return None
    # Inline received messages into the seed: the seed is the first user
    # message shown in the window, so peers' messages are visible there
    # deterministically (no reliance on the model echoing them).
    shown = []
    try:
        lines = prompt_file.read_text(errors="ignore").splitlines()
        in_msgs = False
        for ln in lines:
            if ln.startswith("Messages from "):
                in_msgs = True
                continue
            if in_msgs:
                if ln.startswith("- ") and ": " in ln[2:]:
                    sender, text = ln[2:].split(": ", 1)
                    shown.append(f"Message from {sender} : {text}")
                elif ln.strip():
                    break
    except OSError:
        pass
    seed = (f"You are the Rouvr {role}. Read {prompt_file} now and follow it for the whole session. "
            f"The full stack is already running: WorkHub, Temporal workers, and the submit gate. "
            f"Your window closes automatically once your output file is written. "
            f"Greet the supervisor briefly as the {role.lower()} ready for this task. "
            f"If the instructions list messages from other agents, display each one in your "
            f"first reply as 'Message from <sender> : <text>' so it is visible in this window. "
            f"When you send a message, first say it here as a plain chat message "
            f"(normal assistant text, never only inside a JSON file) as "
            f"'Message to <recipient> : <text>'. Then complete the task.")
    if shown:
        seed += " Messages shown in this window: " + " | ".join(shown)[:600]
    # --yolo: no trust prompt, no approval/continue dialogs — the persona
    # window starts and runs unattended in its governed workspace.
    muse = f"{shlex.quote(bin_path)} --yolo --workspace {shlex.quote(str(cwd))}"
    if model:
        muse += f" --model {shlex.quote(model)}"
    muse += f" {shlex.quote(seed)}"
    script = f"cd {shlex.quote(str(cwd))} && exec {muse}"
    for term, args in (("gnome-terminal", ["--wait", "--title", title, "--", "bash", "-c", script]),
                       ("kitty", ["--title", title, "bash", "-c", script]),
                       ("xterm", ["-title", title, "-e", "bash", "-c", script])):
        if shutil.which(term):
            return [term, *args]
    return None


class MuseLauncher:
    def __init__(self, bin_path: str = "muse", kill_grace_s: float = 10.0,
                 launch_windows: bool = False) -> None:
        self.bin, self.grace, self.launch_windows = bin_path, kill_grace_s, launch_windows

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
        if self.launch_windows:
            chat = chat_command(f"Rouvr muse {sid[:8]}", cwd, prompt_file, model, self.bin,
                                role=persona_role(prompt))
            if chat is not None:
                return await self._run_chat(chat, sid, cwd, env, timeout_s, cancel)
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

    async def _run_chat(self, chat: list[str], sid: str, cwd: Path, env: dict[str, str],
                        timeout_s: int, cancel: asyncio.Event | None) -> MuseTurn:
        """Interactive window mode: a human-visible muse chat does the turn.

        shortcut: interactive muse cannot take our minted --session-id, so a
        rework turn opens a fresh chat whose prompt carries the reviewer notes
        (workspace + branch persist context). Upgrade when muse gains a
        session-id flag for interactive starts."""
        proc = await asyncio.create_subprocess_exec(
            *chat, cwd=str(cwd), env={**os.environ, **env},
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True)
        # Native-messaging address: discover the chat's real session UUID.
        for _ in range(30):
            if proc.returncode is not None:
                break
            found = discover_session_id(cwd)
            if found:
                sid = found
                break
            await asyncio.sleep(2)
        # Automatic completion: the turn is done when its output file lands
        # (submit.json / verdict.json); close the window so the workflow
        # proceeds without any human interaction.
        async def _watch_output() -> None:
            while proc.returncode is None:
                if (cwd / "submit.json").exists() or (cwd / "verdict.json").exists():
                    await asyncio.sleep(15)  # grace: let the agent finish its final reply
                    if proc.returncode is None:
                        await self._kill(proc)
                    return
                await asyncio.sleep(5)

        watcher = asyncio.ensure_future(_watch_output())
        waiters = {asyncio.ensure_future(proc.wait())}
        cancel_t = asyncio.ensure_future(cancel.wait()) if cancel else None
        if cancel_t:
            waiters.add(cancel_t)
        try:
            done, _ = await asyncio.wait(waiters, timeout=timeout_s, return_when=asyncio.FIRST_COMPLETED)
            if proc.returncode is None:
                await self._kill(proc)
                raise RouvrError(Code.CANCELLED if cancel_t in done else Code.DOWNSTREAM,
                                 "muse chat cancelled" if cancel_t in done else "muse chat timed out",
                                 details={"session_id": sid})
        finally:
            watcher.cancel()
            if cancel_t:
                cancel_t.cancel()
        return MuseTurn(session_id=sid, exit_code=0, events=0, last_message="")

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


def build_task_prompt(task: dict, spec_repo: str, branch: str, rework_notes: str,
                      peer_messages: list[str] | None = None) -> str:
    parts = [f"Task {task.get('number', task.get('id'))}: {task.get('title', '')}", "",
             str(task.get("description") or ""), "", "Acceptance criteria:"]
    parts += criterion_lines(task.get("acceptance_criteria") or [])
    parts += ["", f"Repository {spec_repo}, work on branch {branch}. Commit locally; do NOT push and "
              "do not use any git credential — the supervisor grants one and the system pushes; do not merge.",
              "Run the project's tests before finishing.",
              'When done, write submit.json in the workspace root: {"pr_url": "", '
              '"claims": [{"criterion_id": "<id>"}, ...]} listing EVERY criterion id above — no submission, no credit. '
              'Leave pr_url empty: the system opens the PR after your turn and fills it in.',
              'If you need clarification or must flag something for the reviewer, also write '
              'message-reviewer.json in the workspace root: {"text": "<message>"}. '
              'Before writing any file, say it as a plain chat message (normal text, not JSON): Message to reviewer : <message>']
    if peer_messages:
        parts += ["", "Messages from other agents — show each in this chat as "
                  "'Message from <sender> : <text>' as plain chat text before starting work:"] + \
                 [f"- {m}" for m in peer_messages]
    if rework_notes:
        parts += ["", "Reviewer requested changes:", rework_notes]
    return "\n".join(parts)


VERDICT_FILE = "verdict.json"
VERDICTS = ("approve", "request_changes", "reject")


def build_reviewer_prompt(task: dict, diff: str, tests_summary: str,
                          peer_messages: list[str] | None = None) -> str:
    """Criteria + diff + R8 summary. The coder's report is never included."""
    parts = [f"Review task {task.get('number', task.get('id'))}: {task.get('title', '')}", "",
             "Acceptance criteria:"]
    parts += criterion_lines(task.get("acceptance_criteria") or [])
    if peer_messages:
        parts += ["", "Messages from the coder — show each in this chat as "
                  "'Message from coder : <text>' as plain chat text before starting work:"] + \
                 [f"- {m}" for m in peer_messages]
    parts += ["", f"Test runs: {tests_summary}", "", "PR diff:", diff[:12000],
              "", 'Write verdict.json in the working directory: {"verdict": "approve|request_changes|reject",',
              '"notes": "<one paragraph>"}. Approve only if every criterion is met by the diff and tests.',
              'To reply to the coder, also write message-coder.json: {"text": "<message>"}. '
              'Before writing any file, say it as a plain chat message (normal text, not JSON): Message to coder : <message>']
    return "\n".join(parts)


def read_verdict(cwd: Path) -> tuple[str, str]:
    """(verdict, notes). Raises CONTRACT when the reviewer output is unusable (fail closed)."""
    path = cwd / VERDICT_FILE
    if not path.exists():
        raise RouvrError(Code.CONTRACT, "reviewer wrote no verdict.json")
    try:
        data = json.loads(path.read_text())
    except ValueError as e:
        raise RouvrError(Code.CONTRACT, "verdict.json is not valid JSON") from e
    if not isinstance(data, dict) or data.get("verdict") not in VERDICTS:
        raise RouvrError(Code.CONTRACT, "verdict.json needs verdict approve|request_changes|reject")
    return str(data["verdict"]), str(data.get("notes") or "")
