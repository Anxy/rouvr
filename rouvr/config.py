"""rouvr/config.py — typed settings loaded from env (PROPOSAL)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env(name: str, default: str | None = None) -> str:
    v = os.environ.get(name, default)
    if v is None:
        raise RuntimeError(f"missing required env var {name}")
    return v


def _bool(name: str, default: bool) -> bool:
    return _env(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError as e:
        raise RuntimeError(f"{name} must be an integer") from e


@dataclass(frozen=True)
class Settings:
    tenant_id: str
    project_id: str
    temporal_address: str
    temporal_namespace: str
    workhub_base_url: str
    database_url: str
    persona_registry_path: Path
    workspace_root: Path
    evidence_root: Path
    muse_bin: str
    max_rework: int = 2
    lease_ttl_s: int = 120
    heartbeat_s: int = 30
    assign_start_to_close_s: int = 1800
    coder_concurrency: int = 2
    resume_supported: bool = True  # R2 spike verdict: same-session-id resume works
    spec_provider: str = "local"
    receipt_key_path: Path = field(default=Path("/srv/rouvr/keys/receipt_ed25519.pem"))
    receipt_key_id: str = "rouvr-receipt-1"
    testrun_runner_url: str = "http://localhost:8090"
    hidden_tests_root: Path = Path("/srv/rouvr/hidden-tests")  # R8a: outside any coder workspace
    runner_image: str = "rouvr-runner:py313-p9"  # R8: testrunner.Dockerfile build; pytest baked in
    persona_source: str = "aix"          # "aix" (owner decision 3) | "yaml"
    aix_mapping_path: Path = Path("/srv/rouvr/personas/aix_mapping.json")  # Rouvr-defined, see personas.py
    start_api_token_file: Path = Path("/srv/rouvr/start_api.token")
    workhub_backend: str = "stage"      # "stage" | "ledger" (R10 rehearsal runs the local ledger)
    launch_windows: bool = False         # ROUVR_LAUNCH_WINDOWS=1: interactive muse chat per turn in a new terminal window
    spec_root: Path = Path("/srv/rouvr/specs")

    @staticmethod
    def from_env() -> "Settings":
        s = Settings(
            tenant_id=_env("ROUVR_TENANT_ID", "default"),
            project_id=_env("ROUVR_PROJECT_ID", "rouvr"),
            temporal_address=_env("TEMPORAL_ADDRESS", "localhost:7233"),
            temporal_namespace=_env("TEMPORAL_NAMESPACE", "default"),
            workhub_base_url=_env("WORKHUB_BASE_URL").rstrip("/"),
            database_url=_env("ROUVR_DATABASE_URL"),
            persona_registry_path=Path(_env("PERSONA_REGISTRY_PATH", "/srv/rouvr/personas/tenants.yaml")),
            persona_source=_env("PERSONA_SOURCE", "aix"),
            aix_mapping_path=Path(_env("AIX_MAPPING_PATH", "/srv/rouvr/personas/aix_mapping.json")),
            start_api_token_file=Path(_env("START_API_TOKEN_FILE", "/srv/rouvr/start_api.token")),
            workspace_root=Path(_env("WORKSPACE_ROOT", "/srv/rouvr/workspaces")),
            evidence_root=Path(_env("EVIDENCE_ROOT", "/srv/rouvr/evidence")),
            muse_bin=_env("MUSE_BIN", "muse"),
            max_rework=_int("MAX_REWORK", 2),
            lease_ttl_s=_int("LEASE_TTL_S", 120),
            heartbeat_s=_int("HEARTBEAT_S", 30),
            assign_start_to_close_s=_int("ASSIGN_START_TO_CLOSE_S", 1800),
            coder_concurrency=_int("CODER_CONCURRENCY", 2),
            resume_supported=_bool("RESUME_SUPPORTED", True),  # R2: RESUME
            spec_provider=_env("SPEC_PROVIDER", "local"),
            receipt_key_path=Path(_env("RECEIPT_SIGNING_KEY_PATH", "/srv/rouvr/keys/receipt_ed25519.pem")),
            receipt_key_id=_env("RECEIPT_KEY_ID", "rouvr-receipt-1"),
            testrun_runner_url=_env("TESTRUN_RUNNER_URL", "http://localhost:8090"),
            hidden_tests_root=Path(_env("HIDDEN_TESTS_ROOT", "/srv/rouvr/hidden-tests")),
            runner_image=_env("RUNNER_IMAGE", "rouvr-runner:py313-p9"),
            workhub_backend=_env("WORKHUB_BACKEND", "stage"),
            launch_windows=_bool("ROUVR_LAUNCH_WINDOWS", False),
            spec_root=Path(_env("SPEC_ROOT", "/srv/rouvr/specs")),
        )
        if s.max_rework < 0:
            raise RuntimeError("MAX_REWORK must be >= 0")
        if s.coder_concurrency < 1:
            raise RuntimeError("CODER_CONCURRENCY must be >= 1")
        if s.assign_start_to_close_s <= 0:
            raise RuntimeError("ASSIGN_START_TO_CLOSE_S must be positive")
        if s.heartbeat_s <= 0:
            raise RuntimeError("HEARTBEAT_S must be positive")
        if s.lease_ttl_s <= 0:
            raise RuntimeError("LEASE_TTL_S must be positive")
        if s.heartbeat_s * 3 > s.lease_ttl_s + 30:
            raise RuntimeError("HEARTBEAT_S too large for LEASE_TTL_S (need >=3 beats per lease)")
        if s.persona_source not in {"aix", "yaml"}:
            raise RuntimeError("PERSONA_SOURCE must be aix|yaml")
        if s.spec_provider not in {"local", "workhub"}:
            raise RuntimeError("SPEC_PROVIDER must be local|workhub")
        if s.workhub_backend not in {"stage", "ledger"}:
            raise RuntimeError("WORKHUB_BACKEND must be stage|ledger")
        return s
