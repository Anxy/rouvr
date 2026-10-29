#!/usr/bin/env python3
"""tools/sprint_ledger.py — first-class sprint lifecycle operations.

State changes go through these functions, never hand-edits of
docs/system/ROUVR-SPRINT-LIFECYCLE.json. Bots (Worker/Supervisor) call the
CLI; tests import the functions. The module owns the state machine; if the
JSON's `machine` block drifts from it, every operation refuses.

CLI (repo root):
  python tools/sprint_ledger.py register S011 docs/tasks/rouvr/S011-<slug>.md
  python tools/sprint_ledger.py start    S011 --pre-task "..."
  python tools/sprint_ledger.py submit   S011 --tests "..." --evidence "..."
  python tools/sprint_ledger.py review   S011 approved|change_required [--comment "..."] [--verification "..."]
  python tools/sprint_ledger.py closeout S011 --ref "..."
  python tools/sprint_ledger.py block    S011 --note "..." | resume S011 --note "..."
  python tools/sprint_ledger.py status   [S011]
  python tools/sprint_ledger.py validate
Exit 0 = done; 2 = refused (message says why). Writes are atomic (tmp+replace).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LEDGER = REPO / "docs" / "system" / "ROUVR-SPRINT-LIFECYCLE.json"

STATES = ["queued", "running", "needs_review", "closeout", "done", "blocked"]
# (from, to, by, requires[ sprint fields ], cycle verdict that gates the move)
TRANSITIONS = [
    ("queued", "running", "worker", ["pre_task"], None),
    ("running", "needs_review", "worker", ["tests", "evidence"], None),
    ("needs_review", "closeout", "supervisor", ["verification"], "approved"),
    ("needs_review", "running", "supervisor", [], "change_required"),
    ("closeout", "done", "supervisor", ["closeout"], None),
    ("blocked", "queued", "either", ["note"], None),
    ("*", "blocked", "either", ["note"], None),
]
VERDICTS = ("approved", "change_required")


class LedgerError(ValueError):
    """Refusal: the ledger stays untouched."""


def _check_machine(doc: dict) -> None:
    m = doc.get("machine", {})
    want = [{"from": f, "to": t, "by": b, "requires": r} | ({"verdict": v} if v else {})
            for f, t, b, r, v in TRANSITIONS]
    if m.get("states") != STATES or m.get("transitions") != want:
        raise LedgerError("machine block in ledger JSON has drifted from sprint_ledger.TRANSITIONS — refusing")


def load(path: Path = LEDGER) -> dict:
    doc = json.loads(path.read_text())
    _check_machine(doc)
    return doc


def save(doc: dict, path: Path = LEDGER) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, indent=2) + "\n")
    tmp.replace(path)


def get(doc: dict, sid: str) -> dict:
    for s in doc["sprints"]:
        if s["id"] == sid:
            return s
    raise LedgerError(f"unknown sprint {sid}")


def _rule(frm: str, to: str):
    for f, t, by, req, verdict in TRANSITIONS:
        if (f, t) == (frm, to) or (f, t) == ("*", to):
            return by, req, verdict
    return None


def _move(doc: dict, s: dict, to: str, *, by: str, ref: str, verdict: str | None = None,
          **fields) -> None:
    frm = s["state"]
    rule = _rule(frm, to)
    if rule is None:
        raise LedgerError(f"{s['id']}: illegal transition {frm}→{to}")
    owner, requires, want = rule
    if owner not in ("either", by):
        raise LedgerError(f"{s['id']}: {frm}→{to} is owned by {owner}, not {by}")
    for k, v in fields.items():
        if v is not None:
            s[k] = v
    missing = [k for k in requires if not s.get(k)]
    if missing:
        raise LedgerError(f"{s['id']}: {frm}→{to} requires {', '.join(missing)}")
    if verdict is None and want is not None:
        cycles = s.get("cycles", [])
        verdict = cycles[-1]["verdict"] if cycles else None
    if want is not None and verdict != want:
        raise LedgerError(f"{s['id']}: {frm}→{to} needs last cycle verdict {want} (got {verdict})")
    doc["updated"] = date.today().isoformat()
    s["state"] = to
    s.setdefault("history", []).append({"state": to, "at": doc["updated"], "by": by, "ref": ref})


# ---- first-class operations (the bot-facing API) ----

def register(doc: dict, sid: str, file: str, *, depends_on: list[str] | None = None,
             today: str | None = None) -> dict:
    if any(s["id"] == sid for s in doc["sprints"]):
        raise LedgerError(f"{sid}: already registered")
    s = {"id": sid, "file": file, "state": "queued", "worker": None, "supervisor": None,
         "depends_on": list(depends_on or []),
         "cycles": [], "history": [{"state": "queued", "at": today or date.today().isoformat(),
                                    "by": "worker", "ref": "registered"}]}
    doc["sprints"].append(s)
    return s


def assign(doc: dict, sid: str, *, worker: str | None = None, supervisor: str | None = None) -> dict:
    s = get(doc, sid)
    if worker:
        s["worker"] = worker
    if supervisor:
        s["supervisor"] = supervisor
    return s


def next_sprint(doc: dict) -> dict | None:
    """Next logical task: lowest queued sprint whose depends_on are all done."""
    done = {s["id"] for s in doc["sprints"] if s["state"] == "done"}
    for s in sorted(doc["sprints"], key=lambda x: x["id"]):
        if s["state"] == "queued" and all(d in done for d in s.get("depends_on", [])):
            return s
    return None


def blockers(doc: dict, sid: str) -> list[str]:
    s = get(doc, sid)
    done = {x["id"] for x in doc["sprints"] if x["state"] == "done"}
    return [d for d in s.get("depends_on", []) if d not in done]


def claim(doc: dict, sid: str, *, by: str, pre_task: str) -> dict:
    """Claim a queued sprint for worker `by` (queued→running + holder).

    Idempotent resume: re-claiming your own running sprint returns it.
    Claiming someone else's, or one with unfinished dependencies, refuses.
    """
    s = get(doc, sid)
    if s["state"] == "running" and s.get("worker") == by:
        return s  # crashed worker re-attaching to its own claim
    if s.get("worker") and s["state"] not in ("queued",):
        raise LedgerError(f"{sid}: claimed by {s['worker']} (state={s['state']})")
    if blockers(doc, sid):
        raise LedgerError(f"{sid}: blocked by unfinished {blockers(doc, sid)}")
    doc["updated"] = date.today().isoformat()
    s["worker"] = by
    s["claimed_at"] = doc["updated"]
    _move(doc, s, "running", by="worker", ref="claim", pre_task=pre_task)
    return s


def start(doc: dict, sid: str, pre_task: str) -> dict:
    s = get(doc, sid)
    doc["updated"] = date.today().isoformat()
    _move(doc, s, "running", by="worker", ref="pre_task", pre_task=pre_task)
    return s


def submit(doc: dict, sid: str, *, tests: str, evidence: str) -> dict:
    """Worker's mandate ends here: deliver for review, never beyond it."""
    s = get(doc, sid)
    doc["updated"] = date.today().isoformat()
    _move(doc, s, "needs_review", by="worker", ref="tests", tests=tests, evidence=evidence)
    return s


