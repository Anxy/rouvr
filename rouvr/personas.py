"""rouvr/personas.py — persona registry. Source: the AIX mapping (owner decision 3, 29 Oct 2026); YAML kept as fallback. Secrets resolved from files, never env/inputs.

tenants.yaml:
  tenants:
    default:
      personas:
        builder-1: {role: builder, principal: svc-rouvr-builder-1, muse_model: muse-wonder-1.0,
                    credential_file: /srv/rouvr/personas/builder-1.json, github_app: rouvr-builder}
        verifier-1: {role: verifier, principal: svc-rouvr-verifier-1, muse_model: muse-spark,
                     credential_file: /srv/rouvr/personas/verifier-1.json}
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml

from rouvr.errors import Code, RouvrError

Role = Literal["builder", "verifier"]


@dataclass(frozen=True)
class Persona:
    name: str
    role: Role
    principal: str
    muse_model: str
    credential_file: Path
    github_app: str | None = None

    def credentials(self) -> dict:
        # AIX-hardened: refuse creds files readable beyond the owner. Never log the result.
        try:
            mode = self.credential_file.stat().st_mode
        except FileNotFoundError as e:
            raise RouvrError(Code.AUTHZ, f"credential file missing for {self.name}") from e
        if mode & 0o077:
            raise RouvrError(Code.AUTHZ, f"credential file for {self.name} must be 0600")
        try:
            data = json.loads(self.credential_file.read_text())
        except ValueError as e:
            raise RouvrError(Code.AUTHZ, f"credential file for {self.name} is not valid JSON") from e
        if not isinstance(data, dict):
            raise RouvrError(Code.AUTHZ, f"credential file for {self.name} must hold a JSON object")
        return data


class PersonaRegistry:
    def __init__(self, data: dict) -> None:
        self._t: dict[str, dict[str, Persona]] = {}
        for tenant, td in (data.get("tenants") or {}).items():
            self._t[tenant] = {n: Persona(name=n, role=p["role"], principal=p["principal"],
                                          muse_model=p["muse_model"], credential_file=Path(p["credential_file"]),
                                          github_app=p.get("github_app"))
                               for n, p in (td.get("personas") or {}).items()}

    @classmethod
    def load(cls, path: Path) -> "PersonaRegistry":
        return cls(yaml.safe_load(path.read_text()) or {})

    def get(self, tenant: str, name: str, role: Role) -> Persona:
        p = self._t.get(tenant, {}).get(name)
        if p is None:
            raise RouvrError(Code.SCOPE, f"persona {name} not registered for tenant {tenant}")
        if p.role != role:
            raise RouvrError(Code.INELIGIBLE, f"persona {name} is {p.role}, need {role}")
        return p

    def pair(self, tenant: str, builder: str, verifier: str) -> tuple[Persona, Persona]:
        b, v = self.get(tenant, builder, "builder"), self.get(tenant, verifier, "verifier")
        if b.principal == v.principal:
            raise RouvrError(Code.SELF_REVIEW, "verifier principal equals builder principal")
        return b, v

    def by_role(self, tenant: str, role: Role) -> Persona:
        names = sorted(n for n, p in self._t.get(tenant, {}).items() if p.role == role)
        if not names:
            raise RouvrError(Code.SCOPE, f"no {role} persona registered for tenant {tenant}")
        return self._t[tenant][names[0]]

    def role_pair(self, tenant: str) -> tuple[Persona, Persona]:
        """Builder/verifier by tenant role. Stage-confirmed: the assignment
        binding names no personas (single `persona` field), so the pair comes
        from the registry, with the same SELF_REVIEW teeth as pair()."""
        b, v = self.by_role(tenant, "builder"), self.by_role(tenant, "verifier")
        if b.principal == v.principal:
            raise RouvrError(Code.SELF_REVIEW, "verifier principal equals builder principal")
        return b, v


# --- Owner decision 3 (29 Oct 2026): the AIX mapping is the persona registry source. ---
# R6-confirmed vs hyeve-lane @ d2e39d8: AIX keeps per-role 0600 JSON creds files
# ({auth_base, username, password, tenant, workhub_base?, project?}, see
# tools/bridge_creds.py) and logs in per run with the JWT memory-only. There is
# no single AIX mapping file: the mapping below is ROUVR-DEFINED (authored at
# deploy from the AIX roles) and points at those per-role creds files:
#   {"roles": {"builder":  {"principal": "svc-...", "creds_file": "/srv/...json", "model": "muse-wonder-1.0"},
#              "reviewer": {"principal": "svc-...", "creds_file": "/srv/...json", "model": "muse-spark"},
#              "closer":   {"principal": "svc-...", "creds_file": "/srv/...json"}}}
# AIX "reviewer" maps to the Rouvr "verifier" role. Persona name = AIX role name. Single tenant.
# Deploy check: the referenced creds files must exist, be 0600, and hold meta_api_key (headless
# Muse auth, R6 probe) + github_token (PRs) + workhub_token (API calls).
AIX_ROLE_MAP = {"builder": "builder", "reviewer": "verifier", "verifier": "verifier"}
DEFAULT_MODELS = {"builder": "muse-wonder-1.0", "verifier": "muse-spark"}


def registry_from_aix_mapping(data: dict, tenant: str) -> PersonaRegistry:
    roles = data.get("roles")
    if not isinstance(roles, dict):
        raise RouvrError(Code.CONTRACT, "AIX mapping has no 'roles' object (see personas.py for the format)")
    personas: dict[str, dict] = {}
    for aix_role, r in roles.items():
        role = AIX_ROLE_MAP.get(aix_role)
        if role is None:
            continue  # closer etc.: not a Muse persona
        if "principal" not in r or "creds_file" not in r:
            raise RouvrError(Code.CONTRACT, f"AIX mapping role {aix_role} lacks principal/creds_file")
        personas[aix_role] = {"role": role, "principal": r["principal"],
                              "muse_model": r.get("model", DEFAULT_MODELS[role]),
                              "credential_file": r["creds_file"]}
    return PersonaRegistry({"tenants": {tenant: {"personas": personas}}})


def load_aix_mapping(path: Path, tenant: str) -> PersonaRegistry:
    return registry_from_aix_mapping(json.loads(path.read_text()), tenant)
