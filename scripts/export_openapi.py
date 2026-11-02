#!/usr/bin/env python3
"""Export the ledger FastAPI OpenAPI spec for the typed TS client.

Builds the app against a throwaway SQLite DB (no side effects on the
working tree) and writes deterministic JSON to web/src/lib/api/openapi.json.
Run with the project env, e.g. `.venv/bin/python scripts/export_openapi.py`,
or via `pnpm --filter web gen:api` (which also regenerates schema.d.ts).
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "web" / "src" / "lib" / "api" / "openapi.json"


def main() -> int:
    sys.path.insert(0, str(ROOT))
    with tempfile.TemporaryDirectory(prefix="rouvr-openapi-") as d:
        os.environ["ROUVR_LEDGER_DATABASE_URL"] = f"sqlite:///{d}/export.db"
        os.environ.pop("ROUVR_LEDGER_TOKEN", None)
        os.environ.pop("ROUVR_INSECURE_DEV", None)
        from ledger.app import make_app

        spec = make_app().openapi()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(spec, indent=2, sort_keys=True) + "\n")
    print(f"wrote {OUT.relative_to(ROOT)} ({len(spec.get('paths', {}))} paths)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
