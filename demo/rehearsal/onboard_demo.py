"""Hackathon rehearsal: onboarding + Knowledge Lite, against the live stack.

Legs (each prints PASS/FAIL/BLOCKED with the reason; nothing is faked):
  1. demo page serves
  2. register a fresh user on hyeve-auth via the ledger (person-first) —
     BLOCKED with the upstream code until the person service is configured
     (HYEVE_PERSON_BASE_URL) or ROUVR_DEMO_PERSON_ID is supplied
  3. if registered: complete onboarding, profiles land in Knowledge,
     knowledge search with the Hyeve JWT is tenant-scoped
  4. knowledge on the demo scope (supervisor/liveproof) via the stack:
     store a decision, retrieve + cite it in a "later session"

Usage: .venv/bin/python demo/rehearsal/onboard_demo.py [username]
Reads the stack token from .data/api-token (never prints it).
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
LEDGER = os.environ.get("ROUVR_LEDGER_URL", "http://127.0.0.1:8221")
STACK = {"X-Rouvr-Token": (ROOT / ".data" / "api-token").read_text().strip()}


def leg(name: str, fn) -> str:
    try:
        outcome = fn()
    except AssertionError as e:
        print(f"[FAIL] {name}: {e}")
        return "FAIL"
    print(f"[{outcome}] {name}")
    return outcome


def main() -> int:
    user = sys.argv[1] if len(sys.argv) > 1 else f"demo-user-{int(time.time())}"
    state: dict = {}
    results = []

    def page() -> str:
        r = httpx.get(f"{LEDGER}/demo/onboard", timeout=10)
        assert r.status_code == 200 and "Register" in r.text, r.status_code
        return "PASS"

    def register() -> str:
        body = {"username": user, "email": f"{user}@example.com",
                "password": "Demo-Pass-2026!", "given_name": "Demo",
                "family_name": "User"}
        pid = os.environ.get("ROUVR_DEMO_PERSON_ID", "")
        if pid:
            body["person_id"] = pid
        r = httpx.post(f"{LEDGER}/v1/onboard/register", json=body, timeout=30)
        if r.status_code in (502, 503):
            print(f"       upstream: {r.text[:200]}")
            return "BLOCKED"
        assert r.status_code == 200, f"{r.status_code} {r.text[:200]}"
        state["jwt"] = r.json()["access_token"]
        return "PASS"

    def complete() -> str:
        if "jwt" not in state:
            return "BLOCKED"
        form = {"display_name": "Demo User", "role_title": "Founder",
                "organisation": "Demo Org", "use_case": "Hackathon demo",
                "project_name": "demo-project", "project_purpose": "Show Rouvr onboarding",
                "project_type": "mixed", "north_star": "Onboarded in 60s",
                "standing_rules": "Cite every source."}
        r = httpx.post(f"{LEDGER}/v1/onboard/complete", json=form,
                       headers={"Authorization": f"Bearer {state['jwt']}"}, timeout=30)
        assert r.status_code == 200, f"{r.status_code} {r.text[:200]}"
        out = r.json()
        print(f"       tenant={out['tenant']} project={out['project']}"
              f" profiles={out.get('user_profile')},{out.get('project_profile')}")
        hits = httpx.get(f"{LEDGER}/v1/knowledge/search", params={"q": "profile"},
                         headers={"Authorization": f"Bearer {state['jwt']}"},
                         timeout=10).json()["items"]
        assert hits and all(h["tenant"] == out["tenant"] for h in hits), hits
        return "PASS"

    def knowledge_demo_scope() -> str:
        canary = f"canary{int(time.time())}"  # unique per run: store is append-only
        body = {"tenant": "supervisor", "project": "liveproof", "kind": "decision",
                "title": "Hackathon knowledge check",
                "body": f"Knowledge Lite stores this decision for later sessions ({canary})."}
        r = httpx.post(f"{LEDGER}/v1/knowledge", json=body, headers=STACK, timeout=10)
        assert r.status_code == 200, f"{r.status_code} {r.text[:200]}"
        entry = r.json()
        hits = httpx.get(f"{LEDGER}/v1/knowledge/search", params={"q": canary},
                         headers=STACK, timeout=10).json()["items"]
        assert [h["id"] for h in hits] == [entry["id"]], hits
        print(f"       stored + cited as {entry['citation']} (verified={entry['verified']})")
        return "PASS"

    results.append(leg("demo page", page))
    results.append(leg("register on hyeve-auth", register))
    results.append(leg("complete onboarding + profiles in Knowledge", complete))
    results.append(leg("knowledge store/cite on demo scope", knowledge_demo_scope))
    print("LEGS:", " ".join(results))
    # Exit codes are explicit: 0 all PASS, 1 any FAIL, 2 BLOCKED on the live
    # hyeve-auth/person-service dependency (out of scope without live creds).
    # BLOCKED is reported loudly here, never a silent pass.
    if "FAIL" in results:
        return 1
    if "BLOCKED" in results:
        print("BLOCKED: live hyeve-auth/person-service dependency unavailable")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
