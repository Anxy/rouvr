"""Production-transport protocol tests with an exact-shape fake executable.

Every test here drives the REAL ``StdioMspSessionPort`` against
``muse_fake_executable.py`` — a protocol peer speaking the maintained MSP
shapes (initialize/initialized, session/start with viewCursor, catalog
lists, top-level turnId ack with command correlation, turn/started,
sourceRange/viewCursor-anchored items, usage-carrying terminal, gaps, late
events, malformed frames). The fake never mirrors implementation
internals; scenarios select literal frame sequences.

No subprocess is spawned except the fake itself; no network, credentials,
or WorkHub writes. The one harmless REAL-session probe is owned by Root
and documented (not executed) in ``test_real_session_probe``.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import re
import threading
from pathlib import Path

import pytest

from backend.aix_core.framework.execution import muse_session as muse_session_module
from backend.aix_core.framework.execution.muse_session import (
    MuseSessionError,
    MuseTurnParams,
    StdioMspSessionPort,
    _write_evidence_file_at,
    uuid7,
    write_evidence_file,
)
from backend.aix_core.framework.execution.muse_workspace_snapshot import (
    SnapshotIncomplete,
    diff_snapshots,
    snapshot_workspace,
)

FAKE_TURN_ID = "22222222-2222-7000-8000-000000000002"
UUID7_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


def _install_fake(tmp_path: Path) -> Path:
    src = Path(__file__).parent / "muse_fake_executable.py"
    dst = tmp_path / "fake-muse"
    dst.write_bytes(src.read_bytes())
    dst.chmod(0o700)
    return dst


def _private_dir(path: Path) -> Path:
    """Create an explicitly private owned directory for evidence.

    Default mkdir modes inherit umask/ACLs, which trips the production
    shared-directory refusal. The fixture must establish and assert the
    privacy the production check requires; this never weakens it.
    """
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, 0o700)
    st = os.stat(path)
    assert st.st_uid == os.geteuid(), f"evidence dir not owned: {path}"
    assert st.st_mode & 0o022 == 0, (
        f"evidence dir shared: {path} mode={oct(st.st_mode)}"
    )
    return path


# Every environment control the fake executable reads. The production
# transport rebuilds the child environment from its minimal allowlist,
# which intentionally strips these; the fixture below passes them to the
# spawned fake only. This list must stay in sync with the fake's
# os.environ reads.
_FAKE_ENV_KEYS = (
    "MUSE_FAKE_SCENARIO",
    "MUSE_FAKE_LOG",
    "MUSE_FAKE_WRITE_FILES",
    "MUSE_FAKE_ACK_STATUS",
    "MUSE_FAKE_DISPOSITION",
)


def _run_params(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scenario: str,
    *,
    prompt: str = "Do the thing",
    timeout_seconds: float = 60.0,
    approval_mode: str = "denyUnmatched",
    permission_profile: str = ":read-only",
    token_budget: int | None = None,
    extra_env: dict[str, str] | None = None,
) -> tuple[MuseTurnParams, Path]:
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    evidence = _private_dir(tmp_path / "ev")
    monkeypatch.setenv("MUSE_FAKE_SCENARIO", scenario)
    log_path = tmp_path / "fake.log"
    monkeypatch.setenv("MUSE_FAKE_LOG", str(log_path))
    for key, value in (extra_env or {}).items():
        monkeypatch.setenv(key, value)
    # Deterministic test-only HOME: the child environment is rebuilt from
    # the production allowlist, which passes HOME through by name — but a
    # clean test env may not define it at all. Point it at a disposable
    # directory (monkeypatch-reverted); never repurpose the real system
    # HOME, and no production environment code changes.
    fake_home = tmp_path / "fake-home"
    fake_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(fake_home))
    # Test-only child environment injection: widen the transport module's
    # allowlist with exactly the fake controls above so the scenario, log
    # path, file writes, ack status, and disposition reach the spawned
    # fake. monkeypatch reverts it after the test; the production
    # _SAFE_ENV_KEYS source is unchanged and still strips everything else
    # (the sentinel test proves no unrelated variable leaks through).
    monkeypatch.setattr(
        muse_session_module,
        "_SAFE_ENV_KEYS",
        tuple(muse_session_module._SAFE_ENV_KEYS) + _FAKE_ENV_KEYS,
    )
    params = MuseTurnParams(
        model_id="muse-spark-1.3",
        workspace=workspace,
        prompt=prompt,
        evidence_dir=evidence,
        timeout_seconds=timeout_seconds,
        approval_mode=approval_mode,
        permission_profile=permission_profile,
        token_budget=token_budget,
    )
    return params, log_path


def _log_lines(log_path: Path) -> list[str]:
    if not log_path.is_file():
        return []
    return log_path.read_text(encoding="utf-8").splitlines()


@pytest.mark.asyncio
async def test_happy_turn_observes_real_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _install_fake(tmp_path)
    params, _ = _run_params(
        tmp_path, monkeypatch, "happy-write",
        extra_env={"MUSE_FAKE_WRITE_FILES": "helper.py"},
    )
    result = await StdioMspSessionPort(muse_bin=str(fake)).run_turn(params)
    assert result.terminal == "completed"
    assert result.turn_id == FAKE_TURN_ID
    # Joined agent messages in arrival order (structured + epilogue kept).
    assert result.agent_text == "part one\npart two"
    assert result.input_echo_observed is True
    assert result.tool_failures == ()
    assert result.token_usage == {
        "cachedTokens": 0, "inputTokens": 10, "outputTokens": 5, "reasoningTokens": 0,
    }
    assert result.exit_code == 0
    # Clean shutdown disposition: exited on stdin close, no signal sent.
    assert result.shutdown == "clean"
    # Latest consumed cursor in arrival order. Cursors are opaque: the
    # terminal cursor "cur-10" sorts BELOW "cur-9" lexicographically, so a
    # sorting head would report "cur-9" here.
    assert result.view_cursor_head == "cur-10"
    # Observed (not reported) change with a real digest.
    assert len(result.changes) == 1
    change = result.changes[0]
    assert (change.path, change.change, change.kind) == ("helper.py", "added", "file")
    assert change.sha256 is not None
    # Transcript lives outside the workspace, as immutable evidence.
    assert len(result.evidence_files) == 1
    transcript = Path(result.evidence_files[0])
    assert transcript.is_file()
    assert params.workspace not in transcript.parents
    body = json.loads(transcript.read_text(encoding="utf-8"))
    assert body["turn_id"] == FAKE_TURN_ID
    assert body["agent_text"] == "part one\npart two"
    assert body["ack"]["turn_id"] == FAKE_TURN_ID
    assert body["workspace_changes"][0]["path"] == "helper.py"
    assert body["shutdown"] == "clean"
    assert body["view_cursor_head"] == "cur-10"
    # The returned reference is bound to the held directory identity the
    # transport retained pre-effect: the configured path stats to the
    # same (dev, ino) the transcript records.
    directory_stat = params.evidence_dir.stat()
    assert body["evidence_dir_identity"] == {
        "dev": directory_stat.st_dev, "ino": directory_stat.st_ino,
    }


@pytest.mark.asyncio
async def test_spawn_shape_cwd_and_env_isolation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _install_fake(tmp_path)
    monkeypatch.setenv("MUSE_TRANSPORT_SENTINEL_SECRET", "must-not-leak")
    params, log_path = _run_params(tmp_path, monkeypatch, "happy")
    await StdioMspSessionPort(muse_bin=str(fake)).run_turn(params)
    lines = _log_lines(log_path)
    argv_line = next(line for line in lines if line.startswith("argv:"))
    argv = json.loads(argv_line[len("argv:"):])
    assert argv[0] == "serve"
    for flag in ("--provider", "meta", "--model", "muse-spark-1.3",
                 "--contributor-mode", "on", "--disable-request-user-input",
                 "--sandbox-network", "restricted", "--trust-workspace"):
        assert flag in argv
    cwd_line = next(line for line in lines if line.startswith("cwd:"))
    assert cwd_line[len("cwd:"):] == str(params.workspace)
    env_line = next(line for line in lines if line.startswith("env:"))
    env_keys = json.loads(env_line[len("env:"):])
    assert "MUSE_TRANSPORT_SENTINEL_SECRET" not in env_keys
    assert "HOME" in env_keys and "PATH" in env_keys


@pytest.mark.asyncio
async def test_command_ids_are_uuid7(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _install_fake(tmp_path)
    params, log_path = _run_params(tmp_path, monkeypatch, "happy")
    await StdioMspSessionPort(muse_bin=str(fake)).run_turn(params)
    ids: list[str] = []
    for line in _log_lines(log_path):
        if line.startswith("session-start:") or line.startswith("turn-start:"):
            ids.append(json.loads(line.split(":", 1)[1])["commandId"])
    # initialize carries no command id; session/start + turn/start each do.
    assert len(ids) == 2
    assert len(set(ids)) == 2
    for value in ids:
        assert UUID7_RE.match(value), f"not UUIDv7: {value}"


def test_uuid7_generator_shape() -> None:
    first, second = uuid7(), uuid7()
    assert first != second
    assert UUID7_RE.match(first) and UUID7_RE.match(second)


@pytest.mark.asyncio
async def test_approval_roundtrip_observed_without_client_decision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _install_fake(tmp_path)
    params, log_path = _run_params(tmp_path, monkeypatch, "approval")
    result = await StdioMspSessionPort(muse_bin=str(fake)).run_turn(params)
    assert result.terminal == "completed"
    # Three approval notifications plus the solicited server request we
    # declined without resolving.
    assert result.approval_roundtrips == 4
    replies = [
        json.loads(line[len("client-reply:"):])
        for line in _log_lines(log_path)
        if line.startswith("client-reply:")
    ]
    declined = [obj for obj in replies if obj.get("id") == 99]
    assert len(declined) == 1
    assert declined[0].get("error", {}).get("code") == -32601


@pytest.mark.asyncio
async def test_tool_failure_preserved_on_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _install_fake(tmp_path)
    params, _ = _run_params(tmp_path, monkeypatch, "tool-fail")
    result = await StdioMspSessionPort(muse_bin=str(fake)).run_turn(params)
    assert result.terminal == "completed"
    assert [(item.item_id, item.kind, item.status) for item in result.tool_failures] == [
        ("it-tool", "toolCall", "failed")
    ]
    # Agent text alone is not the outcome: the failure rides alongside it.
    assert result.agent_text == "part one\npart two"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scenario,code",
    [
        ("gap", "MUSE_VIEW_GAP"),
        ("late", "MUSE_LATE_ACTIVITY"),
        ("malformed", "MUSE_STREAM_MALFORMED"),
        ("dup-keys", "MUSE_STREAM_MALFORMED"),
        ("bad-bytes", "MUSE_STREAM_MALFORMED"),
        ("early-exit", "MUSE_TRANSPORT_CLOSED"),
        ("bad-ack-cmd", "MUSE_COMMAND_MISMATCH"),
        ("no-turn-id", "MUSE_TURN_START_MALFORMED"),
        ("no-cursor", "MUSE_EVENT_MALFORMED"),
        ("bad-usage", "MUSE_USAGE_MALFORMED"),
        ("empty-text", "MUSE_EMPTY_AGENT_TEXT"),
        ("init-error", "MUSE_REQUEST_ERROR"),
        # Post-terminal residue: the terminal is already accepted when the
        # garbage / second terminal arrives, so only the postmortem check
        # (after process death and joined readers) can refuse these. The
        # old timed drain returned success for both.
        ("malformed-after-terminal", "MUSE_STREAM_MALFORMED"),
        ("contradictory-terminal", "MUSE_TERMINAL_CONTRADICTION"),
        # Post-terminal item events in actual nested shapes: a delta for a
        # known item correlates through the streaming registry (deltas
        # carry only an itemId), and an event with no turn binding at all
        # is unexplained in a fresh single-turn host. A top-level turnId
        # filter would miss both and return success.
        ("late-delta", "MUSE_LATE_ACTIVITY"),
        ("late-unbound", "MUSE_LATE_ACTIVITY"),
        # The parent exits cleanly while a same-group child survives it:
        # group cleanup must send signals, so the shutdown is forced and
        # the valid terminal still refuses. The old code killed the group
        # yet still reported a clean success.
        ("orphan-child", "MUSE_UNCLEAN_EXIT"),
    ],
)
async def test_protocol_refusals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scenario: str, code: str
) -> None:
    fake = _install_fake(tmp_path)
    params, _ = _run_params(tmp_path, monkeypatch, scenario, timeout_seconds=20.0)
    with pytest.raises(MuseSessionError) as caught:
        await StdioMspSessionPort(muse_bin=str(fake)).run_turn(params)
    assert caught.value.code == code


@pytest.mark.asyncio
async def test_rejected_ack_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _install_fake(tmp_path)
    params, _ = _run_params(
        tmp_path, monkeypatch, "happy",
        extra_env={"MUSE_FAKE_ACK_STATUS": "rejected"},
    )
    with pytest.raises(MuseSessionError) as caught:
        await StdioMspSessionPort(muse_bin=str(fake)).run_turn(params)
    assert caught.value.code == "MUSE_ACK_REJECTED"


@pytest.mark.asyncio
async def test_unknown_profile_refuses_before_spawn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _install_fake(tmp_path)
    params, _ = _run_params(
        tmp_path, monkeypatch, "happy", permission_profile=":auto-review"
    )
    with pytest.raises(MuseSessionError) as caught:
        await StdioMspSessionPort(muse_bin=str(fake)).run_turn(params)
    assert caught.value.code == "MUSE_PROFILE_UNKNOWN"


@pytest.mark.asyncio
async def test_evidence_dir_inside_workspace_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _install_fake(tmp_path)
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    inner_evidence = _private_dir(workspace / "ev")
    monkeypatch.setenv("MUSE_FAKE_SCENARIO", "happy")
    params = MuseTurnParams(
        model_id="muse-spark-1.3", workspace=workspace, prompt="x",
        evidence_dir=inner_evidence, timeout_seconds=10.0,
    )
    with pytest.raises(MuseSessionError) as caught:
        await StdioMspSessionPort(muse_bin=str(fake)).run_turn(params)
    assert caught.value.code == "MUSE_EVIDENCE_DIR_INVALID"


@pytest.mark.asyncio
async def test_preplanted_transcript_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _install_fake(tmp_path)
    params, _ = _run_params(tmp_path, monkeypatch, "happy")
    # A worker-planted (or stale) file at the exact transcript name must
    # refuse publication instead of being replaced.
    planted = params.evidence_dir / f"turn-{FAKE_TURN_ID}.json"
    planted.write_text("{}", encoding="utf-8")
    with pytest.raises(MuseSessionError) as caught:
        await StdioMspSessionPort(muse_bin=str(fake)).run_turn(params)
    assert caught.value.code == "MUSE_EVIDENCE_REDIRECTED"
    assert planted.read_text(encoding="utf-8") == "{}"


@pytest.mark.asyncio
async def test_late_foreign_turn_item_is_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A post-terminal item bound to a DIFFERENT turn on its nested item
    # object is proven foreign: the turn evidence stands. Only ours and
    # unexplained same-session items refuse.
    fake = _install_fake(tmp_path)
    params, _ = _run_params(tmp_path, monkeypatch, "late-foreign")
    result = await StdioMspSessionPort(muse_bin=str(fake)).run_turn(params)
    assert result.terminal == "completed"
    assert result.agent_text == "part one\npart two"


@pytest.mark.asyncio
async def test_worker_cancelled_outcome_terminates_cleanup_wait(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A sync cancellation callback that raises CancelledError itself: the
    # worker cleans up and stores that CancelledError as its outcome. The
    # cleanup wait must consume the finished future once and propagate,
    # not re-await it forever without suspending (the old loop spun on the
    # stored CancelledError and never returned).
    fake = _install_fake(tmp_path)
    params, _ = _run_params(tmp_path, monkeypatch, "happy")

    def _raise_cancelled() -> bool:
        raise asyncio.CancelledError("sync callback cancel")

    params = dataclasses.replace(params, is_cancelled=_raise_cancelled)
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(
            StdioMspSessionPort(muse_bin=str(fake)).run_turn(params),
            timeout=30.0,
        )


def test_evidence_racing_publisher_keeps_no_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A concurrent same-user publisher that wins the publication window:
    # injected between our temp write and publication. The old
    # lstat-then-rename path is blind to it and replaces the winner's
    # bytes; hard-link creation fails atomically and refuses instead.
    evidence = _private_dir(tmp_path / "ev")
    real_link = os.link

    def _racing_link(src: str, dst: str, **kwargs: object) -> None:
        fd = os.open(
            dst, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600,
            dir_fd=kwargs.get("dst_dir_fd"),
        )
        try:
            os.write(fd, b'{"racer": true}')
        finally:
            os.close(fd)
        real_link(src, dst, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "link", _racing_link)
    with pytest.raises(MuseSessionError) as caught:
        write_evidence_file(evidence, "turn-race", b'{"ours": true}')
    assert caught.value.code == "MUSE_EVIDENCE_REDIRECTED"
    assert (evidence / "turn-race.json").read_bytes() == b'{"racer": true}'
    assert [path.name for path in evidence.iterdir()
            if path.name.startswith(".")] == []


def test_evidence_publish_never_touches_foreign_temp(tmp_path: Path) -> None:
    # A temp file owned by another in-flight publisher at a predictable
    # name: publication must use its own unique temp and leave the foreign
    # file alone. The old shared temp name unlinked and stole it.
    evidence = _private_dir(tmp_path / "ev")
    decoy = evidence / f".turn-x.{os.getpid()}.tmp"
    decoy.write_bytes(b'{"not": "ours"}')
    path = write_evidence_file(evidence, "turn-x", b'{"ours": true}')
    assert path.read_bytes() == b'{"ours": true}'
    assert decoy.read_bytes() == b'{"not": "ours"}'


def test_evidence_concurrent_publishers_one_winner(tmp_path: Path) -> None:
    # Concurrent same-name publishers: the kernel atomic link lets exactly
    # one win; every loser refuses and the winner's bytes survive intact.
    evidence = _private_dir(tmp_path / "ev")
    barrier = threading.Barrier(8)
    outcomes: list[tuple[str, int, bytes]] = []

    def _publish(index: int) -> None:
        barrier.wait()
        try:
            path = write_evidence_file(
                evidence, "turn-race", f'{{"i": {index}}}'.encode("utf-8")
            )
            outcomes.append(("ok", index, path.read_bytes()))
        except MuseSessionError as exc:
            outcomes.append((exc.code, index, b""))

    threads = [threading.Thread(target=_publish, args=(index,)) for index in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    wins = [outcome for outcome in outcomes if outcome[0] == "ok"]
    assert len(wins) == 1
    assert all(outcome[0] == "MUSE_EVIDENCE_REDIRECTED" for outcome in outcomes
               if outcome[0] != "ok")
    assert (evidence / "turn-race.json").read_bytes() == wins[0][2]
    assert [path.name for path in evidence.iterdir()
            if path.name.startswith(".")] == []


def test_evidence_write_bound_to_held_descriptor(tmp_path: Path) -> None:
    # The configured path is swapped to a different directory mid-hold:
    # publication through the held descriptor still lands in the original
    # inode and never in the replacement. Path re-resolution would follow
    # the swap.
    first = _private_dir(tmp_path / "first")
    dfd = os.open(first, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        moved = tmp_path / "moved"
        os.rename(first, moved)
        _private_dir(tmp_path / "first")
        name = _write_evidence_file_at(dfd, "turn-x", b"{}")
        assert (moved / name).read_bytes() == b"{}"
        assert not (tmp_path / "first" / name).exists()
    finally:
        os.close(dfd)


@pytest.mark.asyncio
async def test_cancellation_owns_worker_before_return(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _install_fake(tmp_path)
    params, log_path = _run_params(tmp_path, monkeypatch, "slow", timeout_seconds=120.0)
    port = StdioMspSessionPort(muse_bin=str(fake))
    task = asyncio.create_task(port.run_turn(params))
    await asyncio.sleep(3)
    assert not task.done()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # Ownership is restored BEFORE the caller returns: the owned process
    # is already dead here with no grace polling. (The old implementation
    # returned while the worker thread was still being stopped and needed
    # up to 10s of post-return polling.)
    pid_line = next(line for line in _log_lines(log_path) if line.startswith("pid:"))
    pid = int(pid_line[len("pid:"):])
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        pass
    else:
        pytest.fail("owned worker alive at cancellation return")
    # Cleanup completed: the same port instance serves again.
    monkeypatch.setenv("MUSE_FAKE_SCENARIO", "happy")
    again = await port.run_turn(dataclasses.replace(params, timeout_seconds=60.0))
    assert again.terminal == "completed"


@pytest.mark.asyncio
async def test_inactivity_watchdog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _install_fake(tmp_path)
    params, _ = _run_params(tmp_path, monkeypatch, "quiet", timeout_seconds=120.0)
    params = dataclasses.replace(params, inactivity_seconds=2.0)
    with pytest.raises(MuseSessionError) as caught:
        await StdioMspSessionPort(muse_bin=str(fake)).run_turn(params)
    assert caught.value.code == "MUSE_TURN_INACTIVITY"


@pytest.mark.asyncio
async def test_turn_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _install_fake(tmp_path)
    params, _ = _run_params(tmp_path, monkeypatch, "slow", timeout_seconds=2.0)
    with pytest.raises(MuseSessionError) as caught:
        await StdioMspSessionPort(muse_bin=str(fake)).run_turn(params)
    assert caught.value.code == "MUSE_TURN_TIMEOUT"


def test_snapshot_detects_deletion(tmp_path: Path) -> None:
    target = tmp_path / "forbidden.py"
    target.write_text("x = 1\n", encoding="utf-8")
    before = snapshot_workspace(tmp_path)
    target.unlink()
    changes = diff_snapshots(before, snapshot_workspace(tmp_path))
    assert [(item.path, item.change) for item in changes] == [("forbidden.py", "deleted")]


def test_snapshot_detects_same_size_restored_mtime_rewrite(tmp_path: Path) -> None:
    target = tmp_path / "helper.py"
    target.write_text("aaaaaaa\n", encoding="utf-8")
    before = snapshot_workspace(tmp_path)
    stat = target.stat()
    target.write_text("bbbbbbb\n", encoding="utf-8")
    os.utime(target, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    changes = diff_snapshots(before, snapshot_workspace(tmp_path))
    assert [(item.path, item.change) for item in changes] == [("helper.py", "modified")]
    assert changes[0].sha256 is not None


def test_snapshot_detects_new_symlink(tmp_path: Path) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("s3cr3t\n", encoding="utf-8")
    before = snapshot_workspace(tmp_path)
    os.symlink(str(outside), tmp_path / "link")
    changes = diff_snapshots(before, snapshot_workspace(tmp_path))
    assert [(item.path, item.change, item.kind) for item in changes] == [
        ("link", "added", "symlink")
    ]
    assert changes[0].link_target == str(outside)


def test_snapshot_detects_mode_change(tmp_path: Path) -> None:
    target = tmp_path / "run.sh"
    target.write_text("#!/bin/sh\n", encoding="utf-8")
    target.chmod(0o600)
    before = snapshot_workspace(tmp_path)
    target.chmod(0o644)
    changes = diff_snapshots(before, snapshot_workspace(tmp_path))
    assert [(item.path, item.change) for item in changes] == [("run.sh", "mode_changed")]


def test_snapshot_missing_root_is_incomplete(tmp_path: Path) -> None:
    with pytest.raises(SnapshotIncomplete):
        snapshot_workspace(tmp_path / "nope")


def test_snapshot_detects_root_mode_change(tmp_path: Path) -> None:
    target = tmp_path / "ws"
    target.mkdir()
    target.chmod(0o700)
    before = snapshot_workspace(target)
    target.chmod(0o755)
    try:
        changes = diff_snapshots(before, snapshot_workspace(target))
    finally:
        target.chmod(0o700)
    # The root itself is covered (path ""): a root-only mode change with
    # no child mutation still diffs. The old snapshot omitted the root
    # entry and reported no change here.
    assert [(item.path, item.change, item.kind) for item in changes] == [
        ("", "mode_changed", "dir")
    ]


def test_real_session_probe_documented_not_executed() -> None:
    """Root-owned harmless real-session probe (procedure only, never run here).

    1. Disposable dir D (mode 0700) outside every checkout; no credentials in
       env beyond the ambient login (the binary uses host config only).
    2. Spawn ``muse serve --provider meta --model muse-spark-1.3
       --contributor-mode on --disable-request-user-input --sandbox-network
       restricted --trust-workspace`` with cwd=D, minimal env allowlist.
    3. ``initialize`` {clientInfo, experimentalApi:true, userInputDialogs:false};
       expect mapping with grantedCapabilities; send ``initialized``.
    4. ``permissionProfile/list`` {}; expect rows incl. available :read-only.
    5. ``session/start`` {workspaceRoot=D, workspaceRoots=[D], providerId meta,
       modelId muse-spark-1.3, approvalMode denyUnmatched,
       permissionProfile :read-only}; expect session.sessionId + viewCursor.
    6. ``hook/list`` + ``mcpServer/list``; expect empty for :read-only.
    7. ``session/read`` {sessionId, excludeItems:true}; expect matching
       workspaceRoot/provider/model, no pendingRequests, null activeTurnId.
    8. Close stdin; expect exit within the shutdown bound; no turn started,
       no approvals, no writes outside D (D stays empty: diff proves it).
    """
    pytest.skip("Root executes the harmless real-session probe out of band")


def test_no_msp_shim_in_transport() -> None:
    import backend.aix_core.framework.execution.muse_session as mod

    text = Path(mod.__file__).read_text(encoding="utf-8")
    assert "FakeSessionPort" not in text
    assert "turn.id" not in text
