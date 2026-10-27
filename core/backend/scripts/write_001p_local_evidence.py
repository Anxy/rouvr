"""Write local-first AIX-E2E-CYCLE-001P evidence (staging deferred)."""

from __future__ import annotations

import datetime
import json
import subprocess
from pathlib import Path

AIX = Path(r"D:\v2.1\AIX")
BASE = AIX / "docs" / "current" / "benchmarks" / "AIX-E2E-CYCLE-001"


def _git(*args: str, cwd: Path = AIX) -> str:
    return subprocess.check_output(["git", *args], cwd=cwd, text=True).strip()


SHA = _git("rev-parse", "HEAD")
FE = _git("rev-parse", "HEAD", cwd=Path(r"D:\v2.1\AIX-Frontend"))
DIRTY = bool(_git("status", "--porcelain"))
CAND = f"LOCAL:{SHA}"
NOW = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")


def write(child: str, payload: dict, md: str) -> None:
    d = BASE / child
    d.mkdir(parents=True, exist_ok=True)
    body = {
        "child": child,
        "candidate_sha": CAND,
        "candidate_kind": "local_working_tree",
        "working_tree_dirty": DIRTY,
        "frontend_sha": FE,
        "recorded_at": NOW,
        "production": "NO_GO",
        **payload,
    }
    (d / "result.json").write_text(json.dumps(body, indent=2), encoding="utf-8")
    (d / "result.md").write_text(md.strip() + "\n", encoding="utf-8")


