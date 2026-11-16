"""rouvr/testrunner.py — run the spec's test command in the PR workspace (local) with a hard timeout.
A remote testrun-runner client can implement the same `TestRunner` protocol."""
from __future__ import annotations

import asyncio
import hashlib
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from rouvr.errors import Code, RouvrError
from rouvr.ids import sanitize_for_log


@dataclass
class TestRun:
    run_id: str
    passed: bool
    exit_code: int
    output_tail: str
    output_full: str = ""  # full sanitized output for outcome parsing; tail is display-only


class TestRunner(Protocol):
    async def run(self, cwd: Path, command: list[str], timeout_s: int, run_key: str) -> TestRun: ...


async def _exec(argv: list[str], cwd: Path, timeout_s: int, *, container_name: str | None = None) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(*argv, cwd=str(cwd), stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.STDOUT)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout_s)
    except asyncio.TimeoutError as e:
        proc.kill()
        await proc.wait()
        if container_name:  # killing the docker CLI leaves the container running
            try:
                rm = await asyncio.create_subprocess_exec("docker", "rm", "-f", container_name,
                                                          stdout=asyncio.subprocess.DEVNULL,
                                                          stderr=asyncio.subprocess.DEVNULL)
                await asyncio.wait_for(rm.wait(), 10)
            except Exception:
                pass
        raise RouvrError(Code.DOWNSTREAM, "tests timed out") from e
    # Full output (bounded) for outcome parsing; callers truncate for display.
    # A tail-only parse silently drops criterion groups whose PASSED lines fall
    # outside the window (live finding: the last sealed group went unrecorded).
    full = sanitize_for_log(out.decode(errors="replace"), limit=262144)
    return proc.returncode or 0, full


def _run_id(prefix: str, run_key: str) -> str:
    return f"{prefix}-{hashlib.sha256(run_key.encode()).hexdigest()[:16]}"


def _docker_in_docker() -> bool:
    return Path("/.dockerenv").exists()


def _volumes_from() -> str:
    """Share the worker's mounts with the test container.

    When the test worker itself runs in Docker and talks to the host docker
    daemon via a mounted socket, ``-v /data/...:/src`` resolves on the *host*
    and is empty. ``--volumes-from`` keeps the clone visible.
    """
    explicit = (os.environ.get("ROUVR_DOCKER_VOLUMES_FROM") or "").strip()
    if explicit:
        return explicit
    if _docker_in_docker():
        return (os.environ.get("HOSTNAME") or "").strip()
    return ""


def _host_mount_cwd(cwd: Path) -> str:
    """Rewrite /data/... to the host bind path when ROUVR_DOCKER_HOST_DATA is set."""
    cwd_s = str(cwd.resolve())
    host_data = (os.environ.get("ROUVR_DOCKER_HOST_DATA") or "").rstrip("/")
    if host_data and (cwd_s == "/data" or cwd_s.startswith("/data/")):
        return host_data + cwd_s[len("/data"):]
    return cwd_s


class ContainerTestRunner:
    """Run tests in a clean container: the clone is mounted, no network."""

    def __init__(self, image: str) -> None:
        self.image = image

    async def run(self, cwd: Path, command: list[str], timeout_s: int, run_key: str) -> TestRun:
        name = f"{_run_id('rouvr', run_key)}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        # The mount is writable: `cwd` is already an ephemeral per-run clone
        # (created and destroyed by the caller), and test side effects
        # (.pytest_cache, module-level sqlite files, snapshot artifacts)
        # legitimately land next to the code. Isolation that matters (no
        # network, pinned image, ephemeral container) is unchanged.
        base = ["docker", "run", "--rm", "--name", name, "--network", "none", "--pull", "never"]
        volumes_from = _volumes_from()
        if volumes_from:
            argv = [*base, "--volumes-from", volumes_from, "-w", str(cwd.resolve()),
                    self.image, *command]
        else:
            argv = [*base, "-v", f"{_host_mount_cwd(cwd)}:/src", "-w", "/src",
                    self.image, *command]
        try:
            rc, full = await _exec(argv, cwd, timeout_s, container_name=name)
        except FileNotFoundError as e:
            raise RouvrError(Code.DOWNSTREAM, "docker is not installed") from e
        return TestRun(run_id=_run_id("ctr", run_key), passed=rc == 0, exit_code=rc,
                        output_tail=full[-2000:], output_full=full)
