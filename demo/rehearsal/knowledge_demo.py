"""Knowledge Lite stage demo that needs no auth-service user creation.

Two process invocations stand in for two sessions (Slice 2 criterion S3,
cut down): `seed` is session A writing profiles + a decision + a rule on
the demo scope; `recall` is a later session retrieving and citing them,
showing verified vs unverified, then confirming.

Usage:
  .venv/bin/python demo/rehearsal/knowledge_demo.py seed
  .venv/bin/python demo/rehearsal/knowledge_demo.py recall
Reads the stack token from .data/api-token (never prints it).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
LEDGER = os.environ.get("ROUVR_LEDGER_URL", "http://127.0.0.1:8221")
H = {"X-Rouvr-Token": (ROOT / ".data" / "api-token").read_text().strip()}
SCOPE = {"tenant": "supervisor", "project": "liveproof"}
CANARY = "quokka-ledger"


def post(path: str, body: dict) -> dict:
    r = httpx.post(f"{LEDGER}{path}", json=body, headers=H, timeout=15)
    r.raise_for_status()
    return r.json()


def search(q: str) -> list[dict]:
    r = httpx.get(f"{LEDGER}/v1/knowledge/search", params={"q": q}, headers=H, timeout=15)
    r.raise_for_status()
    return [e for e in r.json()["items"] if e["tenant"] == SCOPE["tenant"]]


def seed() -> int:
    if search(CANARY):
        print("already seeded (canary found); knowledge is append-only, not duplicating")
        return 0
    up = post("/v1/knowledge", {**SCOPE, "kind": "user_profile",
                                "title": "User profile: Ankur (demo)",
                                "body": json.dumps({
                                    "display_name": "Ankur", "role_title": "Founder",
                                    "organisation": "Rouvr",
                                    "use_case": "Governed agent delivery"})})
    pp = post("/v1/knowledge", {**SCOPE, "kind": "project_profile",
                                "title": "Project profile: Rouvr liveproof",
                                "body": json.dumps({
                                    "name": "liveproof", "type": "software",
                                    "purpose": "Prove governed coding loops live",
                                    "north_star": "Signed receipt per delivered task",
                                    "standing_rules": "Cite knowledge entries used."})})
    for e in (up, pp):  # owner-authored profiles are confirmed
        post(f"/v1/knowledge/{e['id']}/confirm", {})
    dec = post("/v1/knowledge", {**SCOPE, "kind": "decision",
                                 "title": "Decision: knowledge store is SQLite",
                                 "body": f"We chose per-ledger SQLite + FTS5 for the "
                                         f"hackathon cut ({CANARY}); per-tenant files "
                                         f"and embeddings stay in Slice 2."})
    rule = post("/v1/knowledge", {**SCOPE, "kind": "rule",
                                  "title": "Rule (proposed by agent): always cite",
                                  "body": "Every answer using project knowledge must "
                                          "cite knowledge:<id>@v<n>."})
    print(f"seeded profiles {up['citation']} {pp['citation']} (verified)")
    print(f"seeded decision {dec['citation']} rule {rule['citation']} (unverified)")
    return 0


def recall() -> int:
    hits = search(CANARY)
    assert hits, "nothing found: run seed first"
    dec = hits[0]
    print(f"later session found {dec['citation']}: {dec['title']} verified={dec['verified']}")
    rules = [e for e in search("cite") if e["kind"] == "rule"]
    assert rules, "rule not found"
    rule = rules[0]
    print(f"agent-proposed rule {rule['citation']} verified={rule['verified']}"
          " -> retrieved, but NOT binding until confirmed")
    confirmed = post(f"/v1/knowledge/{dec['id']}/confirm", {})
    print(f"human confirmed decision -> verified={confirmed['verified']}")
    prof = [e for e in search("liveproof") if e["kind"] == "project_profile"]
    assert prof and prof[0]["verified"] == 1, prof
    print(f"project profile {prof[0]['citation']} verified=1")
    print("RECALL PASS")
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    raise SystemExit(seed() if cmd == "seed" else recall() if cmd == "recall"
                     else print(__doc__) or 2)