def review(doc: dict, sid: str, verdict: str, *, by: str, comment: str = "",
           verification: str | None = None) -> dict:
    """Supervisor closes one review round on needs_review.

    approved → closeout (verification required); change_required → running
    (comment required). The round is appended to cycles; cycles repeat until
    an approved round. The claimant may never review (SELF_REVIEW analogue)."""
    s = get(doc, sid)
    if s["state"] != "needs_review":
        raise LedgerError(f"{sid}: review only from needs_review (state={s['state']})")
    if s.get("worker") and by == s["worker"]:
        raise LedgerError(f"{sid}: claimant {by} may not review their own work")
    if verdict not in VERDICTS:
        raise LedgerError(f"{sid}: verdict must be one of {VERDICTS}")
    if verdict == "change_required" and not comment:
        raise LedgerError(f"{sid}: change_required requires a comment")
    doc["updated"] = date.today().isoformat()
    cyc = {"round": len(s.get("cycles", [])) + 1, "verdict": verdict, "at": date.today().isoformat(),
           "by": by, "comment": comment, "ref": "review"}
    s["review"] = {"verdict": "approve" if verdict == "approved" else verdict,
                   "at": cyc["at"], "by": by, "ref": "review"}
    to = "closeout" if verdict == "approved" else "running"
    _move(doc, s, to, by="supervisor", ref="review", verdict=verdict, verification=verification)
    s.setdefault("cycles", []).append(cyc)  # append only after the move is legal
    return s


