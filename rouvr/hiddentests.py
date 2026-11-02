"""rouvr/hiddentests.py — hidden acceptance-test store (R8a).

Written once at task creation by the test_author persona (never the coder),
hashed and locked. The coder workspace must have no copy (checked by the
runner). Hidden tests are pytest files; each test name embeds its criterion:
`test_<criterion_id>_...` (e.g. `test_c1_returns_200`). The runner parses
per-criterion outcomes from `pytest -v` output.
"""
from __future__ import annotations

import ast
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from rouvr.cheatscan import literals_in
from rouvr.errors import Code, RouvrError

MANIFEST = "MANIFEST.json"
HIDDEN_FILE_PREFIX = "test_rouvr_hidden_"


def is_hidden_suite_name(name: str) -> bool:
    """Hidden suite filenames live in a reserved namespace: test_rouvr_hidden_*.py.

    The prefix satisfies both constraints at once: it matches pytest's default
    collection (test_*.py) so the sealed suite is collected in the clean-room
    clone, and no coder names its own tests that way, so a coder file can never
    collide by basename (task-34 lesson: identical basenames abort collection
    with an import-file-mismatch error and fail the whole repo run)."""

    return (isinstance(name, str) and "/" not in name and name not in {".", ".."}
            and name.startswith(HIDDEN_FILE_PREFIX) and name.endswith(".py")
            and len(name) > len(HIDDEN_FILE_PREFIX) + len(".py"))
_TRIVIAL = frozenset({"", "test", "tests", "true", "false", "none", "ok", "200", "0", "1"})


@dataclass(frozen=True)
class HiddenManifest:
    task_id: str
    files: tuple[str, ...]
    tree_sha256: str
    created_by: str


def tree_hash(files: dict[str, str]) -> str:
    h = hashlib.sha256()
    for name in sorted(files):
        h.update(name.encode() + b"\0" + files[name].encode() + b"\0")
    return h.hexdigest()


def _check_task_id(task_id: str) -> None:
    if not task_id or "/" in task_id or "\\" in task_id or ".." in task_id:
        raise RouvrError(Code.CONTRACT, f"invalid task id: {task_id!r}")


def write_hidden_suite(root: Path, *, task_id: str, files: dict[str, str], created_by: str) -> HiddenManifest:
    """Lock a hidden suite. Refuses overwrite: locked at creation."""
    _check_task_id(task_id)
    task_dir = root / task_id
    manifest_path = task_dir / MANIFEST
    if manifest_path.exists():
        raise RouvrError(Code.CONTRACT, f"hidden suite for {task_id} is locked")
    if not files:
        raise RouvrError(Code.CONTRACT, "hidden suite needs at least one file")
    for name, content in files.items():
        try:
            compile(content, name, "exec")
        except (SyntaxError, ValueError) as e:
            raise RouvrError(Code.CONTRACT, f"hidden test {name} does not parse: {e}") from e
    task_dir.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        if "/" in name or name in {".", ".."} or not name.endswith(".py"):
            raise RouvrError(Code.CONTRACT, f"bad hidden test name: {name}")
        (task_dir / name).write_text(content)
    manifest = HiddenManifest(task_id=task_id, files=tuple(sorted(files)), tree_sha256=tree_hash(files),
                              created_by=created_by)
    manifest_path.write_text(json.dumps(manifest.__dict__, indent=2, sort_keys=True))
    return manifest


def fetch_hidden_suite(root: Path, task_id: str) -> tuple[Path, HiddenManifest]:
    """Fetch at verify time only. Verifies the creation hash (tamper check)."""
    _check_task_id(task_id)
    task_dir = root / task_id
    manifest_path = task_dir / MANIFEST
    if not manifest_path.exists():
        raise RouvrError(Code.EVIDENCE, f"no hidden suite for {task_id}")
    try:
        raw = json.loads(manifest_path.read_text())
        if not isinstance(raw, dict):
            raise TypeError("manifest must be an object")
        raw["files"] = tuple(raw["files"])  # JSON roundtrip: list back to tuple
        manifest = HiddenManifest(**raw)
        files = {n: (task_dir / n).read_text() for n in manifest.files}
    except (ValueError, TypeError, KeyError, OSError) as e:
        raise RouvrError(Code.EVIDENCE, f"corrupt hidden suite for {task_id}: {e}") from e
    if tree_hash(files) != manifest.tree_sha256:
        raise RouvrError(Code.EVIDENCE, f"hidden suite for {task_id} changed since creation")
    return task_dir, manifest


def assert_coder_clean(coder_ws: Path, task_dir: Path, manifest: HiddenManifest) -> None:
    """The coder workspace must hold no copy of the hidden tests (checked by the runner)."""
    if not coder_ws.exists():
        return
    hidden_names = set(manifest.files)
    hidden_bytes = {(task_dir / n).read_bytes() for n in manifest.files}
    hidden_sizes = {len(b) for b in hidden_bytes}
    for path in sorted(coder_ws.rglob("*")):
        if not path.is_file() or ".git/" in str(path):
            continue
        # Same filename alone is not a leak (task-32 lesson: the coder's own
        # tests/test_durations.py collided by name with the hidden suite but
        # held different bytes). Only identical bytes prove a copy.
        if path.name in hidden_names:
            if path.read_bytes() in hidden_bytes:
                raise RouvrError(Code.EVIDENCE, f"coder workspace leaks hidden tests: {path.name}")
            continue
        if path.stat().st_size in hidden_sizes and path.read_bytes() in hidden_bytes:
            raise RouvrError(Code.EVIDENCE, f"coder workspace leaks hidden tests: {path.name}")


def hidden_literals(hidden_dir: Path) -> frozenset[str]:
    """Distinctive string literals from hidden tests, for the cheat-scan LITERAL rule."""
    out: set[str] = set()
    for path in sorted(hidden_dir.glob("*.py")):
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                token = node.value.strip().lower()
                if len(token) >= 6 and token not in _TRIVIAL and " " not in token:
                    out.add(token)
    return frozenset(out)


def criteria_literals(*texts: str) -> frozenset[str]:
    """Quoted tokens from task criteria/spec text that compliant prod code may
    contain (e.g. a mandated `__all__` entry). Same token shape as
    hidden_literals so the two sets compare directly; bracket/quote edge
    punctuation from prose like `['parse_duration']` is stripped."""
    out: set[str] = set()
    for text in texts:
        for line in (text or "").splitlines():
            for lit in literals_in(line):
                token = lit.strip().strip("[](){}\"'")
                if len(token) >= 6 and token not in _TRIVIAL and " " not in token:
                    out.add(token)
    return frozenset(out)


def parse_criterion_outcomes(pytest_v_output: str) -> dict[str, bool]:
    """Map test_<criterion_id>_... results from `pytest -v` output (True = passed)."""
    outcomes: dict[str, bool] = {}
    for line in pytest_v_output.splitlines():
        line = line.strip()
        if "::test_" not in line or (" PASSED" not in line and " FAILED" not in line and " ERROR" not in line):
            continue
        name = line.split("::", 1)[1].split()[0]
        if not name.startswith("test_"):
            continue
        criterion = name[len("test_"):].split("_", 1)[0]
        passed = " PASSED" in line
        outcomes[criterion] = outcomes.get(criterion, True) and passed
    return outcomes