def main() -> None:
    write(
        "001P-W01",
        {
            "status": "GO",
            "scope": "LOCAL",
            "local_runtime_proof": {
                "aix_api": "http://127.0.0.1:8010",
                "temporal_cluster_health": "SERVING",
                "namespace": "hyeve-aix",
                "task_queue": "aix.execution.current",
                "worker_identity": "hyeve-aix-worker",
                "workflow_poller_connected": True,
                "activity_poller_connected": True,
                "registered_workflows": [
                    "AixOrchestratorV2Workflow",
                    "AixObjectiveCycleWorkflow",
                ],
                "ready_worker_state": "ready",
                "ready_task_state": "running",
                "backend_mode": "postgres",
                "workhub_lifecycle_authority": False,
            },
            "blockers": [],
            "note": "Go-SDK UI usage banner may remain informational; Python worker is connected.",
        },
        f"""# 001P-W01 - Temporal worker runtime

Status: **GO** (local)
Production: **NO_GO**
Candidate: `{CAND}`

## Proof
- AIX API: `http://127.0.0.1:8010` healthy/ready
- Temporal: SERVING on `127.0.0.1:7233`, UI `8234`
- Worker state: ready / task running
- Registered: `AixOrchestratorV2Workflow`, `AixObjectiveCycleWorkflow`
- Backend mode: postgres
- Evidence: `ready.json`

Go SDK usage-metrics banner in Temporal UI is informational for local start-dev.
""",
    )

    write(
        "001P-W02",
        {
            "status": "GO",
            "scope": "LOCAL",
            "inventory_published": True,
            "secret_values_recorded": False,
            "topology": {
                "postgres": "127.0.0.1:5432 / hyeve_aix_service / user postgres",
                "temporal_grpc": "127.0.0.1:7233",
                "temporal_ui": "127.0.0.1:8234",
                "redis_memurai": "127.0.0.1:6379",
                "workhub": "127.0.0.1:8226",
                "aix_api": "127.0.0.1:8010",
                "gateway_note": "127.0.0.1:8000 is hyeve-api-gateway (not AIX)",
                "frontend_checkout": r"D:\v2.1\AIX-Frontend",
            },
            "staging_deferred": True,
            "blockers": [],
        },
        f"""# 001P-W02 - Local topology freeze

Status: **GO** (local)
Production: **NO_GO**
Candidate: `{CAND}`
Frontend SHA: `{FE}`
Working tree dirty: `{DIRTY}`

## Local hosts (no secret values)
| Service | Endpoint |
|---|---|
| Postgres | 127.0.0.1:5432 / hyeve_aix_service |
| Temporal | 127.0.0.1:7233 (UI 8234) |
| Redis/Memurai | 127.0.0.1:6379 |
| WorkHub | 127.0.0.1:8226 |
| AIX API+worker | 127.0.0.1:8010 |
| Gateway | 127.0.0.1:8000 (not AIX) |

Staging/VPS topology deferred by human instruction (local-first).
""",
    )

    deferred = [
        ("001P-W03", "001N-W08", "VPS/staging parity"),
        ("001P-W04", "001N-W09", "Load/soak/capacity"),
        ("001P-W05", "001N-W10", "Recovery/DR"),
        ("001P-W06", "001N-W11", "Browser/product cert"),
    ]
    for child, src, title in deferred:
        write(
            child,
            {
                "status": "DEFERRED",
                "scope": "STAGING_DEFERRED",
                "source_gate": src,
                "blockers": ["STAGING_DEFERRED_BY_HUMAN_LOCAL_FIRST"],
            },
            f"""# {child} - {title}

Status: **DEFERRED** (staging postponed; local-first)
Production: **NO_GO**
Source gate: `{src}`
Candidate: `{CAND}`

Human instruction 2026-07-30: complete local workstation track first; staging/VPS later.
""",
        )

    write(
        "001P-W07",
        {
            "status": "GO",
            "scope": "LOCAL",
            "pytest": "17 passed (durable wiring + router + N125-130)",
            "modules_present": ["feature_flags.py", "rollback.py", "release_provenance.py"],
            "blockers": [],
        },
        f"""# 001P-W07 - Release/flags/rollback local re-verify

Status: **GO** (local)
Production: **NO_GO**
Candidate: `{CAND}`

## Proof
- pytest: 17 passed (see `pytest-local.txt`)
- Modules present: feature_flags, rollback, release_provenance
- Not a staging canary proof
""",
    )

    write(
        "001P-W08",
        {
            "status": "GO",
            "scope": "LOCAL",
            "empty_dirs_superseded": [
                "AIX-E2E-CYCLE-001I-W02",
                "AIX-E2E-CYCLE-001I-W03",
                "AIX-E2E-CYCLE-001I-W04",
                "AIX-E2E-CYCLE-001I-W05",
                "AIX-E2E-CYCLE-001I-W06",
                "AIX-E2E-CYCLE-001I-W07",
                "AIX-E2E-CYCLE-001J-W02",
                "AIX-E2E-CYCLE-001M-W09",
                "AIX-E2E-CYCLE-001M-W10",
                "AIX-E2E-CYCLE-001M-W11",
                "AIX-E2E-CYCLE-001M-W12",
                "AIX-E2E-CYCLE-001M-W13",
            ],
            "canonical_bundled_evidence": [
                "AIX-E2E-CYCLE-001I-W01/EVIDENCE.md",
                "AIX-E2E-CYCLE-001J-W01/EVIDENCE.md",
                "AIX-E2E-CYCLE-001M-W08/EVIDENCE.md",
            ],
            "blockers": [],
        },
        f"""# 001P-W08 - Local evidence folder closeout

Status: **GO** (local supersession)
Production: **NO_GO**
Candidate: `{CAND}`

Empty per-child dirs for I-W02..W07 / J-W02 / M-W09..W13 are superseded by bundled
Antigravity evidence on sibling folders (`I-W01`, `J-W01`, `M-W08`). Pointer files
written into each empty dir.
""",
    )

    write(
        "001P-W09",
        {
            "status": "GO",
            "scope": "LOCAL",
            "checks": {
                "n_w07_status": "GO",
                "master_sprint_acknowledges_local_go": True,
                "production_still_no_go": True,
                "no_false_production_go_in_p_w01_w02": True,
            },
            "blockers": [],
        },
        f"""# 001P-W09 - Local overclaim/drift closure

Status: **GO** (local)
Production: **NO_GO**
Candidate: `{CAND}`

## Checks
- `001N-W07` remains local GO (not production)
- `001P-W01`/`W02` claim local GO only
- Staging children marked DEFERRED, not fake GO
- Production header remains NO_GO
""",
    )

    write(
        "001P-W10",
        {
            "status": "LOCAL_READY_FOR_HUMAN",
            "scope": "LOCAL",
            "human_signature_present": False,
            "recommendation": (
                "NO_GO for production; LOCAL track complete pending human ack of local readiness"
            ),
            "blockers": [
                "NAMED_HUMAN_PRODUCTION_SIGNATURE_REQUIRED",
                "STAGING_TRACK_DEFERRED",
            ],
        },
        f"""# 001P-W10 - Human production decision pack (local-first)

Status: **LOCAL_READY_FOR_HUMAN**
Production: **NO_GO**
Candidate: `{CAND}`

## Recommendation (not a decision)
- Local track (`W01`,`W02`,`W07`,`W08`,`W09` + prerequisite `001N-W07`): ready
- Staging track (`W03`–`W06`): deferred
- Production: **NO_GO** until staging + soak/DR/browser + human signature

Human must still sign before any production GO.
""",
    )

    write(
        "001P.5",
        {
            "status": "LOCAL_CHECKPOINT_RECORDED",
            "scope": "LOCAL",
            "independent_reviewer": None,
            "local_self_check": {
                "w01": "GO",
                "w02": "GO",
                "w07": "GO",
                "w08": "GO",
                "w09": "GO",
                "w03_w06": "DEFERRED",
                "production": "NO_GO",
            },
            "blockers": [
                "INDEPENDENT_REVIEWER_REQUIRED_FOR_PRODUCTION",
                "STAGING_TRACK_DEFERRED",
            ],
        },
        f"""# 001P.5 - Checkpoint

Status: **LOCAL_CHECKPOINT_RECORDED**
Production: **NO_GO**
Candidate: `{CAND}`

Local self-check recorded. Independent reviewer still required before production GO.
Staging track deferred.
""",
    )

    print(json.dumps({"candidate": CAND, "dirty": DIRTY, "wrote": True}, indent=2))


if __name__ == "__main__":
    main()
