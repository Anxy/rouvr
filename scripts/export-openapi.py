"""Export the Rouvr ledger FastAPI OpenAPI spec for the typed TS client.

Reads the live spec from ledger.app (FastAPI serves it at /openapi.json)
and writes a snapshot to web/src/lib/api/openapi.json. The web client
types are then generated with `pnpm --filter web gen:api` (or `pnpm gen:api`
from the repo root to do both steps).

Usage: python3 scripts/export-openapi.py [--check]
  --check: exit 1 if the committed snapshot differs from the live spec.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "web" / "src" / "lib" / "api" / "openapi.json"


def live_spec() -> dict:
    tmp = tempfile.NamedTemporaryFile(prefix="rouvr-openapi-", suffix=".db", delete=False)
    tmp.close()
    os.environ["ROUVR_LEDGER_DATABASE_URL"] = f"sqlite:///{tmp.name}"
    os.environ["ROUVR_INSECURE_DEV"] = "1"
    os.environ.pop("ROUVR_LEDGER_TOKEN", None)
    sys.path.insert(0, str(ROOT))
    from ledger.app import make_app

    try:
        return make_app(f"sqlite:///{tmp.name}").openapi()
    finally:
        for suffix in ("", "-wal", "-shm", "-journal"):
            try:
                os.unlink(tmp.name + suffix)
            except OSError:
                pass


def main() -> int:
    check = "--check" in sys.argv[1:]
    spec = live_spec()
    text = json.dumps(spec, indent=2, sort_keys=True) + "\n"
    if check:
        if not OUT.exists() or OUT.read_text() != text:
            print(f"{OUT.relative_to(ROOT)} is stale: run `pnpm gen:api`", file=sys.stderr)
            return 1
        print(f"{OUT.relative_to(ROOT)} is up to date")
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text)
    print(f"wrote {OUT.relative_to(ROOT)} ({len(spec.get('paths', {}))} paths)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
