"""In-memory WorkHub stand-in for local demos and tests. NOT for production.

Stores tasks, results, reviews and evidence in process memory and exposes a
small, generic REST surface. Point WORKHUB_API_URL at it (default :8221).
"""
from __future__ import annotations

import itertools
import time
from typing import Any

from fastapi import FastAPI, HTTPException

app = FastAPI(title="Rouvr stub WorkHub")
_ids = itertools.count(1)
_store: dict[str, dict[int, dict[str, Any]]] = {}


def _col(kind: str) -> dict[int, dict[str, Any]]:
    return _store.setdefault(kind, {})


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "stub": True}


@app.post("/v1/{kind}")
def create(kind: str, body: dict) -> dict:
    rid = next(_ids)
    rec = {"id": rid, "created_at": time.time(), **body}
    _col(kind)[rid] = rec
    return rec


@app.get("/v1/{kind}")
def list_(kind: str) -> dict:
    return {"items": list(_col(kind).values())}


@app.get("/v1/{kind}/{rid}")
def get(kind: str, rid: int) -> dict:
    if rid not in _col(kind):
        raise HTTPException(404, "not found")
    return _col(kind)[rid]


@app.patch("/v1/{kind}/{rid}")
def patch(kind: str, rid: int, body: dict) -> dict:
    rec = get(kind, rid)
    rec.update(body)
    return rec
