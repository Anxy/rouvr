"""rouvr/personas.py — persona registry. Source: the AIX mapping (owner decision 3, 29 Oct 2026); YAML kept as fallback. Secrets resolved from files, never env/inputs.

tenants.yaml:
  tenants:
    default:
      personas:
        coder-1: {role: coder, principal: svc-rouvr-coder-1, muse_model: muse-wonder-1.0,
                    credential_file: /srv/rouvr/personas/coder-1.json, github_app: rouvr-coder}
        reviewer-1: {role: reviewer, principal: svc-rouvr-reviewer-1, muse_model: muse-spark,
                     credential_file: /srv/rouvr/personas/reviewer-1.json}
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml

from rouvr.errors import Code, RouvrError

Role = Literal["coder", "reviewer"]


_INSTRUCTION_ALIAS = {"coder": "coder", "reviewer": "reviewer"}


@dataclass(frozen=True)
class Persona:
    name: str
    role: Role
    principal: str
    muse_model: str
    credential_file: Path
    github_app: str | None = None
    instructions: str = ""

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
    def __init__(self, data: dict, base_dir: Path | None = None) -> None:
        if not isinstance(data, dict):
            raise RouvrError(Code.CONTRACT, "persona registry must be a mapping with 'tenants'")
        tenants = data.get("tenants") or {}
        if not isinstance(tenants, dict):
            raise RouvrError(Code.CONTRACT, "persona registry 'tenants' must be a mapping")
        self._t: dict[str, dict[str, Persona]] = {}
        for tenant, td in tenants.items():
            if not isinstance(td, dict) or not isinstance(td.get("personas") or {}, dict):
                raise RouvrError(Code.CONTRACT, f"persona tenant {tenant!r} must be a mapping with 'personas'")
            built: dict[str, Persona] = {}
            for n, p in (td.get("personas") or {}).items():
                if not isinstance(p, dict):
                    raise RouvrError(Code.CONTRACT, f"persona {tenant}/{n} must be a mapping")
                missing = [k for k in ("role", "principal", "muse_model", "credential_file") if k not in p]
                if missing:
                    raise RouvrError(Code.CONTRACT, f"persona {tenant}/{n} missing {missing}")
                bad = [k for k in ("role", "principal", "muse_model", "credential_file")
                       if not isinstance(p[k], str)]
                if bad:
                    raise RouvrError(Code.CONTRACT, f"persona {tenant}/{n} values must be strings: {bad}")
                if p["role"] not in {"coder", "reviewer"}:
                    raise RouvrError(Code.CONTRACT, f"persona {tenant}/{n} role must be coder|reviewer")
                built[n] = Persona(name=n, role=p["role"], principal=p["principal"],
                                   muse_model=p["muse_model"], credential_file=Path(p["credential_file"]),
                                   github_app=p.get("github_app"),
                                   instructions=self._load_instructions(base_dir, n, p["role"]))
            self._t[tenant] = built

    @staticmethod
    def _load_instructions(base_dir: Path | None, name: str, role: str) -> str:
        """Persona MD beside the registry: <name>.md, else coder.md/reviewer.md."""
        if base_dir is None:
            return ""
        for candidate in (f"{name}.md", f"{_INSTRUCTION_ALIAS.get(role, role)}.md"):
            p = base_dir / candidate
            if p.exists():
                return p.read_text()
        return ""

    @classmethod
    def load(cls, path: Path) -> "PersonaRegistry":
        return cls(yaml.safe_load(path.read_text()) or {}, base_dir=path.parent)

    def get(self, tenant: str, name: str, role: Role) -> Persona:
        p = self._t.get(tenant, {}).get(name)
        if p is None:
            raise RouvrError(Code.SCOPE, f"persona {name} not registered for tenant {tenant}")
        if p.role != role:
            raise RouvrError(Code.INELIGIBLE, f"persona {name} is {p.role}, need {role}")
        return p

    def pair(self, tenant: str, coder: str, reviewer: str) -> tuple[Persona, Persona]:
        b, v = self.get(tenant, coder, "coder"), self.get(tenant, reviewer, "reviewer")
        if b.principal == v.principal:
            raise RouvrError(Code.SELF_REVIEW, "reviewer principal equals coder principal")
        return b, v

    def by_role(self, tenant: str, role: Role) -> Persona:
        names = sorted(n for n, p in self._t.get(tenant, {}).items() if p.role == role)
        if not names:
            raise RouvrError(Code.SCOPE, f"no {role} persona registered for tenant {tenant}")
        return self._t[tenant][names[0]]

    def role_pair(self, tenant: str) -> tuple[Persona, Persona]:
        """Coder/reviewer by tenant role. Stage-confirmed: the assignment
        binding names no personas (single `persona` field), so the pair comes
        from the registry, with the same SELF_REVIEW teeth as pair()."""
        b, v = self.by_role(tenant, "coder"), self.by_role(tenant, "reviewer")
        if b.principal == v.principal:
            raise RouvrError(Code.SELF_REVIEW, "reviewer principal equals coder principal")
        return b, v


# --- Owner decision 3 (29 Oct 2026): the AIX mapping is the persona registry source. ---
# R6-confirmed vs hyeve-lane @ d2e39d8: AIX keeps per-role 0600 JSON creds files
# ({auth_base, username, password, tenant, workhub_base?, project?}, see
# tools/bridge_creds.py) and logs in per run with the JWT memory-only. There is
# no single AIX mapping file: the mapping below is ROUVR-DEFINED (authored at
# deploy from the AIX roles) and points at those per-role creds files:
#   {"roles": {"coder":  {"principal": "svc-...", "creds_file": "/srv/...json", "model": "muse-wonder-1.0"},
#              "reviewer": {"principal": "svc-...", "creds_file": "/srv/...json", "model": "muse-spark"},
#              "closer":   {"principal": "svc-...", "creds_file": "/srv/...json"}}}
# AIX "reviewer" maps to the Rouvr "reviewer" role. Persona name = AIX role name. Single tenant.
# Deploy check: the referenced creds files must exist, be 0600, and hold meta_api_key (headless
# Muse auth, R6 probe) + github_token (PRs) + workhub_token (API calls).
AIX_ROLE_MAP = {"coder": "coder", "reviewer": "reviewer", "reviewer": "reviewer"}
DEFAULT_MODELS = {"coder": "muse-wonder-1.0", "reviewer": "muse-spark"}


def registry_from_aix_mapping(data: dict, tenant: str) -> PersonaRegistry:
    if not isinstance(data, dict):
        raise RouvrError(Code.CONTRACT, "AIX mapping must be a JSON object (see personas.py for the format)")
    roles = data.get("roles")
    if not isinstance(roles, dict):
        raise RouvrError(Code.CONTRACT, "AIX mapping has no 'roles' object (see personas.py for the format)")
    personas: dict[str, dict] = {}
    for aix_role, r in roles.items():
        role = AIX_ROLE_MAP.get(aix_role)
        if role is None:
            continue  # closer etc.: not a Muse persona
        if not isinstance(r, dict):
            raise RouvrError(Code.CONTRACT, f"AIX mapping role {aix_role} must be a mapping")
        if "principal" not in r or "creds_file" not in r:
            raise RouvrError(Code.CONTRACT, f"AIX mapping role {aix_role} lacks principal/creds_file")
        personas[aix_role] = {"role": role, "principal": r["principal"],
                              "muse_model": r.get("model", DEFAULT_MODELS[role]),
                              "credential_file": r["creds_file"]}
    # shortcut: AIX personas get no instructions (intentionally YAML-only), pass base_dir here if AIX-side *.md files ever exist
    return PersonaRegistry({"tenants": {tenant: {"personas": personas}}})


def load_aix_mapping(path: Path, tenant: str) -> PersonaRegistry:
    return registry_from_aix_mapping(json.loads(path.read_text()), tenant)
