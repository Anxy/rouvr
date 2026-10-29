#!/usr/bin/env python3
"""Validate ROUVR-SPRINT-LIFECYCLE.json. Delegates to tools/sprint_ledger.py
(the first-class lifecycle module) so the rules live in exactly one place.

Usage (repo root): python tools/validate_sprint_lifecycle.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # repo root

from tools.sprint_ledger import REPO, load, validate_doc


def main() -> int:
    doc = load()
    errors = validate_doc(doc, REPO)
    if errors:
        print("lifecycle INVALID:")
        print("\n".join(f"  - {e}" for e in errors))
        return 1
    print(f"lifecycle valid ({len(doc['sprints'])} sprints)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
