#!/usr/bin/env python3
"""Headless model adapter: one surface for codex exec / claude -p.

The canary runner drives models headlessly (build and review jobs) with
no TTY. This module is the only place that spawns a model CLI: callers
name the backend explicitly per job (no model selection), and every run
returns a structured result record under a time box and token budget.

Contract per run:
  - no TTY: stdin is DEVNULL, stdout goes to a file, no pty is ever
    allocated;
  - credential-free: the child inherits only the T1 allowlist
    environment (PATH/LANG/LC_ALL/TZ/SystemRoot plus synthesized
    HOME/USER/LOGNAME/CODEX_HOME); any secret-named entry is dropped,
    and prompt text never enters argv logs or evidence (only a prompt
    sha256 is recorded);
  - structured JSON result: stdout must contain a fenced ```<fence>
    JSON object (default fence "result"); anything else fails closed
    with evidence (sha256, byte count, bounded tail, return code);
  - time box: the child runs in its own process group; on expiry the
    whole group is SIGKILLed and the run fails closed with evidence;
  - token budget: an estimated output token count beyond max_tokens
    fails closed with evidence. Codex final_response_file mode counts and
    parses only --output-last-message bytes, retaining the transcript hash.

This module never touches the loop, dispatcher, queue or WorkHub: it
runs one CLI and reports one record.
"""

from __future__ import annotations

import hashlib
import json
import os
import pwd
import re
import signal
import subprocess
import time
from pathlib import Path

RESULT_SCHEMA = "CanaryModelResultV1"
DEFAULT_FENCE = "result"
BACKENDS = ("codex", "claude")

SECRET_NAME_RE = re.compile(
    r"token|secret|password|credential|authorization|jwt|private[_-]?key"
    r"|api[_-]?key|auth[_-]?token",
    re.IGNORECASE,
)
_ALLOWED_ENV_NAMES = ("PATH", "LANG", "LC_ALL", "TZ", "SystemRoot")
OUTPUT_TAIL_CHARS = 2000
_TOKEN_CHARS_PER_TOKEN = 4
_POLL_INTERVAL_S = 0.05


class AdapterError(RuntimeError):
    """Refused adapter invocation: fail closed before any spawn."""


def credential_free_env(base=None):
    """T1 allowlist environment for a supervised model child process.

    Only the fixed allowlist passes through (plus synthesized account
    fields); any secret-named entry is dropped. Mirrors the pinned
    worker adapter so the same rule holds at this boundary.
    """
    source = os.environ if base is None else base
    clean = {
        name: source[name]
        for name in _ALLOWED_ENV_NAMES
        if name in source and not SECRET_NAME_RE.search(name)
    }
    account = pwd.getpwuid(os.geteuid())
    clean.update(
        {
            "HOME": account.pw_dir,
            "USER": account.pw_name,
            "LOGNAME": account.pw_name,
            "CODEX_HOME": str(Path(account.pw_dir) / ".codex"),
        }
    )
    clean.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
    return {k: v for k, v in clean.items() if not SECRET_NAME_RE.search(k)}


def build_argv(backend, prompt, *, network_access=False, final_response_path=None):
    """Exact CLI argv for one backend. No selection: caller names it."""
    if not isinstance(prompt, str) or not prompt.strip():
        raise AdapterError("model prompt must be a non-empty string")
    if type(network_access) is not bool:
        raise AdapterError("network_access must be a boolean")
    if backend != "codex" and (network_access or final_response_path is not None):
        raise AdapterError("explicit network/final response options require codex")
    if backend == "codex":
        argv = [
            "codex",
            "exec",
            "-s",
            "workspace-write" if network_access else "read-only",
            "--skip-git-repo-check",
        ]
        if network_access:
            argv += ["-c", "sandbox_workspace_write.network_access=true"]
        if final_response_path is not None:
            argv += ["--output-last-message", str(final_response_path)]
        return argv + [prompt]
    if backend == "claude":
        return ["claude", "-p", "--output-format", "json", prompt]
    raise AdapterError(f"unknown model backend {backend!r}")


def sanitized_command(argv):
    """Evidence-safe command shape: flags kept, prompt hashed, never raw."""
    digest = hashlib.sha256(argv[-1].encode("utf-8")).hexdigest()
    return {"argv": list(argv[:-1]), "prompt_sha256": digest}


