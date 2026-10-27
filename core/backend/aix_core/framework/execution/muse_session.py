"""Neutral Muse session/turn transport over the maintained MSP contract.

One fresh process per turn: spawn -> ``initialize``/``initialized`` ->
``permissionProfile/list`` -> ``session/start`` -> ``hook/list`` +
``mcpServer/list`` -> ``turn/start`` -> ``turn/started`` -> view events ->
terminal ``turn/completed`` -> terminate and verify process-group death ->
postmortem residue validation -> after-exit workspace snapshot -> atomic
transcript publication.

The wire shapes follow the maintained schema
(``muse-schema/msp.schema.json``) and the reviewed reference runner
(``runtime_clients.py`` / ``foreground_bridge.py``): UUIDv7 command ids,
top-level ``turnId`` acknowledgement with command correlation and
admission/disposition checks, ``sourceRange``/``viewCursor`` anchored
evidence, ``view/gap`` refusal, joined agent messages, input-echo
verification, approval observation without client decisions, and a
postmortem check of every notification still queued after the readers
join (gaps, late turn activity with per-shape item correlation — nested
item turn for completions/starts/updates, streaming registry for deltas —
a contradictory second terminal, and late stream errors all refuse the
accepted terminal).

Changed-file evidence is observed, not reported: the transport snapshots the
workspace before spawn and diffs it after the process and readers have
exited, using the full descriptor-anchored lstat inventory (see
``muse_workspace_snapshot``). Transcripts are published atomically into a
supervisor-owned evidence directory outside the workspace — never into the
worker-controlled tree — through a directory descriptor that is opened
pre-effect and held for the whole turn, with ownership/mode policy, a
unique no-follow temp per call, and atomic hard-link no-replace
publication. The transcript carries the held directory's trusted
identity bound to the returned reference.

Cancellation owns the worker before the caller returns: the initial await
is shielded, the stop signal is asserted, and the executor thread, owned
process group, and readers are waited out (bounded) before
``CancelledError`` propagates, so the owned process is already dead at
return. An unproven shutdown supersedes the cancellation because the
worker may still be alive. Owned cleanup runs unconditionally across
``BaseException`` paths; the primary refusal keeps its code and carries
the worker shutdown disposition as evidence.

Only a clean worker shutdown (exit 0 after stdin close, no signal sent,
no surviving group members) satisfies the claimed lifecycle: a forced or
nonzero shutdown refuses even with a valid terminal. The latest consumed
view cursor is tracked in arrival order — cursors are opaque and never
sorted.

No synthetic success is ever manufactured. Any non-terminal turn, gap,
late activity, contradictory terminal, malformed frame, unproven
shutdown, incomplete snapshot, or unclean exit raises ``MuseSessionError``
instead of returning a result.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import queue
import secrets
import shutil
import signal
import stat
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

from backend.aix_core.framework.execution.muse_workspace_snapshot import (
    FileChange,
    SnapshotIncomplete,
    WorkspaceSnapshot,
    changes_to_jsonable,
    diff_snapshots,
    snapshot_workspace,
)

# Closed MSP ApprovalMode vocabulary (schema x-msp-openness: closed).
APPROVAL_MODES = frozenset({"allowAll", "promptUnmatched", "onRequest", "denyUnmatched"})

# Non-secret environment for the spawned session (reference parity).
_SAFE_ENV_KEYS = (
    "HOME",
    "PATH",
    "USER",
    "LOGNAME",
    "SHELL",
    "LANG",
    "LC_ALL",
    "TERM",
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
)

_STDERR_MAX_LINES = 100
_STDERR_MAX_CHARS = 1000
# Owned-shutdown bounds: stdin-close grace, SIGTERM grace, SIGKILL reap,
# surviving-group verification, and postmortem reader join. Every wait in
# the owned cleanup path is bounded by one of these.
_EXIT_WAIT_SECONDS = 5.0
_TERM_WAIT_SECONDS = 5.0
_KILL_WAIT_SECONDS = 10.0
_GROUP_DEATH_SECONDS = 5.0
_READER_JOIN_SECONDS = 10.0
_INACTIVITY_DEFAULT_SECONDS = 600.0

_TOOL_KINDS = frozenset({
    "toolCall", "userShell", "webSearch", "subagent", "workflow",
    "reminderChild", "hookRun", "monitorFeed", "compaction", "sideChat",
})


class MuseSessionError(RuntimeError):
    """Explicit transport refusal. ``code`` is stable for caller mapping."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(code if not detail else f"{code}:{detail}")


def uuid7() -> str:
    """Mint a UUIDv7-style id (48-bit unix-ms + ver 7 + variant + 74-bit rand).

    Same bit layout as the reviewed reference generator: time-ordered,
    unique per call. Used for every client command id on the wire.
    """
    ms = int(time.time() * 1000)
    rand = secrets.randbits(74)
    value = (ms & ((1 << 48) - 1)) << 80
    value |= 0x7 << 76
    value |= ((rand >> 62) & 0xFFF) << 64
    value |= 0b10 << 62
    value |= rand & ((1 << 62) - 1)
    import uuid as _uuid

    return str(_uuid.UUID(int=value))


@dataclass(frozen=True, slots=True)
class MuseTurnParams:
    """Everything one bounded Muse turn needs. No credentials by construction."""

    model_id: str
    workspace: Path
    prompt: str
    evidence_dir: Path
    timeout_seconds: float = 300.0
    inactivity_seconds: float = _INACTIVITY_DEFAULT_SECONDS
    approval_mode: str = "denyUnmatched"
    permission_profile: str = ":read-only"
    provider_id: str = "meta"
    reasoning_effort: str = "low"
    token_budget: int | None = None
    is_cancelled: Callable[[], bool] | None = field(default=None, compare=False)


@dataclass(frozen=True, slots=True)
class MuseToolFailure:
    """One turn item that reached a non-completed terminal status."""

    item_id: str
    kind: str
    status: str


@dataclass(frozen=True, slots=True)
class MuseTurnResult:
    """Terminal turn evidence. ``terminal`` is always ``"completed"``.

    A result exists only for a clean worker shutdown (see ``shutdown``):
    a forced or nonzero shutdown refuses instead of returning evidence.
    """

    session_id: str
    turn_id: str
    terminal: str
    agent_text: str
    # Provider-echoed model id from the session object, or "" when absent.
    # Downstream enforcement treats "" as absent, never as a match.
    model_echo: str = ""
    # Observed workspace mutations (after process/readers exited).
    changes: tuple[FileChange, ...] = ()
    # Absolute supervisor-evidence paths written with no-follow semantics.
    evidence_files: tuple[str, ...] = ()
    # Validated MSP TokenUsage mapping, or None when the terminal omits it.
    token_usage: dict[str, Any] | None = None
    turn_record: dict[str, Any] = field(default_factory=dict, compare=False)
    tool_failures: tuple[MuseToolFailure, ...] = ()
    # Observed approval notifications plus solicited server requests. This
    # client declines server-initiated approval requests (-32601) and never
    # resolves an approval itself, so this counts observations, never
    # proven resolved approvals.
    approval_roundtrips: int = 0
    input_echo_observed: bool = False
    # Latest consumed view cursor in arrival order. Cursors are opaque;
    # this is never sorted or parsed to infer position.
    view_cursor_head: str = ""
    duration_ms: int | None = None
    stderr_tail: tuple[str, ...] = ()
    exit_code: int | None = None
    # Worker shutdown disposition at result time: always ``"clean"`` (exit
    # 0 after stdin close, no signal sent, no surviving group members).
    shutdown: str = "clean"


class MuseSessionPort(Protocol):
    """Model-neutral turn boundary: spawn params -> terminal turn evidence."""

    async def run_turn(self, params: MuseTurnParams) -> MuseTurnResult: ...


def normalize_model_id(value: Any) -> str:
    """Normalize a selected model id the way route enforcement does."""
    text = str(value or "").strip().lower()
    return text.removeprefix("openrouter/")


