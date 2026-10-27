"""AIX Feature 12 (Packet 01) — Canonical Objective Truth V1.

Implements immutable truth representation derived directly from raw user objective.
Authority sequence: RAW USER OBJECTIVE -> Understanding -> CanonicalObjectiveTruthV1 -> OVC.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any
from pydantic import BaseModel, Field


def _compute_sha256(data: dict[str, Any]) -> str:
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class CanonicalObjectiveTruthV1(BaseModel):
    canonical_truth_id: str
    objective_id: str
    original_text: str  # immutable raw text
    interpreted_requirements: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    technology_requirements: list[str] = Field(default_factory=list)  # e.g. FastAPI, SQLite, uvicorn
    definition_of_done: list[str] = Field(default_factory=list)
    required_artifacts: list[str] = Field(default_factory=list)
    required_behaviors: list[str] = Field(default_factory=list)
    prohibited_changes: list[str] = Field(default_factory=list)
    hash: str
    version: str = "v1"
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def verify_integrity(self) -> bool:
        payload = {
            "canonical_truth_id": self.canonical_truth_id,
            "objective_id": self.objective_id,
            "original_text": self.original_text,
            "interpreted_requirements": self.interpreted_requirements,
            "constraints": self.constraints,
            "technology_requirements": self.technology_requirements,
            "definition_of_done": self.definition_of_done,
            "required_artifacts": self.required_artifacts,
            "required_behaviors": self.required_behaviors,
            "prohibited_changes": self.prohibited_changes,
            "version": self.version,
        }
        return _compute_sha256(payload) == self.hash


def derive_canonical_truth(
    original_text: str,
    *,
    objective_id: str = "",
    truth_id: str = "",
) -> CanonicalObjectiveTruthV1:
    """Derive CanonicalObjectiveTruthV1 deterministically from raw user objective."""
    text_clean = (original_text or "").strip()
    text_lower = text_clean.lower()

    # 1. Technology requirements detection
    tech_patterns = [
        ("fastapi", r"\bfastapi\b"),
        ("sqlite", r"\bsqlite(?:3)?\b"),
        ("postgresql", r"\b(?:postgres|postgresql)\b"),
        ("uvicorn", r"\buvicorn\b"),
        ("python", r"\bpython\b"),
        ("react", r"\breact\b"),
        ("html/css/javascript", r"\bhtml\b|\bcss\b|\bjavascript\b"),
        ("pytest", r"\bpytest\b"),
        ("sqlalchemy", r"\bsqlalchemy\b"),
        ("docker", r"\bdocker\b"),
    ]
    technologies: list[str] = []
    for tech_name, pat in tech_patterns:
        if re.search(pat, text_lower):
            technologies.append(tech_name)

    # 2. Interpreted requirements & behaviors
    requirements: list[str] = []
    behaviors: list[str] = []
    dod: list[str] = []
    artifacts: list[str] = []

    # Auth detection
    if re.search(r"\b(?:register|registration|signup)\b", text_lower):
        requirements.append("User registration endpoint and persistence")
        behaviors.append("Allow new users to register securely")
    if re.search(r"\b(?:login|signin|authenticate|auth|jwt)\b", text_lower):
        requirements.append("User login and session/token authentication")
        behaviors.append("Authenticate registered users and protect private endpoints")

    # CRUD detection
    crud_ops = []
    if re.search(r"\b(?:add|create|new)\b", text_lower):
        crud_ops.append("create")
    if re.search(r"\b(?:edit|update|modify)\b", text_lower):
        crud_ops.append("update")
    if re.search(r"\b(?:delete|remove)\b", text_lower):
        crud_ops.append("delete")
    if re.search(r"\b(?:list|get|view|show|read)\b", text_lower):
        crud_ops.append("read/list")
    
    if crud_ops or re.search(r"\bcrud\b", text_lower):
        requirements.append(f"Complete CRUD operations ({', '.join(crud_ops or ['create', 'read', 'update', 'delete'])})")
        behaviors.append("Persist and mutate entity records via API endpoints")

    # Expense Tracker specific domain detection
    if "expense" in text_lower:
        requirements.append("Expense management domain entities (amount, category, date, description)")
        artifacts.append("backend database schema with expenses table")

    # Tech stack specific requirements
    if "fastapi" in technologies:
        requirements.append("FastAPI application instance with API routing")
        artifacts.append("backend/main.py or app entrypoint with FastAPI")
        dod.append("FastAPI app initializes and serves OpenAPI docs / health")
    if "sqlite" in technologies:
        requirements.append("SQLite database storage authority")
        artifacts.append("SQLite database file or connection URI")
        dod.append("Data persists to SQLite without requiring external database services")
    if "uvicorn" in technologies or "fastapi" in technologies:
        requirements.append("ASGI web server startup (uvicorn)")
        dod.append("Application successfully starts and listens for HTTP requests")

    # General testing & E2E DoD
    dod.append("All automated tests execute and pass")
    dod.append("End-to-end workflow functions from client request through database persistence")

    # Constraints
    constraints: list[str] = []
    prohibited_changes: list[str] = []
    if "sqlite" in technologies and "postgresql" not in technologies:
        constraints.append("Must use SQLite for persistence authority; do not substitute with external DB like PostgreSQL")
        prohibited_changes.append("Do not require PostgreSQL server or external database daemons")

    obj_id = objective_id or f"obj_{hashlib.md5(text_clean.encode()).hexdigest()[:12]}"
    t_id = truth_id or f"truth_{hashlib.md5((obj_id + text_clean).encode()).hexdigest()[:12]}"

    payload = {
        "canonical_truth_id": t_id,
        "objective_id": obj_id,
        "original_text": text_clean,
        "interpreted_requirements": requirements,
        "constraints": constraints,
        "technology_requirements": technologies,
        "definition_of_done": dod,
        "required_artifacts": artifacts,
        "required_behaviors": behaviors,
        "prohibited_changes": prohibited_changes,
        "version": "v1",
    }
    truth_hash = _compute_sha256(payload)

    return CanonicalObjectiveTruthV1(
        canonical_truth_id=t_id,
        objective_id=obj_id,
        original_text=text_clean,
        interpreted_requirements=requirements,
        constraints=constraints,
        technology_requirements=technologies,
        definition_of_done=dod,
        required_artifacts=artifacts,
        required_behaviors=behaviors,
        prohibited_changes=prohibited_changes,
        hash=truth_hash,
        version="v1",
    )


__all__ = [
    "CanonicalObjectiveTruthV1",
    "derive_canonical_truth",
]