def parse_result(text, *, fence=DEFAULT_FENCE):
    """Last fenced JSON object, or None when the contract is unmet."""
    if (
        not isinstance(fence, str)
        or not fence
        or not re.fullmatch(r"[A-Za-z0-9_-]+", fence)
    ):
        raise AdapterError("result fence must be a bare word")
    matches = re.findall(r"```" + fence + r"\s*(\{.*?\})\s*```", text, re.DOTALL)
    if not matches:
        return None
    try:
        parsed = json.loads(matches[-1])
    except ValueError:
        return None
    if not isinstance(parsed, dict):
        return None
    if fence == DEFAULT_FENCE:
        status = parsed.get("status")
        if not isinstance(status, str) or not status.strip():
            return None
    return parsed


def estimate_output_tokens(text):
    """Conservative output-token estimate: ceil(chars / 4)."""
    return (len(text) + _TOKEN_CHARS_PER_TOKEN - 1) // _TOKEN_CHARS_PER_TOKEN


def _kill_process_group(proc):
    """SIGKILL the whole child process group; True when killpg landed."""
    try:
        pgid = os.getpgid(proc.pid)
    except OSError:
        try:
            proc.kill()
        except OSError:
            pass
        return False
    try:
        os.killpg(pgid, signal.SIGKILL)
        return True
    except OSError:
        try:
            proc.kill()
        except OSError:
            pass
        return False


def run_headless_model(
    backend,
    prompt,
    *,
    cwd,
    timeout_s,
    max_tokens,
    fence=DEFAULT_FENCE,
    env=None,
    log_path,
    popen=subprocess.Popen,
    clock=time.monotonic,
    network_access=False,
    final_response_file=False,
    message_queue=None,
    session_id=None,
    queue_deliver=None,
    queue_reconcile=None,
    queue_locate=None,
):
    """Run one turn; at its boundary, drain the session queue if given.

    With ``message_queue`` (a muse_session_queue.MuseSessionQueue) and
    ``session_id``, the session is marked busy for the turn's duration
    so concurrent senders queue with ``external_agent_ingress_closed``
    instead of being lost. At the turn boundary the queue is drained
    in order through ``queue_deliver`` (with ``queue_reconcile``
    settling rows stranded by a dead attempt and ``queue_locate``
    recovering started jobs that never persisted a handle) and the
    delivery receipts are attached as ``queue_receipts``. Without a
    queue the record shape is unchanged.
    """
    if message_queue is None:
        return _run_headless_model_impl(
            backend, prompt, cwd=cwd, timeout_s=timeout_s,
            max_tokens=max_tokens, fence=fence, env=env, log_path=log_path,
            popen=popen, clock=clock, network_access=network_access,
            final_response_file=final_response_file)
    if not isinstance(session_id, str) or not session_id.strip():
        raise AdapterError("session_id is required with a message queue")
    message_queue.set_turn_active(session_id, True)
    try:
        record = _run_headless_model_impl(
            backend, prompt, cwd=cwd, timeout_s=timeout_s,
            max_tokens=max_tokens, fence=fence, env=env, log_path=log_path,
            popen=popen, clock=clock, network_access=network_access,
            final_response_file=final_response_file)
    finally:
        if queue_deliver is None:
            message_queue.set_turn_active(session_id, False)
            receipts = []
        else:
            receipts = message_queue.on_turn_end(
                session_id, queue_deliver, reconcile=queue_reconcile,
                locate=queue_locate)
    record["queue_receipts"] = receipts
    return record


