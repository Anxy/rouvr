"""Minimal Rouvr CLI over the ledger.  python -m cli.rouvr_cli [--ledger URL] tasks|show ID|demo"""
from __future__ import annotations

import argparse
import json
import os
import sys


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="rouvr")
    ap.add_argument("--ledger", default=os.environ.get("ROUVR_LEDGER_URL", "http://localhost:8221"))
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("tasks")
    s = sub.add_parser("show"); s.add_argument("id", type=int)
    sub.add_parser("demo")
    a = ap.parse_args(argv)
    if a.cmd == "demo":
        from demo.morning_report import run
        print(run(a.ledger)); return 0
    import httpx
    c = httpx.Client(base_url=a.ledger, timeout=10)
    r = c.get("/v1/tasks") if a.cmd == "tasks" else c.get(f"/v1/tasks/{a.id}")
    r.raise_for_status()
    json.dump(r.json(), sys.stdout, indent=2); print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