def closeout(doc: dict, sid: str, *, by: str, ref: str) -> dict:
    s = get(doc, sid)
    doc["updated"] = date.today().isoformat()
    _move(doc, s, "done", by="supervisor", ref="closeout", closeout={"at": date.today().isoformat(),
                                                                   "by": by, "ref": ref})
    return s


def block(doc: dict, sid: str, *, by: str, note: str) -> dict:
    s = get(doc, sid)
    doc["updated"] = date.today().isoformat()
    _move(doc, s, "blocked", by=by, ref="block", note=note)
    return s


def resume(doc: dict, sid: str, *, by: str, note: str) -> dict:
    """blocked→queued; releases the claim so another worker can take it."""
    s = get(doc, sid)
    doc["updated"] = date.today().isoformat()
    _move(doc, s, "queued", by=by, ref="resume", note=note)
    s["worker"], s["claimed_at"] = None, None
    return s


# ---- validation (shared with tools/validate_sprint_lifecycle.py) ----

def validate_doc(doc: dict, repo: Path = REPO) -> list[str]:
    errors: list[str] = []
    states = set(STATES)

    def legal(frm: str, to: str):
        for f, t, by, req, verdict in TRANSITIONS:
            if (f, t) == (frm, to) or (f, t) == ("*", to):
                return by, req, verdict
        return None

    for s in doc["sprints"]:
        sid, state = s["id"], s["state"]
        if state not in states:
            errors.append(f"{sid}: unknown state {state!r}")
        if not (repo / s["file"]).exists():
            errors.append(f"{sid}: sprint file missing: {s['file']}")
        if state == "done":
            for key in ("review", "verification", "closeout"):
                if not s.get(key):
                    errors.append(f"{sid}: done but missing {key}")
        cycles = s.get("cycles", [])
        for i, c in enumerate(cycles, 1):
            if c.get("round") != i:
                errors.append(f"{sid}: cycle round {c.get('round')} != {i} (not sequential)")
            if c.get("verdict") not in VERDICTS:
                errors.append(f"{sid}: cycle {i} verdict must be approved|change_required")
            if c.get("verdict") == "change_required" and not c.get("comment"):
                errors.append(f"{sid}: cycle {i} change_required needs a comment")
        last = cycles[-1]["verdict"] if cycles else None
        if state in ("closeout", "done") and last != "approved":
            errors.append(f"{sid}: {state} requires last cycle verdict approved (got {last})")
        if state == "running" and cycles and last != "change_required":
            errors.append(f"{sid}: running after review requires change_required (got {last})")

        hist = s.get("history", [])
        if not hist or hist[-1]["state"] != state:
            errors.append(f"{sid}: history does not end at current state {state!r}")
        if len(cycles) > sum(1 for h in hist if h["state"] == "needs_review"):
            errors.append(f"{sid}: more cycles than needs_review entries")
        for prev, cur in zip(hist, hist[1:]):
            r = legal(prev["state"], cur["state"])
            if r is None:
                errors.append(f"{sid}: illegal transition {prev['state']}→{cur['state']}")
                continue
            for need in r[1]:
                if not s.get(need):
                    errors.append(f"{sid}: {prev['state']}→{cur['state']} requires {need}")
        ci = 0
        for prev, cur in zip(hist, hist[1:]):
            if prev["state"] != "needs_review":
                continue
            r = legal(prev["state"], cur["state"])
            if r is None or not r[2]:
                continue
            got = cycles[ci]["verdict"] if ci < len(cycles) else None
            if got != r[2]:
                errors.append(f"{sid}: exit from needs_review round {ci + 1} needs verdict "
                              f"{r[2]} (cycle says {got})")
            ci += 1
    return errors


