"""Backfill filesystem sprint sheets (docs/tasks/rouvr/S*.md) into WorkHub.

Mapping: each sheet = one slice (one project `sNNN-<slug>` under tenant
`supervisor`); each "### Task X.Y" = one task titled "[SNNN X.Y] <name>",
criteria from the sheet §7 AC lines, else the task's own "- AC:" bullets,
else its Scope line. S018 (design note, no tasks) is skipped for tasks.

Status: PASS sheets -> walked to done with the §11 closeout posted as
`closeout` evidence and the verdict in the done receipt. OPEN sheets
(S010, S017, S019, S021, S022, S023) -> open tasks, nothing else.

Honesty: every description carries source "backfill:<path>@<commit>" and
"Imported historical record - built before Rouvr managed its own tasks;
not a live Rouvr run." The same sentence goes on done receipts.

Idempotent: matches existing tasks on the "[SNNN X.Y]" title prefix;
re-runs create nothing. Live run needs --live (default is dry run on a
throwaway sqlite DB, which also serves as the REVIEW would-create list).
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SHEETS = sorted((ROOT / "docs/tasks/rouvr").glob("S*.md"))
TENANT = "supervisor"
OPEN_SHEETS = {"S010", "S017", "S019", "S021", "S022", "S023"}
SKIP_SHEETS = {"S018"}  # design note, no tasks -> knowledge backfill
HIST_NOTE = ("Imported historical record - built before Rouvr managed its own "
             "tasks; not a live Rouvr run.")

TASK_RE = re.compile(r"^### Task (\d+\.\d+)\s+[-\u2014]\s+(.*?)(?:\s+\(([A-Z-]+)\))?\s*$")
AC_RE = re.compile(r"^AC-\d+:\s*(.+)")
BULLET_AC_RE = re.compile(r"^-\s+AC:\s*(.+)")


def head_commit() -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                          capture_output=True, text=True).stdout.strip()


def parse_sheet(path: Path) -> dict:
    text = path.read_text().splitlines()
    sid = path.stem.split("-")[0]
    title = ""
    for i, ln in enumerate(text):
        if ln.strip() == "## Sprint Title":
            title = next((l.strip() for l in text[i + 1:] if l.strip()), "")
            break
    verdict = ""
    m = re.search(r"SPRINT CLOSEOUT REPORT\s+[-\u2014]\s+%s:\s*([A-Z]*)" % sid,
                  "\n".join(text))
    if m:
        verdict = m.group(1).strip()
    status = "done" if verdict == "PASS" else "open"
    # §7 AC lines, only when §7 is Acceptance Criteria
    ac: list[str] = []
    for i, ln in enumerate(text):
        if re.match(r"## 7\.\s+Acceptance Criteria", ln):
            for l in text[i + 1:]:
                if l.startswith("## "):
                    break
                m2 = AC_RE.match(l.strip().strip("`"))
                if m2:
                    ac.append(m2.group(1).strip())
            break
    tasks: list[dict] = []
    cur: dict | None = None
    for ln in text:
        m3 = TASK_RE.match(ln.strip())
        if m3:
            if cur:
                tasks.append(cur)
            cur = {"num": m3.group(1), "name": m3.group(2).strip(),
                   "bullets": [], "scope": ""}
        elif cur is not None:
            if ln.startswith("## ") or ln.startswith("### "):
                tasks.append(cur)
                cur = None
            else:
                b = BULLET_AC_RE.match(ln.strip())
                if b:
                    cur["bullets"].append(b.group(1).strip())
                elif ln.strip().startswith("- Scope:") and not cur["scope"]:
                    cur["scope"] = ln.strip()[len("- Scope:"):].strip()
    if cur:
        tasks.append(cur)
    for t in tasks:
        t["criteria"] = ac or t["bullets"] or (
            ["Scope: " + t["scope"]] if t["scope"] else ["Imported as-is; see sheet."])
    return {"sid": sid, "path": str(path.relative_to(ROOT)), "title": title,
            "verdict": verdict, "status": status, "tasks": tasks}


def inventory() -> list[dict]:
    return [parse_sheet(p) for p in SHEETS if p.stem.split("-")[0] not in SKIP_SHEETS]


def sheet_project(sheet: dict) -> str:
    slug = Path(sheet["path"]).stem[len(sheet["sid"]) + 1:]
    return ("%s-%s" % (sheet["sid"].lower(), slug))[:64]


def run(client, commit: str, sheets: list[dict], tenant: str,
        stack: dict | None, live: bool) -> dict:
    """Create (and close PASS) tasks. Returns counts. stack=None for TestClient."""
    made, skipped, closed = 0, 0, 0
    q = client.get("/v1/tasks?limit=1000", headers=stack).json()
    existing = {t.get("title", "") for t in q.get("items", q)}
    for sheet in sheets:
        proj = sheet_project(sheet)
        client.post("/v1/projects", json={"tenant": tenant, "project": proj},
                    headers=stack)
        desc_base = "%s\n\nSource: backfill:%s@%s\n%s" % (
            sheet["title"], sheet["path"], commit, HIST_NOTE)
        for t in sheet["tasks"]:
            title = "[%s %s] %s" % (sheet["sid"], t["num"], t["name"])
            if title in existing:
                skipped += 1
                continue
            made += 1
            if not live:
                continue
            r = client.post("/v1/tasks_with_contract", json={
                "title": title, "intent": "supervisor", "tenant": tenant,
                "project": proj, "contract": {
                    "project_id": proj, "description": desc_base,
                    "acceptance_criteria": t["criteria"]}}, headers=stack)
            r.raise_for_status()
            tid = r.json()["id"]
            if sheet["status"] == "done":
                client.post(f"/v1/tasks/{tid}/transition",
                            json={"to": "claimed",
                                  "idempotency_key": "backfill:%s:%s:claim"
                                  % (sheet["sid"], t["num"]),
                                  "note": "backfill claim"}, headers=stack)
                client.post(f"/v1/tasks/{tid}/evidence", headers=stack, json={
                    "kind": "closeout", "fence": "backfill:%s" % sheet["sid"],
                    "body": {"sheet": sheet["sid"], "verdict": sheet["verdict"],
                             "source": "backfill:%s@%s" % (sheet["path"], commit)}})
                client.post(f"/v1/tasks/{tid}/transition",
                            json={"to": "done",
                                  "idempotency_key": "backfill:%s:%s:done"
                                  % (sheet["sid"], t["num"]),
                                  "note": "%s %s. %s" % (sheet["sid"],
                                                        sheet["verdict"], HIST_NOTE)},
                            headers=stack)
                closed += 1
    return {"made_or_would_make": made, "skipped_existing": skipped,
            "closed": closed}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true",
                    help="write to the live ledger (default: temp-DB dry run)")
    ap.add_argument("--base-url", default="http://127.0.0.1:8221")
    args = ap.parse_args()
    commit = head_commit()
    sheets = inventory()
    if args.live:
        import httpx
        token = (ROOT / ".data/api-token").read_text().strip()
        c = httpx.Client(base_url=args.base_url, timeout=30,
                         headers={"X-Rouvr-Token": token})
        c.post("/v1/tenants", json={"tenant": TENANT})
        res = run(c, commit, sheets, TENANT, None, live=True)
        print("LIVE:", json.dumps(res))
    else:
        import tempfile
        from fastapi.testclient import TestClient
        from ledger.app import make_app
        import os
        os.environ["ROUVR_LEDGER_TOKEN"] = "stack-secret"
        db = "sqlite:///%s/dry.db" % tempfile.mkdtemp()
        with TestClient(make_app(db)) as c:
            stack = {"X-Rouvr-Token": "stack-secret"}
            c.post("/v1/tenants", json={"tenant": TENANT}, headers=stack)
            first = run(c, commit, sheets, TENANT, stack, live=True)
            # read-back on the temp DB: states + receipts (per-task GET;
            # the list endpoint carries no receipts)
            items = c.get("/v1/tasks?limit=1000", headers=stack).json()["items"]
            by_title = {x.get("title"): x for x in items}
            open_ok = done_ok = 0
            for s in sheets:
                for t in s["tasks"]:
                    title = "[%s %s] %s" % (s["sid"], t["num"], t["name"])
                    assert title in by_title, title
                    hits = [by_title[title]]
                    st = c.get("/v1/tasks/%s" % hits[0]["id"],
                               headers=stack).json()
                    hits[0] = st
                    if s["status"] == "done":
                        assert st["state"] == "done", (title, st["state"])
                        bodies = [(r.get("body") if isinstance(r.get("body"), str)
                                   else json.dumps(r.get("body")))
                                  for r in hits[0].get("receipts", [])]
                        assert any("Imported historical record" in (b or "")
                                   for b in bodies), title
                        done_ok += 1
                    else:
                        assert st["state"] == "open", (title, st["state"])
                        open_ok += 1
            second = run(c, commit, sheets, TENANT, stack, live=True)
            assert second["made_or_would_make"] == 0, second
        total = sum(len(s["tasks"]) for s in sheets)
        print("DRY RUN (throwaway DB, live untouched):")
        for s in sheets:
            print("  %s %-9s tasks=%d project=%s" % (
                s["sid"], s["status"], len(s["tasks"]), sheet_project(s)))
        print("sheets=%d tasks=%d (open=%d done=%d) S018->knowledge" % (
            len(sheets), total, open_ok, done_ok))
        print("read-back: all states + honesty receipts verified on temp DB")
        print("idempotency: second pass created %d (must be 0)" %
              second["made_or_would_make"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