def is_muse_model_id(value: Any) -> bool:
    """True only for the accepted Muse model family.

    Accepts the canonical session-factory name (``Muse``) and provider ids
    whose final path segment is ``muse`` or starts with ``muse-``/``muse_``
    (e.g. ``muse-spark-1.3``, ``meta/muse-spark-1.3``). Sibling lanes
    (``spark-1.3``, ``glimmer``) and foreign providers never match.
    """
    text = normalize_model_id(value)
    if not text:
        return False
    family = text.split("/")[-1]
    return family == "muse" or family.startswith("muse-") or family.startswith("muse_")


def _unlink_at(dfd: int, name: str) -> None:
    """Best-effort removal of failed partial evidence output."""
    try:
        os.unlink(name, dir_fd=dfd)
    except OSError:
        pass


def _validate_evidence_dfd(dfd: int, directory: Path) -> tuple[int, int]:
    """Ownership/mode policy on a held evidence directory descriptor.

    Returns the trusted ``(st_dev, st_ino)`` identity the caller retains
    while the descriptor is held; every later operation resolves relative
    to the descriptor, never by path.
    """
    try:
        st = os.fstat(dfd)
    except OSError as exc:
        raise MuseSessionError(
            "MUSE_EVIDENCE_DIR_INVALID",
            f"cannot stat evidence dir: {directory}: {exc}",
        ) from exc
    if not stat.S_ISDIR(st.st_mode):
        raise MuseSessionError(
            "MUSE_EVIDENCE_DIR_INVALID", f"not a directory: {directory}"
        )
    if st.st_uid != os.geteuid():
        raise MuseSessionError(
            "MUSE_EVIDENCE_DIR_UNOWNED",
            f"evidence dir not owned by the current user: {directory}",
        )
    if st.st_mode & 0o022:
        raise MuseSessionError(
            "MUSE_EVIDENCE_DIR_SHARED",
            f"evidence dir is group/world writable: {directory}",
        )
    return (st.st_dev, st.st_ino)


def _write_evidence_file_at(dfd: int, name: str, payload: bytes) -> str:
    """Publish through a held directory descriptor; returns the final name.

    The temp name is unique per call (pid, thread, random token), so two
    concurrent publishers never share a temp and never delete each
    other's temp. Publication uses hard-link creation, which fails
    atomically when the final name already exists: there is no
    check-then-replace window in which a concurrent same-user publisher
    could have its file replaced. Only this call's own temp is ever
    unlinked.
    """
    safe = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in name)[:64]
    if not safe:
        raise MuseSessionError("MUSE_EVIDENCE_NAME_INVALID", "empty evidence name")
    final = f"{safe}.json"
    tmp = (
        f".{safe}.{os.getpid():d}.{threading.get_ident():x}."
        f"{secrets.token_hex(8)}.tmp"
    )
    tmp_flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW
    try:
        fd = os.open(tmp, tmp_flags, 0o600, dir_fd=dfd)
    except OSError as exc:
        # Never unlink here: O_EXCL failed, so this temp is not ours.
        raise MuseSessionError(
            "MUSE_EVIDENCE_WRITE_FAILED", f"temp create: {exc}"
        ) from exc
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        _unlink_at(dfd, tmp)
        raise MuseSessionError("MUSE_EVIDENCE_WRITE_FAILED", str(exc)) from exc
    try:
        os.link(tmp, final, src_dir_fd=dfd, dst_dir_fd=dfd)
    except FileExistsError as exc:
        _unlink_at(dfd, tmp)
        raise MuseSessionError(
            "MUSE_EVIDENCE_REDIRECTED",
            f"evidence file already present: {final}",
        ) from exc
    except OSError as exc:
        _unlink_at(dfd, tmp)
        raise MuseSessionError(
            "MUSE_EVIDENCE_WRITE_FAILED", f"publish: {exc}"
        ) from exc
    _unlink_at(dfd, tmp)
    try:
        os.fsync(dfd)
    except OSError as exc:
        raise MuseSessionError(
            "MUSE_EVIDENCE_WRITE_FAILED", f"publish dir fsync: {exc}"
        ) from exc
    return final


def write_evidence_file(directory: Path, name: str, payload: bytes) -> Path:
    """Publish one immutable evidence file, atomically, outside worker control.

    One-shot wrapper: opens the directory with ``O_NOFOLLOW |
    O_DIRECTORY``, enforces the ownership/mode policy, publishes through
    the held descriptor, and closes it. Callers that span effects (the
    turn transport) must hold the descriptor themselves across the whole
    effect instead of reopening here; the pre-spawn path check in the
    transport is fail-fast only and this write-time check is authoritative
    for the one-shot path.
    """
    try:
        dfd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as exc:
        raise MuseSessionError(
            "MUSE_EVIDENCE_DIR_INVALID",
            f"not a held real directory: {directory}: {exc}",
        ) from exc
    try:
        _validate_evidence_dfd(dfd, directory)
        final = _write_evidence_file_at(dfd, name, payload)
    finally:
        os.close(dfd)
    return Path(directory) / final


def _strict_json_loads(text: str) -> Any:
    def _reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON object key: {key!r}")
            result[key] = value
        return result

    return json.loads(text, object_pairs_hook=_reject_duplicates)