def main() -> int:
    ap = argparse.ArgumentParser(prog="sprint_ledger")
    ap.add_argument("--file", type=Path, default=LEDGER, help="ledger JSON (default: docs/system/…)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, args in (
        ("register", ("file",)), ("start", ("--pre-task",)), ("submit", ("--tests", "--evidence")),
        ("review", ("verdict",)), ("closeout", ("--ref",)), ("block", ("--note",)),
        ("resume", ("--note",)), ("status", ()), ("validate", ()),
        ("next", ()), ("claim", ("--by",)), ("claim-next", ("--by",)),
    ):
        p = sub.add_parser(name)
        if name not in ("validate", "next", "claim-next"):
            p.add_argument("sid", nargs="?" if name == "status" else None)
        if name == "register":
            p.add_argument("sheet", metavar="file")
            p.add_argument("--depends-on", default="")
        if name in ("start", "claim", "claim-next"):
            p.add_argument("--pre-task", required=True)
        if name in ("claim", "claim-next"):
            p.add_argument("--by", required=True)
        if name == "submit":
            p.add_argument("--tests", required=True); p.add_argument("--evidence", required=True)
        if name == "review":
            p.add_argument("verdict", choices=VERDICTS)
            p.add_argument("--comment", default=""); p.add_argument("--verification"); p.add_argument("--by", default="supervisor")
        if name == "closeout":
            p.add_argument("--ref", required=True); p.add_argument("--by", default="supervisor")
        if name in ("block", "resume"):
            p.add_argument("--note", required=True); p.add_argument("--by", default="supervisor")
    a = ap.parse_args()
    try:
        doc = load(a.file)
        if a.cmd == "validate":
            errs = validate_doc(doc)
            if errs:
                print("lifecycle INVALID:"); print("\n".join(f"  - {e}" for e in errs)); return 2
            print(f"lifecycle valid ({len(doc['sprints'])} sprints)"); return 0
        if a.cmd == "status":
            for s in doc["sprints"]:
                if a.sid is None or s["id"] == a.sid:
                    print(f"{s['id']}: {s['state']} (cycles={len(s.get('cycles', []))})")
            return 0
        if a.cmd == "next":
            n = next_sprint(doc)
            print(f"{n['id']}: {n['file']}" if n else "none")
            return 0
        if a.cmd == "register":
            s = register(doc, a.sid, a.sheet,
                         depends_on=[d for d in a.depends_on.split(",") if d])
        elif a.cmd == "claim":
            s = claim(doc, a.sid, by=a.by, pre_task=a.pre_task)
        elif a.cmd == "claim-next":
            n = next_sprint(doc)
            if n is None:
                print("none claimable")
                return 0
            s = claim(doc, n["id"], by=a.by, pre_task=a.pre_task)
        elif a.cmd == "start":
            s = start(doc, a.sid, a.pre_task)
        elif a.cmd == "submit":
            s = submit(doc, a.sid, tests=a.tests, evidence=a.evidence)
        elif a.cmd == "review":
            s = review(doc, a.sid, a.verdict, by=a.by, comment=a.comment, verification=a.verification)
        elif a.cmd == "closeout":
            s = closeout(doc, a.sid, by=a.by, ref=a.ref)
        elif a.cmd == "block":
            s = block(doc, a.sid, by=a.by, note=a.note)
        else:
            s = resume(doc, a.sid, by=a.by, note=a.note)
        errs = validate_doc(doc)  # never write an invalid ledger
        mine = [e for e in errs if e.startswith(f"{getattr(a, 'sid', '')}:")]
        if errs:
            raise LedgerError("post-write validation failed: " + "; ".join(mine or errs))
        save(doc, a.file)
        print(f"{s['id']}: {s['state']}")
        return 0
    except LedgerError as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
