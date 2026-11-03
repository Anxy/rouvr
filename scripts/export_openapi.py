#!/usr/bin/env python3
"""Export the ledger FastAPI OpenAPI spec for the typed TS client (FL-A1).

Writes the spec served by ``ledger.app`` to
``web/src/lib/api/openapi.json``; rebuild the TypeScript types from it with::

    pnpm --filter web gen:api

The app is built against a throwaway SQLite file so exporting never touches
a real ledger database. ``--check`` exits nonzero when the committed
snapshot differs from the live spec (drift gate, no write).
"""

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "web" / "src" / "lib" / "api" / "openapi.json"
OUT_LEDGER = ROOT / "ledger" / "openapi.json"


def live_spec() -> dict:
    sys.path.insert(0, str(ROOT))
    fd, tmp = tempfile.mkstemp(prefix="rouvr-openapi-", suffix=".db")
    os.close(fd)
    try:
        from ledger.app import make_app

        return make_app(f"sqlite:///{tmp}").openapi()
    finally:
        for suffix in ("", "-wal", "-shm"):
            try:
                os.unlink(tmp + suffix)
            except OSError:
                pass


def render(spec: dict) -> str:
    return json.dumps(spec, indent=2, sort_keys=True) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="fail if the committed snapshot differs (no write)")
    args = ap.parse_args()

    text = render(live_spec())
    if args.check:
        for target in (OUT, OUT_LEDGER):
            if not target.is_file():
                print(f"missing {target.relative_to(ROOT)}:"
                      " run scripts/export_openapi.py")
                return 1
            if target.read_text() != text:
                print(f"drift in {target.relative_to(ROOT)}:"
                      " re-run scripts/export_openapi.py"
                      " and pnpm --filter web gen:api")
                return 1
        print(f"both snapshots match the live spec"
              f" ({len(json.loads(text).get('paths', {}))} paths)")
        return 0

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text)
    print(f"wrote {OUT.relative_to(ROOT)}"
          f" ({len(json.loads(text).get('paths', {}))} paths)")
    OUT_LEDGER.write_text(text)
    print(f"wrote {OUT_LEDGER.relative_to(ROOT)}"
          f" ({len(json.loads(text).get('paths', {}))} paths)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