class _StdioJsonRpc:
    """Line-delimited stdio JSON-RPC with strict framing and cursor registry."""

    def __init__(self, proc: subprocess.Popen[str]) -> None:
        self._proc = proc
        self._next_id = 1
        self._pending: dict[int, queue.Queue[dict[str, Any]]] = {}
        self._pending_lock = threading.Lock()
        self._notifications: queue.Queue[dict[str, Any]] = queue.Queue()
        self._send_lock = threading.Lock()
        self._stream_error: str | None = None
        self._reader_done = threading.Event()
        self._stderr_lines: list[str] = []
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._stderr_reader = threading.Thread(target=self._stderr_loop, daemon=True)
        self._reader.start()
        self._stderr_reader.start()

    def _fail_stream(self, detail: str) -> None:
        if self._stream_error is None:
            self._stream_error = detail
        self._reader_done.set()

    def _read_loop(self) -> None:
        try:
            stdout = self._proc.stdout
            if stdout is None:
                self._fail_stream("stdout unavailable")
                return
            for line in stdout:
                if not line.strip():
                    continue
                try:
                    obj = _strict_json_loads(line)
                except ValueError as exc:
                    self._fail_stream(f"malformed frame: {exc}")
                    return
                if not isinstance(obj, dict):
                    self._fail_stream("non-object frame")
                    return
                self._dispatch(obj)
        except Exception as exc:  # noqa: BLE001 - retained for shutdown refusal
            self._fail_stream(f"{type(exc).__name__}: {exc}")
        finally:
            self._reader_done.set()

    def _stderr_loop(self) -> None:
        try:
            stderr = self._proc.stderr
            if stderr is None:
                return
            for line in stderr:
                if len(self._stderr_lines) < _STDERR_MAX_LINES:
                    self._stderr_lines.append(line.rstrip()[:_STDERR_MAX_CHARS])
        except Exception:  # noqa: BLE001 - stderr diagnostics are best-effort
            pass

    def _dispatch(self, obj: dict[str, Any]) -> None:
        if "id" in obj and "method" not in obj:
            with self._pending_lock:
                waiter = self._pending.get(obj["id"])
            if waiter is not None:
                waiter.put(obj)
            return
        if "method" in obj and "id" not in obj:
            self._notifications.put(obj)
            return
        if "method" in obj:
            # Server-initiated request (e.g. approval solicitation): decline
            # with method-not-handled and deliver it for observation. This
            # client never resolves approvals itself.
            try:
                self._send({
                    "jsonrpc": "2.0", "id": obj["id"],
                    "error": {"code": -32601, "message": "method not handled"},
                })
            except OSError:
                pass
            self._notifications.put(obj)

    def _send(self, obj: dict[str, Any]) -> None:
        stdin = self._proc.stdin
        if stdin is None or stdin.closed:
            raise MuseSessionError("MUSE_TRANSPORT_CLOSED", "session stdin is gone")
        try:
            with self._send_lock:
                stdin.write(json.dumps(obj, separators=(",", ":")) + "\n")
                stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise MuseSessionError(
                "MUSE_TRANSPORT_CLOSED", f"session pipe broke: {exc}"
            ) from exc

    def stream_error(self) -> str | None:
        return self._stream_error

    def reader_done(self) -> bool:
        return self._reader_done.is_set()

    def stderr_tail(self) -> tuple[str, ...]:
        return tuple(self._stderr_lines)

    def join_readers(self, timeout: float) -> bool:
        self._reader.join(timeout=timeout)
        self._stderr_reader.join(timeout=max(0.0, timeout))
        return not self._reader.is_alive() and not self._stderr_reader.is_alive()

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        """Send a client-to-server notification (no id, no reply)."""
        obj: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            obj["params"] = params
        self._send(obj)

    def get_notification(self, timeout: float) -> dict[str, Any] | None:
        try:
            return self._notifications.get(timeout=timeout)
        except queue.Empty:
            return None

    def has_pending(self) -> bool:
        """True when undrained notifications are queued.

        Only consulted together with ``reader_done()``: once the reader
        thread has ended no further frame can arrive, so a non-empty queue
        at that point is conclusive (unlike a draining check, this consumes
        nothing, so an accepted terminal can never be discarded by it).
        """
        return not self._notifications.empty()

    def restash(self, note: dict[str, Any]) -> None:
        """Return a held notification to the queue (turn-launch boundary)."""
        self._notifications.put(note)

    def drain_notifications(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        while True:
            try:
                out.append(self._notifications.get_nowait())
            except queue.Empty:
                return out

    def request(
        self,
        method: str,
        params: dict[str, Any],
        timeout: float,
        stop: threading.Event,
    ) -> Any:
        rid = self._next_id
        self._next_id += 1
        waiter: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)
        with self._pending_lock:
            self._pending[rid] = waiter
        try:
            self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
            deadline = time.monotonic() + timeout
            while True:
                if stop.is_set():
                    raise MuseSessionError("CANCELLED", f"cancelled during {method}")
                if self._stream_error is not None:
                    raise MuseSessionError("MUSE_STREAM_MALFORMED", self._stream_error)
                if self._reader_done.is_set() and waiter.empty():
                    raise MuseSessionError(
                        "MUSE_TRANSPORT_CLOSED",
                        f"{method}: transport closed with no response",
                    )
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise MuseSessionError(
                        "MUSE_REQUEST_TIMEOUT", f"{method} produced no response"
                    )
                try:
                    obj = waiter.get(timeout=min(0.1, remaining))
                except queue.Empty:
                    continue
                if not isinstance(obj, dict):
                    raise MuseSessionError("MUSE_RESPONSE_MALFORMED", method)
                if "error" in obj:
                    raise MuseSessionError("MUSE_REQUEST_ERROR", f"{method}: {obj['error']}")
                return obj.get("result")
        finally:
            with self._pending_lock:
                self._pending.pop(rid, None)


_SESSION_START_REQUIRED = (
    "commandId", "workspaceRoot", "workspaceRoots",
    "providerId", "modelId", "approvalMode", "permissionProfile",
)
_TURN_START_REQUIRED = ("commandId", "sessionId", "input", "ifBusy")


def _require_params(method: str, params: dict[str, Any]) -> None:
    required = (
        _SESSION_START_REQUIRED if method == "session/start"
        else _TURN_START_REQUIRED if method == "turn/start"
        else ()
    )
    missing = [key for key in required if key not in params]
    if missing:
        raise MuseSessionError(
            "MUSE_PARAM_SHAPE_INVALID", f"{method} missing {sorted(missing)}"
        )


@dataclass(slots=True)
class _TurnTracker:
    session_id: str
    turn_id: str
    cursors: set[str] = field(default_factory=set)
    # Latest consumed cursor in arrival order. Cursors are opaque: this is
    # assigned on every accepted cursor and never sorted or parsed.
    latest_cursor: str | None = None
    # Streaming item registry: itemId -> nested item.turnId (or None when
    # the sighting carried no turn, e.g. deltas). Postmortem delta
    # correlation (deltas carry only an itemId) resolves through this.
    item_turns: dict[str, Any] = field(default_factory=dict)
    agent_texts: list[str] = field(default_factory=list)
    tool_failures: list[MuseToolFailure] = field(default_factory=list)
    completed_items: dict[str, dict[str, Any]] = field(default_factory=dict)
    input_echoes: list[str] = field(default_factory=list)
    approvals: list[dict[str, Any]] = field(default_factory=list)
    turn_started: bool = False
    turn_started_source: dict[str, Any] = field(default_factory=dict)
    terminal_record: dict[str, Any] = field(default_factory=dict)
    method_counts: dict[str, int] = field(default_factory=dict)
    handshake: dict[str, Any] = field(default_factory=dict)
    ack: dict[str, Any] = field(default_factory=dict)
    last_activity: float = field(default_factory=time.monotonic)

    def note_cursor(self, params: dict[str, Any], *, required: bool, context: str) -> str:
        cursor = params.get("viewCursor")
        if not isinstance(cursor, str) or not cursor:
            if required:
                raise MuseSessionError(
                    "MUSE_EVENT_MALFORMED", f"{context}: missing viewCursor"
                )
            return ""
        if cursor in self.cursors:
            raise MuseSessionError(
                "MUSE_VIEW_CURSOR_REUSE", f"{context}: cursor already consumed"
            )
        self.cursors.add(cursor)
        self.latest_cursor = cursor
        return cursor

    def note_source(self, params: dict[str, Any], *, required: bool, context: str) -> Any:
        source = params.get("sourceRange")
        if required and not isinstance(source, dict):
            raise MuseSessionError(
                "MUSE_EVENT_MALFORMED", f"{context}: missing sourceRange"
            )
        return source