def _run_headless_model_impl(
    backend,
    prompt,
    *,
    cwd,
    timeout_s,
    max_tokens,
    fence=DEFAULT_FENCE,
    env=None,
    log_path,
    popen=subprocess.Popen,
    clock=time.monotonic,
    network_access=False,
    final_response_file=False,
):
    """Run one headless model CLI; always return a result record.

    Invalid construction (unknown backend, empty prompt, non-positive
    time box or budget, missing workdir) raises AdapterError before any
    spawn. Runtime outcomes return records: status "succeeded" carries
    the parsed "result"; status "failed" carries a "reason" (timeout,
    nonzero-exit, token-budget-exceeded, malformed-output,
    spawn-refused) plus an "evidence" mapping.
    """
    if backend not in BACKENDS:
        raise AdapterError(f"unknown model backend {backend!r}")
    if not isinstance(prompt, str) or not prompt.strip():
        raise AdapterError("model prompt must be a non-empty string")
    try:
        timeout = float(timeout_s)
    except (TypeError, ValueError):
        raise AdapterError("time box must be a positive number")
    if not timeout > 0:
        raise AdapterError("time box must be a positive number")
    try:
        budget = int(max_tokens)
    except (TypeError, ValueError):
        raise AdapterError("token budget must be a positive integer")
    if budget <= 0:
        raise AdapterError("token budget must be a positive integer")
    workdir = Path(cwd)
    if not workdir.is_dir():
        raise AdapterError("model workdir is not a directory")
    if log_path is None:
        raise AdapterError("model log path is required")

    final_path = Path(log_path).resolve().with_suffix(".final.txt") if final_response_file else None
    if final_path is not None and (final_path.exists() or final_path.is_symlink()):
        raise AdapterError("final response path already exists; use a new invocation")
    argv = build_argv(backend, prompt, network_access=network_access,
                      final_response_path=final_path)
    # The T1 allowlist applies to caller-supplied env too: only fixed
    # names pass through, secret-named entries are dropped, and account
    # fields are synthesized. No caller can widen the child env.
    child_env = credential_free_env(os.environ if env is None else env)
    command = sanitized_command(argv)
    log_file = Path(log_path)
    log_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(log_file, "wb") as log:
            proc = popen(
                argv,
                cwd=str(workdir),
                env=child_env,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                close_fds=True,
                start_new_session=True,
            )
            pid = proc.pid
            deadline = clock() + timeout
            timed_out = False
            group_killed = False
            while proc.poll() is None:
                if clock() >= deadline:
                    timed_out = True
                    group_killed = _kill_process_group(proc)
                    break
                time.sleep(_POLL_INTERVAL_S)
            returncode = proc.wait()
    except OSError as exc:
        return {
            "status": "failed",
            "backend": backend,
            "reason": "spawn-refused",
            "evidence": {
                "backend": backend,
                **command,
                "error": str(exc)[:160],
            },
        }
    try:
        raw = log_file.read_bytes()
    except OSError:
        raw = b""
    output = raw.decode("utf-8", errors="replace")
    final_raw = None
    if final_path is not None:
        try:
            final_raw = final_path.read_bytes()
        except FileNotFoundError:
            final_raw = b""
        output = final_raw.decode("utf-8", errors="replace")
    evidence = {
        "backend": backend,
        **command,
        "cwd": str(workdir),
        "output_sha256": hashlib.sha256(raw).hexdigest(),
        "output_bytes": len(raw),
        "output_tokens_estimate": estimate_output_tokens(output),
        "output_tail": output[-OUTPUT_TAIL_CHARS:],
        "returncode": returncode,
        "timed_out": timed_out,
        "process_group_killed": group_killed,
        "timeout_s": timeout,
        "max_tokens": budget,
        "pid": pid,
        "network_access": network_access,
        "budget_source": "final_response" if final_path is not None else "transcript",
        "final_response_sha256": (hashlib.sha256(final_raw).hexdigest()
                                  if final_raw is not None else None),
        "final_response_bytes": len(final_raw) if final_raw is not None else None,
    }
    if timed_out:
        return {
            "status": "failed",
            "backend": backend,
            "reason": "timeout",
            "pid": pid,
            "evidence": evidence,
        }
    if returncode != 0:
        return {
            "status": "failed",
            "backend": backend,
            "reason": "nonzero-exit",
            "pid": pid,
            "evidence": evidence,
        }
    if estimate_output_tokens(output) > budget:
        return {
            "status": "failed",
            "backend": backend,
            "reason": "token-budget-exceeded",
            "pid": pid,
            "evidence": evidence,
        }
    parsed = parse_result(output, fence=fence)
    if parsed is None:
        return {
            "status": "failed",
            "backend": backend,
            "reason": "malformed-output",
            "pid": pid,
            "evidence": evidence,
        }
    return {
        "status": "succeeded",
        "backend": backend,
        "pid": pid,
        "result": parsed,
        "evidence": evidence,
    }
