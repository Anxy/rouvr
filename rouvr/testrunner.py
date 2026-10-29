"""rouvr/testrunner.py — run the spec's test command in the PR workspace (local) with a hard timeout.
A remote testrun-runner client can implement the same `TestRunner` protocol."""
from __future__ import annotations

import asyncio
import hashlib
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


class TestRunner(Protocol):
    async def run(self, cwd: Path, command: list[str], timeout_s: int, run_key: str) -> TestRun: ...


async def _exec(argv: list[str], cwd: Path, timeout_s: int) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(*argv, cwd=str(cwd), stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.STDOUT)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout_s)
    except asyncio.TimeoutError as e:
        proc.kill()
        await proc.wait()
        raise RouvrError(Code.DOWNSTREAM, "tests timed out") from e
    return proc.returncode or 0, sanitize_for_log(out.decode(errors="replace")[-4000:])


def _run_id(prefix: str, run_key: str) -> str:
    return f"{prefix}-{hashlib.sha256(run_key.encode()).hexdigest()[:16]}"


class LocalTestRunner:
    async def run(self, cwd: Path, command: list[str], timeout_s: int, run_key: str) -> TestRun:
        rc, tail = await _exec(command, cwd, timeout_s)
        return TestRun(run_id=_run_id("local", run_key), passed=rc == 0, exit_code=rc, output_tail=tail)


class ContainerTestRunner:
    """Run tests in a clean container: the clone is mounted read-only, no network."""

    def __init__(self, image: str) -> None:
        self.image = image

    async def run(self, cwd: Path, command: list[str], timeout_s: int, run_key: str) -> TestRun:
        argv = ["docker", "run", "--rm", "--network", "none", "--pull", "never",
               "-v", f"{cwd}:/src:ro", "-w", "/src", self.image, *command]
        try:
            rc, tail = await _exec(argv, cwd, timeout_s)
        except FileNotFoundError as e:
            raise RouvrError(Code.DOWNSTREAM, "docker is not installed") from e
        return TestRun(run_id=_run_id("ctr", run_key), passed=rc == 0, exit_code=rc, output_tail=tail)