class StdioMspSessionPort:
    """Real ``muse serve`` stdio session transport using MSP shapes.

    One fresh process per :meth:`run_turn`, driven through the maintained
    handshake (``initialize``/``initialized``), catalog admission
    (``permissionProfile/list``), ``session/start``, capability posture
    (``hook/list`` + ``mcpServer/list``), ``turn/start`` with a correlated
    top-level ``turnId`` ack, ``turn/started`` launch proof, cursor-anchored
    view events with ``view/gap`` refusal, postmortem residue validation,
    verified process-group death with surviving-group escalation,
    clean-exit acceptance, and a post-exit workspace diff.
    The binary is resolved from ``PATH`` unless ``muse_bin`` names one
    explicitly; an explicit name is authoritative and fails closed when it
    names nothing usable (no fallback to ``PATH``). No absolute checkout
    or credential is ever embedded.
    """

    def __init__(
        self,
        *,
        muse_bin: str | None = None,
        startup_timeout_s: float = 20.0,
        admission_timeout_s: float = 15.0,
    ) -> None:
        explicit = str(muse_bin).strip() if muse_bin is not None else ""
        if explicit:
            # Explicit operator configuration is authoritative: it must name
            # a usable binary and never fall back to PATH discovery.
            # shutil.which() resolves a bare name via PATH and validates a
            # path with a directory component for existence/executability.
            resolved = shutil.which(explicit)
            if not resolved:
                raise MuseSessionError(
                    "MUSE_BIN_NOT_FOUND",
                    f"configured muse binary not found or not executable: {explicit}",
                )
        else:
            resolved = shutil.which("muse")
            if not resolved:
                raise MuseSessionError(
                    "MUSE_BIN_NOT_FOUND", "no muse binary on PATH and none configured"
                )
        self._muse_bin = resolved
        self._startup_timeout_s = startup_timeout_s
        self._admission_timeout_s = admission_timeout_s

    async def run_turn(self, params: MuseTurnParams) -> MuseTurnResult:
        """Run one turn; cancellation owns the worker before returning.

        The initial await is shielded so a task cancel cannot orphan the
        executor thread: the stop signal is asserted and the worker thread,
        owned process group, and readers are waited out (bounded — the sync
        drive polls the stop signal and every cleanup wait is bounded)
        before the ``CancelledError`` propagates, so the owned process is
        already dead at return. An unproven shutdown supersedes the
        cancellation because the worker may still be alive. A turn that
        finished concurrently with the cancel is returned, never discarded.
        """
        stop = threading.Event()
        future = asyncio.get_running_loop().run_in_executor(
            None, self._run_turn_sync, params, stop
        )
        try:
            return await asyncio.shield(future)
        except BaseException as original:
            stop.set()
            while True:
                if future.done():
                    # The worker reached a terminal outcome: consume it
                    # exactly once. A stored CancelledError is the worker's
                    # own outcome (e.g. a sync cancellation callback raised
                    # it), not a fresh cancellation of this wrapper —
                    # re-awaiting a finished future would raise it again
                    # without ever suspending.
                    try:
                        return future.result()
                    except MuseSessionError as worker_error:
                        if worker_error.code == "MUSE_SHUTDOWN_UNPROVEN":
                            raise worker_error from original
                        break
                    except BaseException:
                        break
                try:
                    return await asyncio.shield(future)
                except asyncio.CancelledError:
                    # Cancellation of this wrapper while the worker is
                    # still unfinished: keep waiting out the owned cleanup.
                    continue
                except BaseException as worker_outcome:
                    if (
                        isinstance(worker_outcome, MuseSessionError)
                        and worker_outcome.code == "MUSE_SHUTDOWN_UNPROVEN"
                    ):
                        raise worker_outcome from original
                    break
            raise original

    def _run_turn_sync(self, params: MuseTurnParams, stop: threading.Event) -> MuseTurnResult:
        started_at = time.monotonic()
        self._validate_params(params)
        workspace = Path(params.workspace).resolve(strict=True)
        evidence_dir = Path(params.evidence_dir)
        if not evidence_dir.is_dir() or evidence_dir.is_symlink():
            raise MuseSessionError(
                "MUSE_EVIDENCE_DIR_INVALID", f"not a real directory: {evidence_dir}"
            )
        evidence_resolved = evidence_dir.resolve(strict=True)
        if workspace == evidence_resolved:
            raise MuseSessionError(
                "MUSE_EVIDENCE_DIR_INVALID", "evidence dir must differ from the workspace"
            )
        try:
            workspace.relative_to(evidence_resolved)
        except ValueError:
            pass
        else:
            raise MuseSessionError(
                "MUSE_EVIDENCE_DIR_INVALID", "evidence dir must not contain the workspace"
            )
        try:
            evidence_resolved.relative_to(workspace)
        except ValueError:
            pass
        else:
            raise MuseSessionError(
                "MUSE_EVIDENCE_DIR_INVALID",
                "evidence dir must live outside the worker-controlled workspace",
            )
        try:
            before = snapshot_workspace(workspace)
        except SnapshotIncomplete as exc:
            raise MuseSessionError("MUSE_SNAPSHOT_INCOMPLETE", "; ".join(exc.errors)) from exc
        if stop.is_set() or (params.is_cancelled is not None and params.is_cancelled()):
            raise MuseSessionError("CANCELLED", "cancelled before spawn")
        # The evidence directory is opened BEFORE any effect and held for
        # the whole turn: every later file operation resolves relative to
        # this descriptor, so replacing an ancestor after validation
        # cannot redirect publication. Its (dev, ino) identity is recorded
        # and travels into the transcript. Residual window: the path-based
        # containment check above and this open are adjacent with no
        # intervening I/O, but a swap inside that window (requiring
        # ancestor-write capability outside the worker's reach) would still
        # select the opened directory — the descriptor, not the path, is
        # authoritative from here on.
        try:
            evidence_dfd = os.open(
                evidence_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            )
        except OSError as exc:
            raise MuseSessionError(
                "MUSE_EVIDENCE_DIR_INVALID",
                f"not a held real directory: {evidence_dir}: {exc}",
            ) from exc
        try:
            dir_identity = _validate_evidence_dfd(evidence_dfd, evidence_dir)
            return self._run_turn_held(
                params, stop, workspace, evidence_dir, evidence_dfd,
                dir_identity, before, started_at,
            )
        finally:
            os.close(evidence_dfd)

    def _run_turn_held(
        self,
        params: MuseTurnParams,
        stop: threading.Event,
        workspace: Path,
        evidence_dir: Path,
        evidence_dfd: int,
        dir_identity: tuple[int, int],
        before: WorkspaceSnapshot,
        started_at: float,
    ) -> MuseTurnResult:
        """Spawn and drive one turn while holding the evidence directory."""
        command = [
            self._muse_bin, "serve",
            "--provider", params.provider_id,
            "--model", params.model_id.strip(),
            "--contributor-mode", "on",
            "--disable-request-user-input",
            "--sandbox-network", "restricted",
            "--trust-workspace",
        ]
        env = {key: os.environ[key] for key in _SAFE_ENV_KEYS if key in os.environ}
        try:
            proc = subprocess.Popen(
                command,
                cwd=str(workspace),
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                start_new_session=True,
            )
        except OSError as exc:
            raise MuseSessionError("MUSE_SPAWN_FAILED", str(exc)) from exc
        rpc = _StdioJsonRpc(proc)
        tracker = _TurnTracker(session_id="", turn_id="")
        drive_failure: BaseException | None = None
        terminal = "completed"
        turn_record: dict[str, Any] = {}
        try:
            session_id, session_echo, handshake = self._handshake(
                rpc, params, workspace, stop, tracker
            )
            tracker.session_id = session_id
            turn_id, ack = self._start_turn(rpc, params, session_id, stop, tracker)
            tracker.turn_id = turn_id
            tracker.handshake = handshake
            tracker.ack = ack
            terminal, turn_record = self._await_terminal(
                rpc, params, stop, tracker, turn_id
            )
        except BaseException as exc:
            drive_failure = exc
        # Unconditional owned cleanup across BaseException paths: stdin
        # close, bounded signal escalation, surviving-group kill and
        # verification, reader join. An unproven shutdown propagates here
        # (chaining the drive outcome as context) and supersedes it.
        exit_code, stderr_tail, shutdown = self._terminate(proc, rpc)
        if isinstance(drive_failure, MuseSessionError):
            note = f"worker shutdown={shutdown} exit={exit_code}"
            detail = f"{drive_failure.detail}; {note}" if drive_failure.detail else note
            raise MuseSessionError(drive_failure.code, detail) from drive_failure
        if drive_failure is not None:
            raise drive_failure
        # The worker is dead and the readers are joined, so no new frame
        # can arrive: every notification still queued is postmortem residue
        # and is validated before any success is recorded.
        self._check_postmortem(rpc, tracker, turn_id, turn_record)
        try:
            after = snapshot_workspace(workspace)
        except SnapshotIncomplete as exc:
            raise MuseSessionError("MUSE_SNAPSHOT_INCOMPLETE", "; ".join(exc.errors)) from exc
        changes = diff_snapshots(before, after)
        if exit_code != 0 or shutdown != "clean":
            raise MuseSessionError(
                "MUSE_UNCLEAN_EXIT",
                f"valid terminal but worker shutdown={shutdown} exit={exit_code}; "
                f"workspace_changes={len(changes)}",
            )
        outcome = self._transcript_pending(
            tracker=tracker, terminal=terminal, turn_record=turn_record,
            model_echo=session_echo, shutdown=shutdown,
        )
        transcript_path = self._write_transcript(
            evidence_dfd, evidence_dir, dir_identity,
            outcome, changes, exit_code, stderr_tail, shutdown,
            context={
                "handshake": tracker.handshake,
                "ack": tracker.ack,
                "turn_started": tracker.turn_started_source,
                "method_counts": dict(tracker.method_counts),
                "view_cursors_consumed": len(tracker.cursors),
                "prompt_sha256": hashlib.sha256(params.prompt.encode("utf-8")).hexdigest(),
            },
            started_at=started_at,
        )
        return MuseTurnResult(
            session_id=outcome.session_id,
            turn_id=outcome.turn_id,
            terminal=outcome.terminal,
            agent_text=outcome.agent_text,
            model_echo=outcome.model_echo,
            changes=changes,
            evidence_files=(transcript_path,),
            token_usage=outcome.token_usage,
            turn_record=outcome.turn_record,
            tool_failures=outcome.tool_failures,
            approval_roundtrips=outcome.approval_roundtrips,
            input_echo_observed=outcome.input_echo_observed,
            view_cursor_head=outcome.view_cursor_head,
            duration_ms=outcome.duration_ms,
            stderr_tail=stderr_tail,
            exit_code=exit_code,
            shutdown=shutdown,
        )

    @staticmethod
    def _validate_params(params: MuseTurnParams) -> None:
        model_id = str(params.model_id or "").strip()
        if not model_id:
            raise MuseSessionError("MUSE_MODEL_ID_MISSING", "turn needs a model id")
        if not str(params.prompt or ""):
            raise MuseSessionError("MUSE_PROMPT_MISSING", "turn needs a prompt")
        timeout = float(params.timeout_seconds or 0)
        if not timeout > 0:
            raise MuseSessionError("MUSE_TIMEOUT_INVALID", "turn needs a positive timeout")
        inactivity = float(params.inactivity_seconds or 0)
        if not inactivity > 0:
            raise MuseSessionError("MUSE_TIMEOUT_INVALID", "turn needs a positive inactivity bound")
        if str(params.approval_mode or "") not in APPROVAL_MODES:
            raise MuseSessionError(
                "MUSE_APPROVAL_MODE_UNKNOWN",
                f"not a closed MSP approval mode: {params.approval_mode!r}",
            )
        if not str(params.permission_profile or "").strip():
            raise MuseSessionError("MUSE_PROFILE_INVALID", "turn needs a permission profile")
        budget = params.token_budget
        if budget is not None and (isinstance(budget, bool) or not isinstance(budget, int) or budget < 1):
            raise MuseSessionError("MUSE_BUDGET_INVALID", "token budget must be an integer >= 1")
        workspace = Path(params.workspace)
        if not workspace.is_dir():
            raise MuseSessionError(
                "MUSE_WORKSPACE_MISSING", f"workspace is not a directory: {workspace}"
            )

    def _handshake(
        self,
        rpc: _StdioJsonRpc,
        params: MuseTurnParams,
        workspace: Path,
        stop: threading.Event,
        tracker: _TurnTracker,
    ) -> tuple[str, str, dict[str, Any]]:
        """initialize -> profile catalog -> session/start -> posture checks."""
        initializing = rpc.request(
            "initialize",
            {
                "clientInfo": {"name": "hyeve-aix-muse-execution", "version": "1"},
                "capabilities": {"experimentalApi": True, "userInputDialogs": False},
            },
            self._startup_timeout_s, stop,
        )
        if not isinstance(initializing, dict):
            raise MuseSessionError("MUSE_INITIALIZE_FAILED", "result is not a mapping")
        granted = initializing.get("grantedCapabilities")
        if not isinstance(granted, list):
            raise MuseSessionError("MUSE_INITIALIZE_FAILED", "no grantedCapabilities")
        experimental = bool(initializing.get("experimentalApi", False))
        if not experimental and params.token_budget is not None:
            raise MuseSessionError(
                "MUSE_CAPABILITY_POSTURE",
                "server declined experimentalApi but a token budget was requested",
            )
        rpc.notify("initialized")
        profiles = rpc.request("permissionProfile/list", {}, self._admission_timeout_s, stop)
        profile_row = self._select_profile(profiles, params.permission_profile)
        workspace_str = str(workspace)
        session_params = {
            "commandId": uuid7(),
            "workspaceRoot": workspace_str,
            "workspaceRoots": [workspace_str],
            "providerId": params.provider_id,
            "modelId": params.model_id.strip(),
            "approvalMode": params.approval_mode,
            "permissionProfile": params.permission_profile,
        }
        _require_params("session/start", session_params)
        started = rpc.request("session/start", session_params, self._startup_timeout_s, stop)
        if not isinstance(started, dict):
            raise MuseSessionError("MUSE_SESSION_START_MALFORMED", "result is not a mapping")
        session = started.get("session")
        head_cursor = started.get("viewCursor")
        if not isinstance(session, dict):
            raise MuseSessionError("MUSE_SESSION_START_MALFORMED", "no session object")
        if not isinstance(head_cursor, str) or not head_cursor:
            raise MuseSessionError("MUSE_SESSION_START_MALFORMED", "no viewCursor head")
        session_id = str(session.get("sessionId") or "").strip()
        if not session_id:
            raise MuseSessionError("MUSE_SESSION_START_MALFORMED", "no session.sessionId")
        # The session must be rooted exactly where we asked: a different
        # workspaceRoot would aim the worker at the wrong tree.
        if str(session.get("workspaceRoot") or "") != workspace_str:
            raise MuseSessionError(
                "MUSE_SESSION_ROOT_MISMATCH", "session is not rooted at the workspace"
            )
        model_echo = str(session.get("modelId") or "").strip()
        hooks = rpc.request("hook/list", {"sessionId": session_id}, self._admission_timeout_s, stop)
        mcps = rpc.request("mcpServer/list", {"sessionId": session_id}, self._admission_timeout_s, stop)
        hook_rows = hooks.get("hooks") if isinstance(hooks, dict) else None
        mcp_rows = mcps.get("servers") if isinstance(mcps, dict) else None
        if params.permission_profile == ":read-only" and (hook_rows or mcp_rows):
            raise MuseSessionError(
                "MUSE_SESSION_CAPABILITY_REFUSED",
                "read-only session loaded hooks or MCP servers",
            )
        handshake = {
            "experimental_api": experimental,
            "granted_capabilities": [str(item) for item in granted],
            "profile_row": profile_row,
            "session_echo": {key: session.get(key) for key in ("sessionId", "workspaceRoot", "providerId", "modelId")},
            "view_cursor_head": head_cursor,
            "hooks": len(hook_rows) if isinstance(hook_rows, list) else 0,
            "mcp_servers": len(mcp_rows) if isinstance(mcp_rows, list) else 0,
        }
        return session_id, model_echo, handshake

    @staticmethod
    def _select_profile(profiles: Any, wanted: str) -> dict[str, Any]:
        rows = profiles.get("profiles") if isinstance(profiles, dict) else None
        if not isinstance(rows, list):
            raise MuseSessionError("MUSE_PROFILE_INVALID", "profile catalog is not a list")
        for row in rows:
            if isinstance(row, dict) and row.get("profileId") == wanted:
                if row.get("available") is not True:
                    raise MuseSessionError(
                        "MUSE_PROFILE_UNAVAILABLE", f"profile {wanted!r} is not available"
                    )
                return {key: row.get(key) for key in ("profileId", "available")}
        raise MuseSessionError("MUSE_PROFILE_UNKNOWN", f"profile {wanted!r} not in catalog")

    def _start_turn(
        self,
        rpc: _StdioJsonRpc,
        params: MuseTurnParams,
        session_id: str,
        stop: threading.Event,
        tracker: _TurnTracker,
    ) -> tuple[str, dict[str, Any]]:
        command_id = uuid7()
        turn_params: dict[str, Any] = {
            "commandId": command_id,
            "sessionId": session_id,
            "input": [{"type": "text", "text": params.prompt}],
            "ifBusy": "queue",
            "reasoningEffort": params.reasoning_effort,
        }
        if params.token_budget is not None:
            turn_params["tokenBudget"] = params.token_budget
        _require_params("turn/start", turn_params)
        ack = rpc.request("turn/start", turn_params, self._admission_timeout_s, stop)
        if not isinstance(ack, dict):
            raise MuseSessionError("MUSE_TURN_START_MALFORMED", "ack is not a mapping")
        if ack.get("commandId") != command_id:
            raise MuseSessionError("MUSE_COMMAND_MISMATCH", "ack does not echo our command id")
        if ack.get("status") != "accepted":
            raise MuseSessionError("MUSE_ACK_REJECTED", f"status={ack.get('status')!r}")
        disposition = ack.get("disposition")
        if disposition not in {"started", "queued", "steered"}:
            raise MuseSessionError("MUSE_ACK_REJECTED", f"disposition={disposition!r}")
        if bool(ack.get("startedNewTurn")) != (disposition == "started"):
            raise MuseSessionError(
                "MUSE_ACK_REJECTED",
                f"disposition/startedNewTurn disagree: {disposition!r}",
            )
        # Authoritative: the top-level turnId, never a nested derivation.
        turn_id = str(ack.get("turnId") or "").strip()
        if not turn_id:
            raise MuseSessionError("MUSE_TURN_START_MALFORMED", "ack carries no turnId")
        ack_record = {
            "command_id": command_id,
            "status": ack.get("status"),
            "disposition": disposition,
            "started_new_turn": bool(ack.get("startedNewTurn")),
            "turn_id": turn_id,
        }
        started_event = self._await_turn_started(rpc, params, stop, tracker, turn_id)
        ack_record["turn_started"] = started_event
        return turn_id, ack_record

    def _await_turn_started(
        self,
        rpc: _StdioJsonRpc,
        params: MuseTurnParams,
        stop: threading.Event,
        tracker: _TurnTracker,
        turn_id: str,
    ) -> dict[str, Any]:
        """Require the turn/started launch boundary (reference parity)."""
        deadline = time.monotonic() + self._admission_timeout_s
        stash: list[dict[str, Any]] = []
        while True:
            if stop.is_set() or (params.is_cancelled is not None and params.is_cancelled()):
                raise MuseSessionError("CANCELLED", "cancelled awaiting turn launch")
            if rpc.stream_error() is not None:
                raise MuseSessionError("MUSE_STREAM_MALFORMED", rpc.stream_error() or "")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise MuseSessionError(
                    "MUSE_TURN_NOT_STARTED", f"no turn/started for {turn_id}"
                )
            event = rpc.get_notification(timeout=min(0.5, remaining))
            if event is None:
                if rpc.reader_done() and not rpc.has_pending():
                    raise MuseSessionError(
                        "MUSE_TRANSPORT_CLOSED", "transport closed before turn launch"
                    )
                continue
            method = event.get("method")
            notified = event.get("params") if isinstance(event.get("params"), dict) else {}
            if notified.get("sessionId") != tracker.session_id:
                continue
            tracker.last_activity = time.monotonic()
            if method == "view/gap":
                raise MuseSessionError("MUSE_VIEW_GAP", "gap before turn launch")
            if method == "turn/started" and notified.get("turnId") == turn_id:
                cursor = tracker.note_cursor(notified, required=True, context="turn/started")
                source = tracker.note_source(notified, required=True, context="turn/started")
                tracker.turn_started = True
                tracker.turn_started_source = {"view_cursor": cursor, "source": source}
                for held in stash:
                    rpc.restash(held)
                return {"view_cursor": cursor}
            stash.append(event)

    def _await_terminal(
        self,
        rpc: _StdioJsonRpc,
        params: MuseTurnParams,
        stop: threading.Event,
        tracker: _TurnTracker,
        turn_id: str,
    ) -> tuple[str, dict[str, Any]]:
        """Consume cursor-anchored view events until the terminal record."""
        deadline = time.monotonic() + float(params.timeout_seconds)
        inactivity = float(params.inactivity_seconds)
        while True:
            if stop.is_set() or (params.is_cancelled is not None and params.is_cancelled()):
                raise MuseSessionError("CANCELLED", "cancelled while awaiting turn")
            if rpc.stream_error() is not None:
                raise MuseSessionError("MUSE_STREAM_MALFORMED", rpc.stream_error() or "")
            now = time.monotonic()
            if now >= deadline:
                raise MuseSessionError("MUSE_TURN_TIMEOUT", f"turn {turn_id} timed out")
            if now - tracker.last_activity > inactivity:
                raise MuseSessionError(
                    "MUSE_TURN_INACTIVITY",
                    f"no session event for {inactivity:.0f}s on turn {turn_id}",
                )
            event = rpc.get_notification(timeout=min(0.5, deadline - now))
            if event is None:
                if rpc.reader_done() and not rpc.has_pending():
                    raise MuseSessionError(
                        "MUSE_TRANSPORT_CLOSED",
                        f"transport closed with no terminal for turn {turn_id}",
                    )
                continue
            method = event.get("method")
            notified = event.get("params") if isinstance(event.get("params"), dict) else {}
            if "id" in event and "method" in event:
                # Server-initiated request (already declined in dispatch):
                # observe, never resolve.
                tracker.method_counts[method] = tracker.method_counts.get(method, 0) + 1
                if str(method or "").startswith("approval/"):
                    tracker.approvals.append({"method": method, "solicited": True})
                continue
            if notified.get("sessionId") != tracker.session_id:
                continue
            tracker.last_activity = time.monotonic()
            tracker.method_counts[method] = tracker.method_counts.get(method, 0) + 1
            if method == "view/gap":
                raise MuseSessionError(
                    "MUSE_VIEW_GAP",
                    f"delivery gap after={notified.get('after')!r} next={notified.get('next')!r}",
                )
            if method == "session/closed":
                raise MuseSessionError("MUSE_SESSION_CLOSED", "session unloaded mid-turn")
            if method in {"approval/requested", "approval/reviewing", "approval/updated", "approval/resolved"}:
                tracker.note_cursor(notified, required=False, context=method)
                tracker.approvals.append({
                    "method": method,
                    "approval_id": notified.get("approvalId"),
                    "turn_id": notified.get("turnId"),
                })
                continue
            if method in {"item/started", "item/updated", "item/delta"}:
                self._observe_item_progress(tracker, turn_id, method, notified)
                continue
            if method == "item/completed":
                self._observe_item_completed(tracker, turn_id, notified, params.prompt)
                continue
            if method == "turn/completed" and notified.get("turnId") == turn_id:
                return self._accept_terminal(tracker, turn_id, notified)
            # turn/foregroundCompleted is explicitly non-terminal; any other
            # session-correlated frame only resets the watchdog above.

    def _observe_item_progress(
        self, tracker: _TurnTracker, turn_id: str, method: str, notified: dict[str, Any]
    ) -> None:
        tracker.note_cursor(notified, required=False, context=method)
        item: Any = None
        item_id: Any = None
        if method == "item/delta":
            item_id = notified.get("itemId")
        else:
            item = notified.get("item")
            if isinstance(item, dict):
                item_id = item.get("itemId")
        if not isinstance(item_id, str) or not item_id:
            return
        tracker.item_turns.setdefault(
            item_id, item.get("turnId") if isinstance(item, dict) else None
        )
        if item is not None and isinstance(item, dict) and item.get("turnId") not in (None, turn_id):
            return

    def _observe_item_completed(
        self, tracker: _TurnTracker, turn_id: str, notified: dict[str, Any], prompt: str
    ) -> None:
        tracker.note_cursor(notified, required=True, context="item/completed")
        tracker.note_source(notified, required=True, context="item/completed")
        item = notified.get("item")
        if not isinstance(item, dict):
            raise MuseSessionError("MUSE_EVENT_MALFORMED", "item/completed carries no item")
        item_id = item.get("itemId")
        if not isinstance(item_id, str) or not item_id:
            raise MuseSessionError("MUSE_EVENT_MALFORMED", "completed item lacks itemId")
        tracker.item_turns.setdefault(item_id, item.get("turnId"))
        if item.get("turnId") != turn_id:
            return
        kind = str(item.get("kind") or "")
        status = str(item.get("status") or "")
        if item_id in tracker.completed_items:
            prior = tracker.completed_items[item_id]
            if prior.get("kind") == kind and prior.get("status") == status and prior.get("text") == item.get("text"):
                return  # idempotent redelivery of the identical terminal object
            raise MuseSessionError(
                "MUSE_ITEM_REPLAY", f"item {item_id} completed twice with different content"
            )
        tracker.completed_items[item_id] = {
            "kind": kind, "status": status, "text": item.get("text"),
        }
        if kind == "userMessage":
            text = str(item.get("text") or "")
            if tracker.input_echoes:
                raise MuseSessionError("MUSE_INPUT_ECHO_DUPLICATE", "second input echo")
            if text != prompt:
                raise MuseSessionError("MUSE_INPUT_ECHO_MISMATCH", "input echo differs")
            tracker.input_echoes.append(item_id)
            return
        if kind == "agentMessage":
            text = str(item.get("text") or "")
            if text:
                tracker.agent_texts.append(text)
        if status != "completed":
            # Terminal-unknown or explicitly failed tool/model activity. The
            # turn record stays authoritative for the outcome; this preserves
            # the failure for explicit downstream handling.
            tracker.tool_failures.append(MuseToolFailure(
                item_id=item_id, kind=kind or "unknown", status=status or "unknown",
            ))

    def _accept_terminal(
        self, tracker: _TurnTracker, turn_id: str, notified: dict[str, Any]
    ) -> tuple[str, dict[str, Any]]:
        tracker.note_cursor(notified, required=True, context="turn/completed")
        source = tracker.note_source(notified, required=True, context="turn/completed")
        terminal = notified.get("terminal")
        usage = notified.get("usage")
        validated_usage: dict[str, Any] | None = None
        if usage is not None:
            validated_usage = self._validate_usage(usage)
        tracker.terminal_record = {
            "terminal": terminal,
            "source_range": source,
            "view_cursor": notified.get("viewCursor"),
            "usage": validated_usage,
            "duration_ms": notified.get("durationMs"),
            "reason": notified.get("reason"),
            "error": notified.get("error") if isinstance(notified.get("error"), dict) else None,
        }
        if terminal != "completed":
            error = tracker.terminal_record["error"] or {}
            raise MuseSessionError(
                "MUSE_TURN_FAILED",
                f"turn {turn_id} terminal {terminal}: "
                f"{error.get('kind', '')}:{error.get('message', '') or tracker.terminal_record['reason'] or 'unknown'}",
            )
        if not tracker.agent_texts:
            raise MuseSessionError(
                "MUSE_EMPTY_AGENT_TEXT",
                f"turn {turn_id} completed without an agent message",
            )
        return "completed", dict(notified)

    @staticmethod
    def _validate_usage(usage: Any) -> dict[str, Any]:
        """Validate the MSP TokenUsage shape without fabricating values."""
        if not isinstance(usage, dict):
            raise MuseSessionError("MUSE_USAGE_MALFORMED", "usage is not a mapping")
        required = ("cachedTokens", "inputTokens", "outputTokens", "reasoningTokens")
        cleaned: dict[str, Any] = {}
        for key in required:
            value = usage.get(key)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise MuseSessionError("MUSE_USAGE_MALFORMED", f"usage.{key} is not a counter")
            cleaned[key] = value
        for key in ("cacheReadTokens", "cacheWriteTokens"):
            value = usage.get(key)
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise MuseSessionError("MUSE_USAGE_MALFORMED", f"usage.{key} is not a counter")
            cleaned[key] = value
        return cleaned

    def _check_postmortem(
        self,
        rpc: _StdioJsonRpc,
        tracker: _TurnTracker,
        turn_id: str,
        accepted: dict[str, Any],
    ) -> None:
        """Validate every notification still queued after the readers joined.

        The worker is dead and the readers are joined, so no new frame can
        arrive: the queue is drained non-blockingly and every residue is
        judged. A late stream error, a delivery gap, further activity for
        the turn, or a second terminal that disagrees with the accepted one
        refuses the turn. An idempotent redelivery of the accepted terminal
        is tolerated. Session-unrelated and foreign-turn residue is ignored
        exactly as during streaming; explicitly non-terminal frames
        (foreground progress, approval observations, session close) are
        tolerated as observations.
        """
        if rpc.stream_error() is not None:
            raise MuseSessionError(
                "MUSE_STREAM_MALFORMED", f"late stream error: {rpc.stream_error()}"
            )
        for note in rpc.drain_notifications():
            if "id" in note:
                # Server-initiated request: already declined at dispatch;
                # observation only.
                continue
            params = note.get("params") if isinstance(note.get("params"), dict) else {}
            if params.get("sessionId") != tracker.session_id:
                continue
            method = note.get("method")
            if method == "view/gap":
                raise MuseSessionError("MUSE_VIEW_GAP", "gap after the turn terminal")
            if method in {"session/closed", "turn/foregroundCompleted"}:
                continue
            if str(method or "").startswith("approval/"):
                continue
            if method in {
                "item/completed", "item/started", "item/updated", "item/delta",
            }:
                # Item events carry no top-level turnId: completed/started/
                # updated bind the turn on the nested item object, and
                # deltas carry only an itemId resolved through the streaming
                # registry. Anything same-session that cannot be proven
                # foreign is late in this fresh single-turn host.
                binding = self._late_item_binding(method, params, tracker, turn_id)
                if binding != "foreign":
                    raise MuseSessionError(
                        "MUSE_LATE_ACTIVITY",
                        f"{method} after the turn terminal ({binding})",
                    )
                continue
            if params.get("turnId") != turn_id:
                continue
            if method == "turn/completed":
                triple = (
                    params.get("terminal"),
                    params.get("viewCursor"),
                    params.get("sessionId"),
                )
                expected = (
                    accepted.get("terminal"),
                    accepted.get("viewCursor"),
                    accepted.get("sessionId"),
                )
                if triple != expected:
                    raise MuseSessionError(
                        "MUSE_TERMINAL_CONTRADICTION",
                        f"second terminal for turn {turn_id} disagrees: {triple!r}",
                    )
                continue
            if method == "turn/started":
                # Single-turn session: a second launch after the terminal
                # is late by definition (turnId matched above).
                raise MuseSessionError(
                    "MUSE_LATE_ACTIVITY", f"{method} after the turn terminal"
                )
            # Any other turn-correlated frame only resets the watchdog
            # during streaming; postmortem it is inert residue.

    @staticmethod
    def _late_item_binding(
        method: str,
        params: dict[str, Any],
        tracker: _TurnTracker,
        turn_id: str,
    ) -> str:
        """Correlate a post-terminal item event: ours, foreign, or unexplained.

        ``item/completed``, ``item/started`` and ``item/updated`` bind the
        turn on the nested ``item`` object (there is no top-level turnId);
        ``item/delta`` carries only an ``itemId`` and resolves through the
        streaming item registry. A present nested turn different from ours
        proves a foreign turn and is preserved as a distinction; anything
        else in this fresh single-turn host — our turn, or an event with
        no turn binding at all — cannot be assigned to another turn.
        """
        if method == "item/delta":
            item_id = params.get("itemId")
            if isinstance(item_id, str) and item_id:
                owner = tracker.item_turns.get(item_id)
                if owner == turn_id:
                    return "ours"
                if owner is not None:
                    return "foreign"
            return "unexplained"
        item = params.get("item")
        if not isinstance(item, dict):
            return "unexplained"
        nested = item.get("turnId")
        if nested == turn_id:
            return "ours"
        if isinstance(nested, str) and nested:
            return "foreign"
        item_id = item.get("itemId")
        if isinstance(item_id, str) and item_id:
            owner = tracker.item_turns.get(item_id)
            if owner == turn_id:
                return "ours"
            if owner is not None:
                return "foreign"
        return "unexplained"

    def _transcript_pending(
        self,
        *,
        tracker: _TurnTracker,
        terminal: str,
        turn_record: dict[str, Any],
        model_echo: str,
        shutdown: str,
    ) -> MuseTurnResult:
        usage = tracker.terminal_record.get("usage")
        return MuseTurnResult(
            session_id=tracker.session_id,
            turn_id=tracker.turn_id,
            terminal=terminal,
            # The whole turn, in arrival order (reference parity): a
            # structured result plus a conversational epilogue stay together
            # so reviewers see everything the model claimed.
            agent_text="\n".join(tracker.agent_texts),
            model_echo=model_echo,
            token_usage=dict(usage) if isinstance(usage, dict) else None,
            turn_record=dict(turn_record),
            tool_failures=tuple(tracker.tool_failures),
            approval_roundtrips=len(tracker.approvals),
            input_echo_observed=bool(tracker.input_echoes),
            view_cursor_head=tracker.latest_cursor or "",
            duration_ms=turn_record.get("durationMs") if isinstance(
                turn_record.get("durationMs"), int) else None,
            shutdown=shutdown,
        )

    def _terminate(
        self, proc: subprocess.Popen[str], rpc: _StdioJsonRpc
    ) -> tuple[int | None, tuple[str, ...], str]:
        """Bounded shutdown with surviving-group escalation and death proof.

        Close stdin, wait for a voluntary exit, then escalate SIGTERM and
        SIGKILL against the owned process. The child owns its process group
        (``start_new_session``), so after the child is reaped any surviving
        group member is killed and re-verified: a group that outlives the
        escalation is an unproven shutdown and refuses. Returns
        ``(exit_code, stderr_tail, shutdown)`` where ``shutdown`` is
        ``clean`` (exited after stdin close, no signal sent),
        ``terminated`` (SIGTERM sent), or ``killed`` (SIGKILL sent).
        """
        shutdown = "clean"
        if proc.stdin is not None:
            try:
                proc.stdin.close()
            except (BrokenPipeError, OSError):
                pass
        try:
            proc.wait(timeout=_EXIT_WAIT_SECONDS)
        except subprocess.TimeoutExpired:
            shutdown = "terminated"
            try:
                proc.terminate()
            except OSError:
                pass
            try:
                proc.wait(timeout=_TERM_WAIT_SECONDS)
            except subprocess.TimeoutExpired:
                shutdown = "killed"
                killpg = getattr(os, "killpg", None)
                try:
                    if killpg is not None:
                        killpg(proc.pid, signal.SIGKILL)
                    else:
                        proc.kill()
                except (OSError, ProcessLookupError):
                    try:
                        proc.kill()
                    except OSError:
                        pass
                try:
                    proc.wait(timeout=_KILL_WAIT_SECONDS)
                except subprocess.TimeoutExpired as exc:
                    raise MuseSessionError(
                        "MUSE_SHUTDOWN_UNPROVEN", "session process survived SIGKILL"
                    ) from exc
        if proc.poll() is None:
            raise MuseSessionError("MUSE_SHUTDOWN_UNPROVEN", "session process still alive")
        # Group escalation even after the parent is reaped: orphans that
        # outlived the child still belong to this turn's owned group. Any
        # kill sent here makes the shutdown forced, never clean.
        if self._ensure_group_gone(proc.pid):
            shutdown = "killed"
        if not rpc.join_readers(timeout=_READER_JOIN_SECONDS):
            raise MuseSessionError("MUSE_SHUTDOWN_UNPROVEN", "reader threads did not exit")
        return proc.returncode, rpc.stderr_tail(), shutdown

    @staticmethod
    def _ensure_group_gone(pid: int) -> bool:
        """Kill any survivors of the owned process group and verify death.

        Returns True when a SIGKILL was sent to surviving members (a
        forced termination the caller must record as unclean), False when
        the group was already gone. Runs immediately after the child is
        reaped so the checked group is still the turn's own; the
        check-then-kill window is kept minimal. Residual race: if the OS
        recycled the pid between the reap and this check, signal-0 could
        succeed on a foreign same-user group. Only a successful signal-0
        (a group we are allowed to signal) escalates to SIGKILL, and a
        group that outlives the bounded re-verification refuses instead of
        being left running.
        """
        killpg = getattr(os, "killpg", None)
        if killpg is None:
            return False
        try:
            killpg(pid, 0)
        except ProcessLookupError:
            return False
        except OSError as exc:
            raise MuseSessionError(
                "MUSE_SHUTDOWN_UNPROVEN", f"cannot signal session group: {exc}"
            ) from exc
        try:
            killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            return False
        except OSError as exc:
            raise MuseSessionError(
                "MUSE_SHUTDOWN_UNPROVEN", f"cannot kill session group: {exc}"
            ) from exc
        deadline = time.monotonic() + _GROUP_DEATH_SECONDS
        while True:
            try:
                killpg(pid, 0)
            except ProcessLookupError:
                return True
            except OSError as exc:
                raise MuseSessionError(
                    "MUSE_SHUTDOWN_UNPROVEN", f"cannot verify session group: {exc}"
                ) from exc
            if time.monotonic() >= deadline:
                raise MuseSessionError(
                    "MUSE_SHUTDOWN_UNPROVEN", "session process group still has members"
                )
            time.sleep(0.05)

    def _write_transcript(
        self,
        evidence_dfd: int,
        evidence_dir: Path,
        dir_identity: tuple[int, int],
        outcome: MuseTurnResult,
        changes: tuple[FileChange, ...],
        exit_code: int | None,
        stderr_tail: tuple[str, ...],
        shutdown: str,
        *,
        context: dict[str, Any],
        started_at: float,
    ) -> str:
        """Publish the transcript through the pre-effect held descriptor.

        The transcript carries the held directory's trusted ``(dev, ino)``
        identity alongside the file name, so the returned reference is
        bound to the actual held identity: a verifier stats the configured
        path and compares the parent directory's identity.
        """
        payload = {
            "session_id": outcome.session_id,
            "turn_id": outcome.turn_id,
            "terminal": outcome.terminal,
            "model_echo": outcome.model_echo,
            "handshake": context.get("handshake"),
            "ack": context.get("ack"),
            "turn_started": context.get("turn_started"),
            "method_counts": context.get("method_counts"),
            "view_cursors_consumed": context.get("view_cursors_consumed"),
            "prompt_sha256": context.get("prompt_sha256"),
            "agent_text": outcome.agent_text,
            "tool_failures": [
                {"item_id": item.item_id, "kind": item.kind, "status": item.status}
                for item in outcome.tool_failures
            ],
            "approval_roundtrips": outcome.approval_roundtrips,
            "input_echo_observed": outcome.input_echo_observed,
            "view_cursor_head": outcome.view_cursor_head,
            "duration_ms": outcome.duration_ms,
            "token_usage": outcome.token_usage,
            "turn_record": outcome.turn_record,
            "workspace_changes": changes_to_jsonable(changes),
            "exit_code": exit_code,
            "shutdown": shutdown,
            "evidence_dir_identity": {"dev": dir_identity[0], "ino": dir_identity[1]},
            "stderr_tail": list(stderr_tail),
            "elapsed_seconds": round(time.monotonic() - started_at, 3),
        }
        raw = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
        final = _write_evidence_file_at(evidence_dfd, f"turn-{outcome.turn_id}", raw)
        return str(Path(evidence_dir) / final)


__all__ = [
    "APPROVAL_MODES",
    "MuseSessionError",
    "MuseSessionPort",
    "MuseToolFailure",
    "MuseTurnParams",
    "MuseTurnResult",
    "StdioMspSessionPort",
    "is_muse_model_id",
    "normalize_model_id",
    "uuid7",
    "write_evidence_file",
]

