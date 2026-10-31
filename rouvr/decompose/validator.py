#!/usr/bin/env python3
"""Validator for Decomposer-lite v1.3 roadmaps (S021 STEP 1).

Mini-language (anything else is rejected at parse):
  {"test_passes": [node_id, bundle_sha]}   pytest node, bundle pinned by sha256
  {"no_changes_outside": [[paths]]}        changed files must sit under paths
  {"path_absent": [path]}                  path must not exist
  {"and": [verifier, ...]}                 all legs must pass

Outcome classes: PASS | FAIL_ASSERT (a check evaluated false, deterministic)
| ERROR (parse/timeout/collection/import/crash — never counts as assertion).

A task is valid only if: it parses; allowed/pinned paths do not overlap
(equal or containing either way); it covers >=1 known requirement; and 3
runs on the current head all FAIL as assertions with identical detail
(a verifier that passes on baseline is hollow; disagreement is flaky).

Usage: python rouvr/decompose/validator.py roadmap.json [--runs N] [--expect fail|pass]
Exit 0 iff every task is valid and every requirement is covered.
--expect fail (default): the TDD gate — verifiers must fail as assertions,
  unanimously, proving they catch the bug before the fix.
--expect pass: the completion gate — verifiers must pass unanimously,
  proving the work is done and deterministic.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

PRIMITIVES = ("test_passes", "no_changes_outside", "path_absent", "and")
RUNS = 3
PYTEST_TIMEOUT_S = 120


class VerifierParseError(ValueError):
    pass


def repo_root() -> Path:
    out = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                         capture_output=True, text=True, check=False)
    if out.returncode != 0:
        raise VerifierParseError("not inside a git checkout")
    return Path(out.stdout.strip())


def parse_verifier(v) -> tuple:
    """Normalize one verifier to a tuple tree or raise VerifierParseError."""
    if not isinstance(v, dict) or len(v) != 1:
        raise VerifierParseError(f"verifier must be a single-key object, got {v!r:.80}")
    (kind, arg), = v.items()
    if kind not in PRIMITIVES:
        raise VerifierParseError(f"unknown primitive {kind!r}")
    if kind == "test_passes":
        if (not isinstance(arg, list) or len(arg) != 2
                or not all(isinstance(a, str) for a in arg) or "::" not in arg[0]):
            raise VerifierParseError(f"test_passes needs [node_id::test, sha256], got {arg!r:.80}")
        return ("test_passes", arg[0], arg[1])
    if kind == "no_changes_outside":
        if (not isinstance(arg, list) or len(arg) != 1 or not isinstance(arg[0], list)
                or not arg[0] or not all(isinstance(p, str) for p in arg[0])):
            raise VerifierParseError(f"no_changes_outside needs [[paths...]], got {arg!r:.80}")
        return ("no_changes_outside", tuple(arg[0]))
    if kind == "path_absent":
        if not isinstance(arg, list) or len(arg) != 1 or not isinstance(arg[0], str):
            raise VerifierParseError(f"path_absent needs [path], got {arg!r:.80}")
        return ("path_absent", arg[0])
    if (not isinstance(arg, list) or not arg
            or not all(isinstance(s, dict) for s in arg)):
        raise VerifierParseError(f"and needs a non-empty list of verifiers, got {arg!r:.80}")
    return ("and", tuple(parse_verifier(s) for s in arg))


def changed_files(root: Path) -> list[str]:
    out = subprocess.run(["git", "status", "--porcelain", "-uall"],
                         capture_output=True, text=True, check=False, cwd=root)
    if out.returncode != 0:
        raise VerifierParseError("git status failed")
    files = []
    for line in out.stdout.splitlines():
        path = line[3:]
        if " -> " in path:  # renames: judge the new side
            path = path.split(" -> ", 1)[1]
        files.append(path.strip().strip('"'))
    return files


def run_verifier(node: tuple, root: Path) -> tuple[str, str]:
    """One run. Returns (PASS|FAIL_ASSERT|ERROR, detail)."""
    kind = node[0]
    if kind == "test_passes":
        _, node_id, bundle_sha = node
        test_file = root / node_id.split("::", 1)[0]
        if not test_file.is_file():
            return ("ERROR", f"no test file for {node_id}")
        actual = hashlib.sha256(test_file.read_bytes()).hexdigest()
        if actual != bundle_sha:
            return ("ERROR", f"stale bundle for {node_id}")
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "pytest", node_id, "-q", "-p", "no:cacheprovider"],
                capture_output=True, text=True, timeout=PYTEST_TIMEOUT_S, cwd=root)
        except subprocess.TimeoutExpired:
            return ("ERROR", f"timeout running {node_id}")
        tail = "\n".join(proc.stdout.splitlines()[-3:] + proc.stderr.splitlines()[-1:])
        if proc.returncode == 0:
            return ("PASS", f"{node_id} passed")
        failed = re.search(r"(\d+) failed", tail)
        errored = re.search(r"(\d+) error", tail, re.IGNORECASE)
        if failed and not errored:
            return ("FAIL_ASSERT", f"{node_id}: {failed.group(0)}")
        return ("ERROR", f"{node_id}: exit {proc.returncode}: {tail[:200]}")
    if kind == "no_changes_outside":
        _, paths = node
        bad = [f for f in changed_files(root)
               if not any(f == a or f.startswith(a.rstrip("/") + "/") for a in paths)]
        if bad:
            shown = ", ".join(sorted(bad)[:5]) + ("..." if len(bad) > 5 else "")
            return ("FAIL_ASSERT", f"changes outside {list(paths)}: {shown}")
        return ("PASS", f"tree confined to {list(paths)}")
    if kind == "path_absent":
        if (root / node[1]).exists():
            return ("FAIL_ASSERT", f"{node[1]} exists")
        return ("PASS", f"{node[1]} absent")
    # and
    details, saw_fail, saw_error = [], False, False
    for sub in node[1]:
        outcome, detail = run_verifier(sub, root)
        details.append(detail)
        saw_error = saw_error or outcome == "ERROR"
        saw_fail = saw_fail or outcome == "FAIL_ASSERT"
    if saw_error:
        return ("ERROR", "; ".join(details))
    if saw_fail:
        return ("FAIL_ASSERT", "; ".join(details))
    return ("PASS", "; ".join(details))


def paths_overlap(allowed: list, pinned: list) -> str | None:
    for a in allowed:
        for p in pinned:
            if a == p or a.startswith(p.rstrip("/") + "/") or p.startswith(a.rstrip("/") + "/"):
                return f"allowed {a!r} overlaps pinned {p!r}"
    return None


def validate_task(task: dict, requirement_ids: set[str], root: Path,
                  runs: int = RUNS, expect: str = "fail") -> tuple[bool, list[str]]:
    problems: list[str] = []
    try:
        node = parse_verifier(task.get("verifier"))
    except VerifierParseError as e:
        return False, [f"parse: {e}"]
    overlap = paths_overlap(task.get("allowed_paths", []), task.get("pinned_paths", []))
    if overlap:
        problems.append(f"overlap: {overlap}")
    covers = task.get("covers", [])
    if not covers or not all(c in requirement_ids for c in covers):
        problems.append(f"coverage: task covers unknown/empty requirements {covers!r}")
    if problems:
        return False, problems
    results = [run_verifier(node, root) for _ in range(runs)]
    outcomes = {r[0] for r in results}
    details = {d for _, d in results}
    if expect == "pass":
        if outcomes == {"PASS"} and len(details) == 1:
            return True, [f"passes x{runs}, unanimous: {results[0][1][:160]}"]
        return False, [f"not-green: {results}"]
    if outcomes == {"PASS"}:
        return False, ["hollow: verifier passes on the current head"]
    if "ERROR" in outcomes:
        return False, [f"error-class: {sorted({d for o, d in results if o == 'ERROR'})}"]
    if outcomes != {"FAIL_ASSERT"} or len(details) != 1:
        return False, [f"flaky: {results}"]
    if task.get("verifier_fails_on_head") != "assertion":
        return False, ["contract: verifier_fails_on_head must be 'assertion'"]
    return True, [f"fails-as-assertion x{runs}, unanimous: {results[0][1][:160]}"]


def validate_doc(doc: dict, root: Path, runs: int = RUNS, expect: str = "fail") -> tuple[bool, dict]:
    requirement_ids = {r["id"] for r in doc.get("requirements", [])}
    report: dict = {"tasks": {}, "coverage": []}
    ok = True
    for task in doc.get("tasks", []):
        valid, notes = validate_task(task, requirement_ids, root, runs, expect)
        report["tasks"][task.get("id", "?")] = {"valid": valid, "notes": notes}
        ok = ok and valid
    covered: dict[str, list] = {}
    for task in doc.get("tasks", []):
        for c in task.get("covers", []):
            covered.setdefault(c, []).append(task.get("id"))
    for rid in sorted(requirement_ids):
        if rid not in covered:
            report["coverage"].append(f"{rid}: NO COVERING TASK")
            ok = False
    for task in doc.get("tasks", []):
        if not any(task.get("id") in v for v in covered.values()):
            report["coverage"].append(f"{task.get('id')}: covers nothing")
            ok = False
    return ok, report


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: validator.py roadmap.json [--runs N] [--expect fail|pass]", file=sys.stderr)
        return 2
    runs, expect = RUNS, "fail"
    rest = list(argv[2:])
    while rest:
        flag = rest.pop(0)
        if flag == "--runs":
            runs = int(rest.pop(0))
        elif flag == "--expect":
            expect = rest.pop(0)
            if expect not in ("fail", "pass"):
                print("validator ERROR: --expect must be fail|pass", file=sys.stderr)
                return 2
        else:
            print(f"validator ERROR: unknown flag {flag}", file=sys.stderr)
            return 2
    try:
        doc = json.loads(Path(argv[1]).read_text())
        root = repo_root()
        ok, report = validate_doc(doc, root, runs, expect)
    except (OSError, ValueError, VerifierParseError) as e:
        print(f"validator ERROR: {e}", file=sys.stderr)
        return 2
    for tid, res in report["tasks"].items():
        status = "valid" if res["valid"] else "INVALID"
        print(f"{tid}: {status}")
        for note in res["notes"]:
            print(f"  - {note}")
    for line in report["coverage"]:
        print(f"coverage: {line}")
    print("roadmap VALID" if ok else "roadmap INVALID")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
