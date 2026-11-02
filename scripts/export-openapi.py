#!/usr/bin/env python3
"""Export the ledger FastAPI OpenAPI spec for the web typed client (FL-A1).

Run from the repo root:
    .venv/bin/python scripts/export-openapi.py [--check]

Writes web/src/lib/api/openapi.json deterministically (sorted keys, fixed
formatting) so regeneration is a clean no-op when the API is unchanged.
--check exits 1 when the snapshot differs from the live spec (CI drift gate).
After re-exporting, regenerate types:  pnpm --filter web gen:api
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "web" / "src" / "lib" / "api" / "openapi.json"


def live_spec() -> dict:
    sys.path.insert(0, str(ROOT))
    from ledger.app import make_app  # noqa: PLC0415

    # In-memory DB: export needs routes only, never touches real state.
    return make_app("sqlite:///:memory:").openapi()


def main(argv: list[str]) -> int:
    spec = live_spec()
    text = json.dumps(spec, indent=2, sort_keys=True) + "\n"
    if "--check" in argv:
        current = OUT.read_text() if OUT.exists() else ""
        if current != text:
            print(f"drift: {OUT.relative_to(ROOT)} is stale; run "
                  "scripts/export-openapi.py then pnpm --filter web gen:api",
                  file=sys.stderr)
            return 1
        print(f"ok: {OUT.relative_to(ROOT)} matches the live spec")
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text)
    print(f"wrote {OUT.relative_to(ROOT)} ({len(spec['paths'])} paths)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
