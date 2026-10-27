#!/usr/bin/env python3
"""Read-only AIX-E2E-CYCLE-001 drift baseline collector. Does not mutate repos/DBs/deployments."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def git(cwd: Path, *args: str) -> dict:
    try:
        r = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=60)
        return {"ok": r.returncode == 0, "stdout": r.stdout.strip(), "stderr": r.stderr.strip()}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "stdout": "", "stderr": str(exc)}


def main() -> int:
    aix = Path(os.environ.get("AIX_CHECKOUT", r"D:\v2.1\AIX")).resolve()
    fe = Path(os.environ.get("AIX_FRONTEND", r"D:\v2.1\AIX-Frontend")).resolve()
    mirror = Path(os.environ.get("AIX_STALE_MIRROR", r"D:\v2.1\hyeve\projects\AIX")).resolve()
    hyeve = Path(os.environ.get("HYEVE_CHECKOUT", r"D:\v2.1\hyeve")).resolve()
    out_path = Path(
        os.environ.get(
            "AIX_DRIFT_OUT",
            str(aix / "docs/current/benchmarks/AIX-E2E-CYCLE-001/AIX-E2E-CYCLE-001A0-W06/drift-baseline.json"),
        )
    )

    main_py = aix / "hyeve-aix-service/backend/main.py"
    mounted = (
        sorted(set(re.findall(r"include_router\((\w+)", main_py.read_text(encoding="utf-8", errors="replace"))))
        if main_py.exists()
        else []
    )
    routers_dir = aix / "hyeve-aix-service/backend/routers"
    routers = (
        sorted(p.name for p in routers_dir.glob("*.py") if p.name != "__init__.py") if routers_dir.exists() else []
    )

    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "packet": "AIX-E2E-CYCLE-001A0-W06",
        "read_only": True,
        "mutations": "none",
        "production_status": "NO_GO",
        "aix": {
            "path": str(aix),
            "remote": git(aix, "remote", "-v"),
            "head": git(aix, "rev-parse", "HEAD"),
            "status": git(aix, "status", "--short", "--branch"),
            "ahead_behind": git(aix, "rev-list", "--left-right", "--count", "@{u}...HEAD"),
        },
        "aix_frontend": {
            "path": str(fe),
            "exists": fe.exists(),
            "remote": git(fe, "remote", "-v") if fe.exists() else None,
            "head": git(fe, "rev-parse", "HEAD") if fe.exists() else None,
            "status": git(fe, "status", "--short", "--branch") if fe.exists() else None,
        },
        "hyeve": {
            "path": str(hyeve),
            "exists": hyeve.exists(),
            "head": git(hyeve, "rev-parse", "HEAD") if hyeve.exists() and (hyeve / ".git").exists() else None,
            "status": git(hyeve, "status", "--short", "--branch") if hyeve.exists() and (hyeve / ".git").exists() else None,
        },
        "stale_mirror": {
            "path": str(mirror),
            "exists": mirror.exists(),
            "quarantine_marker": (mirror / "QUARANTINE.md").exists() if mirror.exists() else False,
        },
        "routers": {"files": routers, "mounted_include_router_vars": mounted},
        "compose": {
            "min_compose": str(aix / "deploy/hostinger/docker-compose.aix-min.yml"),
            "exists": (aix / "deploy/hostinger/docker-compose.aix-min.yml").exists(),
        },
        "runtime_checks": {
            "temporal_reachability": "UNKNOWN_NOT_PROBED",
            "postgres_schema_version": "UNKNOWN_NOT_PROBED",
            "memurai_streams": "UNKNOWN_NOT_PROBED",
            "gateway_ready": "UNKNOWN_NOT_PROBED",
        },
        "notes": [
            "Does not mutate repositories, deployments, or databases.",
            "Live Temporal/Memurai/Gateway reachability left UNKNOWN unless separately probed.",
        ],
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(str(out_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
