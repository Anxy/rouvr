#!/usr/bin/env python3
"""Regenerate the sprint-index companions from the full index (do not hand-edit outputs).

Usage (repo root): python tools/generate_sprint_index_companions.py
Reads docs/system/ROUVR-SPRINT-INDEX.json; writes CURRENT.json (index + counts)
and SUMMARY.md (status table). Run at every sprint closeout.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

SYSTEM = Path(__file__).resolve().parent.parent / "docs" / "system"
FULL = SYSTEM / "ROUVR-SPRINT-INDEX.json"
CURRENT = SYSTEM / "ROUVR-SPRINT-INDEX-CURRENT.json"
SUMMARY = SYSTEM / "ROUVR-SPRINT-INDEX-SUMMARY.md"


def main() -> None:
    full = json.loads(FULL.read_text())
    sprints = full.get("sprints", [])
    counts: dict[str, int] = {}
    for s in sprints:
        counts[s.get("status", "unknown")] = counts.get(s.get("status", "unknown"), 0) + 1
    CURRENT.write_text(json.dumps({"generated_at": datetime.now(timezone.utc).isoformat(),
                                   "counts": counts, "sprints": sprints}, indent=2) + "\n")
    lines = ["# Rouvr sprint index summary", "",
             f"Generated from `ROUVR-SPRINT-INDEX.json` — {len(sprints)} sprints.",
             "", "| ID | Title | Status | Confidence | Known gaps |",
             "|----|-------|--------|------------|------------|"]
    for s in sprints:
        gaps = "; ".join(s.get("known_gaps", [])) or "—"
        lines.append(f"| {s['id']} | {s['title']} | {s['status']} | "
                     f"{s.get('confidence', '?')} | {gaps} |")
    SUMMARY.write_text("\n".join(lines) + "\n")
    print(f"wrote {CURRENT.name} + {SUMMARY.name} ({len(sprints)} sprints)")


if __name__ == "__main__":
    main()
