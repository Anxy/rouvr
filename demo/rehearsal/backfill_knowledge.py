"""Backfill repo knowledge (sheets, docs, runbooks, spikes, notes) into
Knowledge Lite. Slices/plans + MEMORY.md are already covered by the seed
corpus and are excluded here (dedupe by normalized title).

Per entry: kind by deterministic section rule (binding/key decisions,
success criteria, validation plan -> decision; standing/non-negotiable/
delivery rules sections -> rule; else note), tenant supervisor, project
liveproof, visibility company, verified 0, body carries source
"backfill:<path>@<commit>".

Default is dry run on a throwaway sqlite DB with read-back assertions.
Live run needs --live (writes to --base-url with .data/api-token).
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TENANT, PROJECT = "supervisor", "liveproof"
VIS = "company"

# (path, sections-or-None-for-whole-file, extra excludes)
SOURCES = [
    ("docs/tasks/rouvr/S001-bootstrap-hygiene-base.md", None, ()),
    ("docs/tasks/rouvr/S002-spike-staging.md", None, ()),
    ("docs/tasks/rouvr/S003-workflow-ownership.md", None, ()),
    ("docs/tasks/rouvr/S004-build-leg.md", None, ()),
    ("docs/tasks/rouvr/S005-gate-tests-runner.md", None, ()),
    ("docs/tasks/rouvr/S006-verifier-close.md", None, ()),
    ("docs/tasks/rouvr/S007-rehearsal-a2.md", None, ()),
    ("docs/tasks/rouvr/S008-stage-promotion.md", None, ()),
    ("docs/tasks/rouvr/S009-aix-backport.md", None, ()),
    ("docs/tasks/rouvr/S010-p2-receipts.md", None, ()),
    ("docs/tasks/rouvr/S011-trust-boundaries.md", None, ()),
    ("docs/tasks/rouvr/S012-session-lifecycle-resilience.md", None, ()),
    ("docs/tasks/rouvr/S013-review-loop-integrity.md", None, ()),
    ("docs/tasks/rouvr/S014-storage-scale.md", None, ()),
    ("docs/tasks/rouvr/S015-proof-and-hygiene.md", None, ()),
    ("docs/tasks/rouvr/S016-user-profiles.md", None, ()),
    ("docs/tasks/rouvr/S017-loop-integrity-fixes.md", None, ()),
    ("docs/tasks/rouvr/S018-story-gaps-queued.md", None, ()),
    ("docs/tasks/rouvr/S019-launch-secrets-grants.md", None, ()),
    ("docs/tasks/rouvr/S020-review-fixes.md", None, ()),
    ("docs/tasks/rouvr/S021-decomposer-lite.md", None, ()),
    ("docs/tasks/rouvr/S022-tenant-project-partitions.md", None, ()),
    ("docs/tasks/rouvr/S023-hyeve-demo-integration.md", None, ()),
    ("docs/ROUVR-DEV-GUIDELINES.md", None, ()),
    ("docs/ROUVR-MASTER-SPRINT.md", None, ()),
    ("docs/a2-gates.md", None, ()),
    ("docs/architecture.md", None, ()),
    ("docs/concepts.md", None, ()),
    ("docs/configuration.md", None, ()),
    ("docs/demo.md", None, ()),
    ("docs/ecosystem.md", None, ()),
    ("docs/failure-triage.md", None, ()),
    ("docs/implementation-plan.md", None, ()),
    ("docs/models.md", None, ()),
    ("docs/rename-parked.md", None, ()),
    ("docs/roadmap.md", None, ()),
    ("docs/rouvr-repo-review-2026-10-30.md", None, ()),
    ("docs/rouvr-roadmap.md", None, ()),
    ("docs/test-quarantine.md", None, ()),
    ("docs/workhub-fixes.md", None, ()),
    ("demo/RUNBOOK.md", None, ()),
    ("docs/release-notes/r1-history-hygiene.md", None, ()),
    ("docs/spikes/muse-resume.md", None, ()),
    ("docs/system/ROUVR-SPRINT-INDEX-SUMMARY.md", None, ()),
    ("docs/plans/README.md", None, ()),
    ("README.md", None, ()),
    ("SUPERVISOR.md", None, ()),
    ("plan.md", None, ()),
    ("CHANGELOG.md", None, ()),
    ("demo/audit/2026-11-01/c-runbook-gaps.md", None, ()),
]

SKIP_SECTIONS = ("target files", "roadmap mapping", "files changed", "sources",
                  "sprint id", "sprint title", "acceptance", "task completion",)
TASKLIST_RE = re.compile(r"^\d+\.\s+tasks?$", re.I)


def _skip_section(title: str) -> bool:
    t = title.lower()
    return t.startswith(SKIP_SECTIONS) or bool(TASKLIST_RE.match(t))
LABEL_RE = re.compile(r"^(Implementation Summary|Tests Run|Non-Happy-Path Tests|"
                      r"Known Gaps|Deviations|DRIFT_RISK|Claim-check|Ranked gaps)"
                      r"\s*:\s*(.+)")
RULE_SECTION_RE = re.compile(r"rules?\b", re.I)
DECISION_SECTION_RE = re.compile(r"decision|binding", re.I)


def kind_for(section: str) -> str:
    s = section.lower()
    if RULE_SECTION_RE.search(s):
        return "rule"
    if DECISION_SECTION_RE.search(s):
        return "decision"
    return "note"


def norm(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", t.lower()).strip()


def _slugify(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60].rstrip("-") or "entry"


def extract(path: Path, commit: str, seen: set[str]) -> list[dict]:
    lines = path.read_text().splitlines()
    # group into ## sections
    sections: list[tuple[str, list[str]]] = []
    cur_title, cur_lines = "preamble", []
    for ln in lines:
        if ln.startswith("## "):
            sections.append((cur_title, cur_lines))
            cur_title, cur_lines = ln[3:].strip(), []
        else:
            cur_lines.append(ln)
    sections.append((cur_title, cur_lines))
    out: list[dict] = []
    tag = "[sheet %s]" % path.stem.split("-")[0] if "tasks/rouvr" in str(path) \
        else "[doc %s]" % path.stem
    for title, body in sections:
        if _skip_section(title):
            continue
        kind = kind_for(title)
        bullets = 0
        lead: list[str] = []   # leading prose block before first blank/bullet
        lead_closed = False
        for ln in body:
            s = ln.strip()
            if s.startswith("- "):
                bullets += 1
                lead_closed = True
                text = s[2:].strip()
                if len(text) < 12:
                    continue
                out.append((kind, "%s %s" % (tag, re.sub(r"\*\*", "", text[:90]).strip()), text))
                continue
            m = LABEL_RE.match(s)
            if m and len(m.group(2)) >= 20:
                lead_closed = True
                out.append((kind, "%s %s" % (tag, m.group(1)), m.group(2).strip()))
                continue
            if not s or s.startswith(("```", "|", "#")):
                if lead:  # blank ends the lead; leading blanks are skipped
                    lead_closed = True
                continue
            if not lead_closed and len(" ".join(lead + [s])) < 2000:
                lead.append(s)
        if bullets == 0 and lead:
            t = " ".join(lead).strip()
            if len(t) >= 40:
                out.append((kind, "%s %s" % (tag, re.sub(r"\*\*", "", t[:90]).strip()), t))
    entries = []
    scope_tag = ("sheet-%s" % path.stem.split("-")[0].lower() if "tasks/rouvr" in str(path)
                 else "doc-%s" % path.stem.lower())
    for kind, title, text in out:
        base = _slugify("backfill-%s" % title)[:55].rstrip("-") or "entry"
        slug, i = base, 2
        while slug in seen:
            slug = "%s-%d" % (base, i)
            i += 1
        seen.add(slug)
        summary = re.sub(r"\s+", " ", text).strip()[:160]
        entries.append({"kind": kind, "title": title[:160],
                        "body": "%s\n\n(source: backfill:%s@%s)" % (text, path, commit),
                        "slug": slug, "tags": [kind, scope_tag], "summary": summary})
    return entries


def inventory(commit: str) -> dict[str, list[dict]]:
    inv: dict[str, list[dict]] = {}
    seen: set[str] = set()
    for rel, _sections, _ex in SOURCES:
        p = ROOT / rel
        if not p.exists():
            inv[rel] = []
            continue
        inv[rel] = extract(p, commit, seen)
    return inv


def run(client, inv: dict[str, list[dict]], stack: dict | None,
        existing_slugs: set[str], corpus_titles: set[str], live: bool) -> dict:
    """Dedupe keys: deterministic slug (re-runs create nothing) plus the
    seed-corpus titles (backfill never duplicates the 132-entry corpus)."""
    made = skipped = skipped_corpus = 0
    for rel, items in inv.items():
        for e in items:
            if e["slug"] in existing_slugs:
                skipped += 1
                continue
            if norm(e["title"]) in corpus_titles:
                skipped_corpus += 1
                continue
            made += 1
            if not live:
                continue
            r = client.post("/v1/knowledge", json={
                "tenant": TENANT, "project": PROJECT, "kind": e["kind"],
                "title": e["title"], "body": e["body"],
                "slug": e["slug"], "tags": e["tags"], "summary": e["summary"],
                "visibility": VIS}, headers=stack)
            r.raise_for_status()
            existing_slugs.add(e["slug"])
    return {"would_make": made, "skipped": skipped,
            "skipped_corpus": skipped_corpus}


def live_backfill_slugs() -> set[str]:
    """Read-only probe of the live sqlite file: slugs already backfilled.
    Never writes; the live run skips these (idempotent re-runs)."""
    import sqlite3
    dbp = ROOT / ".data" / "ledger.db"
    con = sqlite3.connect("file:%s?mode=ro" % dbp, uri=True)
    try:
        return {r[0] for r in con.execute(
            "SELECT slug FROM knowledge_entries WHERE slug LIKE 'backfill-%'")}
    finally:
        con.close()


def live_corpus_titles() -> set[str]:
    """Read-only probe: normalized titles of the 132-entry seed corpus
    (canary marker), so the backfill never duplicates them."""
    import sqlite3
    dbp = ROOT / ".data" / "ledger.db"
    con = sqlite3.connect("file:%s?mode=ro" % dbp, uri=True)
    try:
        return {norm(r[0]) for r in con.execute(
            "SELECT title FROM knowledge_entries WHERE body LIKE"
            " '%rouvr-lite-corpus-2026-11%' AND id NOT IN"
            " (SELECT supersedes_id FROM knowledge_entries"
            " WHERE supersedes_id IS NOT NULL)")}
    finally:
        con.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--base-url", default="http://127.0.0.1:8221")
    args = ap.parse_args()
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                            capture_output=True, text=True).stdout.strip()
    inv = inventory(commit)
    total = sum(len(v) for v in inv.values())
    if args.live:
        import httpx
        token = (ROOT / ".data/api-token").read_text().strip()
        c = httpx.Client(base_url=args.base_url, timeout=30,
                         headers={"X-Rouvr-Token": token})
        have = live_backfill_slugs()  # read-only probe, skip-existing pass
        corpus = live_corpus_titles()  # read-only probe, never duplicate seed
        res = run(c, inv, None, have, corpus, live=True)
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
            c.post("/v1/projects", json={"tenant": TENANT, "project": PROJECT},
                   headers=stack)
            res = run(c, inv, stack, set(), set(), live=True)
            # spot read-back: samples by slug + unverified + company
            samples = [e for v in inv.values() for e in v[:1]][:3]
            for e in samples:
                got = c.get("/v1/knowledge/%s" % e["slug"],
                            headers=stack).json()
                assert got["title"] == e["title"], e["slug"]
                assert got["verified"] == 0
                assert got["visibility"] == "company"
                assert got["tags"] == e["tags"] and got["summary"] == e["summary"]
            # second pass over the now-filled DB must create nothing
            # (search pages at 100, so collect slugs from the temp DB file)
            import re as _re
            import sqlite3 as _sq
            m = _re.search(r"sqlite:///(.+)", db)
            assert m, db
            con = _sq.connect(m.group(1))
            try:
                have = {r[0] for r in con.execute(
                    "SELECT slug FROM knowledge_entries WHERE slug LIKE 'backfill-%'")}
            finally:
                con.close()
            again = run(c, inv, stack, have, set(), live=True)
            assert again == {"would_make": 0, "skipped": total,
                             "skipped_corpus": 0}, again
            print("idempotency: second pass creates 0 (%d skipped)" % total)
            # curator smoke: suggestions only, bounded runtime on this corpus
            import time as _t
            _t0 = _t.time()
            cur = c.post("/v1/knowledge/curate",
                         json={"tenant": TENANT, "project": PROJECT},
                         headers=stack).json()
            by_type: dict[str, int] = {}
            for s in cur.get("suggestions", []):
                by_type[s.get("type", "?")] = by_type.get(s.get("type", "?"), 0) + 1
            print("curator: %d suggestion(s) in %.1fs %s (suggestions only, nothing verified/deleted)"
                  % (cur.get("count", len(cur.get("suggestions", []))), _t.time() - _t0,
                     by_type))
        kinds: dict[str, int] = {}
        for v in inv.values():
            for e in v:
                kinds[e["kind"]] = kinds.get(e["kind"], 0) + 1
        print("DRY RUN (throwaway DB, live untouched): files=%d entries=%d kinds=%s"
              % (len(inv), total, kinds))
        for rel, items in inv.items():
            kk: dict[str, int] = {}
            for e in items:
                kk[e["kind"]] = kk.get(e["kind"], 0) + 1
            print("  %-50s %3d %s" % (rel, len(items), kk))
        print("read-back: samples by slug OK, verified=0, visibility=company")
        print("dedupe: deterministic-slug skip set (live run pre-loads backfill-% slugs)")
        outdir = ROOT / "demo" / "rehearsal" / "2026-11-02" / "knowledge-backfill"
        outdir.mkdir(parents=True, exist_ok=True)
        rp = outdir / "REVIEW.md"
        with rp.open("w") as f:
            f.write("# Knowledge backfill REVIEW (dry run, %s)\n\n" % commit)
            f.write("Scope: tenant=supervisor project=liveproof visibility=company, "
                    "all unverified (D13). Source marker `backfill:<path>@<commit>` in body.\n\n")
            for rel, items in inv.items():
                f.write("## %s (%d)\n\n" % (rel, len(items)))
                f.write("| slug | kind | title | summary |\n|---|---|---|---|\n")
                for e in items:
                    f.write("| %s | %s | %s | %s |\n" % (
                        e["slug"], e["kind"],
                        e["title"].replace("|", "/")[:100],
                        e["summary"].replace("|", "/")[:100]))
                f.write("\n")
        print("REVIEW list: %s" % rp)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
