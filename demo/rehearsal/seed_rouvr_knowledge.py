"""Push the Rouvr Lite roadmap knowledge (MEMORY.md + docs/plans slices)
into Knowledge Lite on the demo scope, then prove it is retrievable.

Kind mapping is deterministic per source section (no body keyword guessing):
binding/key decisions, success criteria and validation plans seed as
decision; every other section seeds as note. Standing rules become rule
entries only at onboarding runtime (owner-typed), never by guessing.

Usage:
  .venv/bin/python demo/rehearsal/seed_rouvr_knowledge.py seed
  .venv/bin/python demo/rehearsal/seed_rouvr_knowledge.py recall [query ...]
  .venv/bin/python demo/rehearsal/seed_rouvr_knowledge.py wipe
Idempotent: entries carry a corpus canary; seeding twice adds nothing, and a
partial first seed is backfilled by title dedupe on reseed.
Reads the stack token from .data/api-token (never prints it).
"""
from __future__ import annotations

import os
import re
import sqlite3
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
LEDGER = os.environ.get("ROUVR_LEDGER_URL", "http://127.0.0.1:8221")
H = {"X-Rouvr-Token": (ROOT / ".data" / "api-token").read_text().strip()}
SCOPE = {"tenant": "supervisor", "project": "liveproof"}
CANARY = "rouvr-lite-corpus-2026-11"

# Deterministic section -> kind. No body text is inspected.
SECTION_KIND = (
    (("binding decisions", "key decisions"), "decision"),
    (("success criteria", "validation plan"), "decision"),
)
DEFAULT_KIND = "note"


def kind_for_section(section: str) -> str:
    s = section.lower()
    for prefixes, kind in SECTION_KIND:
        if any(s.startswith(p) for p in prefixes):
            return kind
    return DEFAULT_KIND


def bullets(path: Path, section_prefixes: tuple[str, ...]) -> list[tuple[str, str]]:
    """(section, text) bullets under headings starting with any prefix."""
    lines = path.read_text().splitlines()
    out, cur = [], ""
    for line in lines:
        if line.startswith("## "):
            cur = line[3:].strip().lower()
        elif line.startswith("- ") and any(cur.startswith(p) for p in section_prefixes):
            out.append((cur, line[2:].strip()))
    return out


def _slug(title: str, seen: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:55].rstrip("-") or "entry"
    slug, i = base, 2
    while slug in seen:
        slug = f"{base}-{i}"
        i += 1
    seen.add(slug)
    return slug


def corpus() -> list[dict]:
    items: list[dict] = []
    seen: set[str] = set()
    plans = ROOT / "docs/plans"
    for name in ("2026-11-01-slice-1-identity-tenancy-projects.md",
                 "2026-11-01-slice-2-knowledge-lite.md",
                 "2026-11-01-slice-3-task-creation-methodology.md"):
        path = plans / name
        for section, text in bullets(path, ("binding decisions", "key decisions",
                                            "part b", "part a", "risks", "assumption",
                                            "success criteria", "views", "where we",
                                            "validation plan", "goal", "steps",
                                            "sources")):
            title = re.sub(r"\*\*", "", text[:90]).strip()
            items.append({"kind": kind_for_section(section),
                          "title": f"[{name[11:18]}] {title}",
                          "body": f"{text}\n\n(source: docs/plans/{name}, section: {section}; {CANARY})",
                          "slug": _slug(f"{name[11:18]}-{title}", seen),
                          "tags": [kind_for_section(section),
                                   re.sub(r"[^a-z0-9]+", "-", section.lower()).strip("-")[:40] or "general"],
                          "summary": re.sub(r"\s+", " ", text).strip()[:160]})
    mem = ROOT / "MEMORY.md"
    for section, text in bullets(mem, ("key findings", "quirks")):
        items.append({"kind": kind_for_section(section),
                      "title": f"[memory] {text[:90]}",
                      "body": f"{text}\n\n(source: MEMORY.md, section: {section}; {CANARY})",
                      "slug": _slug(f"memory-{text[:60]}", seen),
                      "tags": [kind_for_section(section),
                               re.sub(r"[^a-z0-9]+", "-", section.lower()).strip("-")[:40] or "general"],
                      "summary": re.sub(r"\s+", " ", text).strip()[:160]})
    return items


def search(q: str) -> list[dict]:
    r = httpx.get(f"{LEDGER}/v1/knowledge/search",
                  params={"q": q, "full": "true", "n": 100}, headers=H, timeout=20)
    r.raise_for_status()
    return r.json()["items"]


def _title_present(title: str) -> bool:
    """Per-title existence probe (search pages at 50; corpus exceeds a page)."""
    words = [w for w in re.findall(r"\w+", title) if len(w) > 4][:3]
    for w in words:
        try:
            for h in search(w):
                if h["title"] == title and CANARY in h.get("body", ""):
                    return True
        except Exception:
            continue
    return False


def seed() -> int:
    items = corpus()
    if len(search(CANARY)) >= 50 and all(_title_present(e["title"]) for e in items):
        print(f"corpus already seeded ({len(items)} entries); not duplicating")
        return 0
    n = 0
    for e in items:
        if _title_present(e["title"]):
            continue  # backfill after a partial first seed
        r = httpx.post(f"{LEDGER}/v1/knowledge", json={**SCOPE, **e}, headers=H, timeout=15)
        r.raise_for_status()
        n += 1
    print(f"seeded {n} rouvr-lite entries on supervisor/liveproof "
          f"({len(items)} in corpus, deterministic section kinds)")
    return 0


def wipe() -> int:
    """Delete canary corpus rows (demo scope only). Demo support, not an API:
    production entries stay append-only."""
    db = os.environ.get("ROUVR_LEDGER_DATABASE_URL", "")
    if db.startswith("sqlite:///"):
        dbpath = db[len("sqlite:///"):]
    else:
        dbpath = str(ROOT / ".data" / "ledger.db")
    con = sqlite3.connect(dbpath)
    ids = [r[0] for r in con.execute(
        "SELECT id FROM knowledge_entries WHERE body LIKE ?", (f"%{CANARY}%",))]
    if ids:
        con.execute(f"DELETE FROM knowledge_fts WHERE rowid IN ({','.join('?' * len(ids))})", ids)
        con.execute(f"DELETE FROM knowledge_entries WHERE id IN ({','.join('?' * len(ids))})", ids)
        con.commit()
    con.close()
    print(f"wiped {len(ids)} canary entries")
    return 0


def recall(queries: list[str]) -> int:
    for q in queries or ["decomposer skipped", "curator", "god token", "revision unit"]:
        hits = [h for h in search(q) if CANARY in h["body"]][:3]
        print(f"--- {q!r}: {len(hits)} corpus hit(s)")
        for h in hits:
            print(f"  {h['citation']} [{h['kind']} v{h['verified'] and 'verified' or 'unverified'}] {h['title'][:80]}")
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "seed":
        raise SystemExit(seed())
    if cmd == "recall":
        raise SystemExit(recall(sys.argv[2:]))
    if cmd == "wipe":
        raise SystemExit(wipe())
    print(__doc__)
    raise SystemExit(2)
