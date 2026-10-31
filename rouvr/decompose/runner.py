"""Decomposer-lite run gate and parent check (S021)."""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path


def _under(path: str, roots: list) -> bool:
    return any(r in (".", "./") or path == r or path.startswith(r.rstrip("/") + "/")
               for r in roots)


def _bundle_hashes(node, root: Path) -> dict:
    out = {}
    kind = node[0]
    if kind == "test_passes":
        f = root / node[1].split("::", 1)[0]
        out[str(f)] = hashlib.sha256(f.read_bytes()).hexdigest() if f.is_file() else "missing"
    elif kind == "and":
        for sub in node[1]:
            out.update(_bundle_hashes(sub, root))
    return out


def run_gate(task: dict, repo_root, _run=None, _changed=None) -> dict:
    """Run the verifier plus scope guards. Pinned touch fails automatically.

    Receipt: head SHA, verdict, details, bundle hashes, pinned touches.
    """
    from rouvr.decompose import validator as V
    _run = _run or V.run_verifier
    _changed = _changed or V.changed_files
    try:
        node = V.parse_verifier(task.get("verifier"))
    except V.VerifierParseError as e:
        return {"task": task.get("id"), "verdict": "fail", "reason": f"unparsable: {e}",
                "bundle_hashes": {}, "pinned_touched": [], "changed": []}
    head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                          check=False, cwd=repo_root).stdout.strip()
    outcome, detail = _run(node, Path(repo_root))
    changed = _changed(Path(repo_root))
    pinned_touched = sorted({f for f in changed if _under(f, task.get("pinned_paths", []))})
    outside = sorted({f for f in changed
                      if not _under(f, task.get("allowed_paths", []))})
    verdict = "pass" if outcome == "PASS" and not pinned_touched and not outside else "fail"
    return {"head_sha": head, "task": task.get("id"), "verdict": verdict,
            "reason": detail if verdict == "pass" else "; ".join(
                [detail] +
                ([f"pinned touched: {pinned_touched}"] if pinned_touched else []) +
                ([f"outside allowed: {outside}"] if outside else [])),
            "bundle_hashes": _bundle_hashes(node, Path(repo_root)),
            "pinned_touched": pinned_touched, "changed": sorted(set(changed))}


def check_parent(parent: tuple, children: list, repo_root, _run=None) -> dict:
    """Run the parent test on the combined revision.

    Green children plus a red parent is DECOMPOSITION_FAILURE (send the
    split back to decompose); anything else unexpected is an error.
    """
    from rouvr.decompose import validator as V
    _run = _run or V.run_verifier
    root = Path(repo_root)
    pres = _run(parent, root)
    cres = [_run(c, root) for c in children]
    if all(o == "PASS" for o, _ in cres) and pres[0] == "FAIL_ASSERT":
        return {"verdict": "DECOMPOSITION_FAILURE", "action": "re-decompose",
                "parent": pres[1], "children": [d for _, d in cres]}
    if pres[0] == "PASS" and all(o == "PASS" for o, _ in cres):
        return {"verdict": "COMPOSES", "parent": pres[1]}
    return {"verdict": "ERROR",
            "parent": pres, "children": cres}
