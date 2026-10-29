"""rouvr/host.py — dependency container built once per worker process; activities receive it.

R6 carries settings + personas + exec_specs + muse + workhub_for. R8 adds tests,
P2 adds the pool. Fields stay None until their step lands."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from rouvr.config import Settings
from rouvr.execspec import ExecSpecProvider, LocalExecSpecProvider
from rouvr.muse import MuseLauncher
from rouvr.personas import Persona, PersonaRegistry, load_aix_mapping
from rouvr.testrunner import ContainerTestRunner, TestRunner
from rouvr.workhub.client import WorkHubClient
from rouvr.workhub.ledger import LedgerWorkHub
from rouvr.workhub.port import WorkHubPort


@dataclass
class Host:
    settings: Settings
    workhub_for: Callable[[Persona | None], WorkHubPort]
    personas: PersonaRegistry
    exec_specs: ExecSpecProvider
    muse: MuseLauncher
    tests: TestRunner                # R8: clean-container runner
    pool: Any | None = None          # P2


def build_host(s: Settings, *, transport: httpx.AsyncBaseTransport | None = None,
               orchestrator_token: str | None = None) -> Host:
    """Transport/token params are test seams (ledger arm, no live secrets in tests)."""
    reg = (load_aix_mapping(s.aix_mapping_path, s.tenant_id) if s.persona_source == "aix"
           else PersonaRegistry.load(s.persona_registry_path))
    cache: dict[str, WorkHubPort] = {}

    def wh(p: Persona | None) -> WorkHubPort:
        if s.workhub_backend == "ledger":  # local rehearsal: one shared ledger client, no auth
            if "_ledger" not in cache:
                cache["_ledger"] = LedgerWorkHub(s.workhub_base_url, transport=transport)
            return cache["_ledger"]
        key = p.name if p else "_orchestrator"
        if key not in cache:
            if p is None:
                token = orchestrator_token
                if token is None:
                    token = Path("/srv/rouvr/keys/orchestrator.token").read_text().strip()
            else:
                token = p.credentials()["workhub_token"]
            cache[key] = WorkHubClient(s.workhub_base_url, s.tenant_id, s.project_id, token,
                                       transport=transport)
        return cache[key]

    return Host(settings=s, workhub_for=wh, personas=reg,
                exec_specs=LocalExecSpecProvider(s.spec_root),
                muse=MuseLauncher(s.muse_bin), tests=ContainerTestRunner(s.runner_image))
