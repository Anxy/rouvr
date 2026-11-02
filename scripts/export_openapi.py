#!/usr/bin/env python3
"""Export the Rouvr ledger FastAPI OpenAPI document for the typed TS client.

Writes web/src/lib/api/openapi.json from the app served by ledger/app.py
(the same document FastAPI serves at /openapi.json). The web client types
are then generated with:

    pnpm --filter web gen:api

Runs against an throwaway SQLite DB so exporting never touches the real
ledger file.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "web" / "src" / "lib" / "api" / "openapi.json"


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="rouvr-openapi-") as tmp:
        os.environ["ROUVR_LEDGER_DATABASE_URL"] = f"sqlite:///{tmp}/ledger.db"
        sys.path.insert(0, str(ROOT))
        from ledger.app import app

        spec = app.openapi()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(spec, indent=2, sort_keys=True) + "\n")
    print(f"wrote {OUT.relative_to(ROOT)} ({len(spec.get('paths', {}))} paths)")


if __name__ == "__main__":
    main()
