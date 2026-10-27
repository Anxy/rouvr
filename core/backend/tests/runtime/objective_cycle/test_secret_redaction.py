"""SMTP_PASSWORD (and other env-var-prefixed credentials) must be redacted before an
objective is ever persisted — in source code, generated artifacts, objective
payloads, WorkHub tasks, logs, events, or diffs."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from backend.aix_core.runtime.governance.secret_boundary import looks_like_unredacted_secret, redact_objective_text
from backend.aix_core.runtime.executor.real_execute import run_real_execute


def _init_git_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=root, check=True)


@pytest.mark.parametrize(
    ("raw", "secret_value"),
    [
        ("please add SMTP_PASSWORD=hunter2secret to the config", "hunter2secret"),
        ("DB_PASSWORD: mySup3rSecret!", "mySup3rSecret!"),
        ("MY-API-KEY=sk-live-abc123def456", "sk-live-abc123def456"),
        ("set password=trustno1 for the mailer", "trustno1"),
    ],
)
def test_env_var_prefixed_and_bare_secrets_are_redacted(raw: str, secret_value: str) -> None:
    redacted, kinds = redact_objective_text(raw)
    assert kinds, f"expected a secret kind to be detected in: {raw!r}"
    assert "***REDACTED***" in redacted
    # The literal secret value must never survive redaction, even though the
    # placeholder itself still superficially looks "key=value"-shaped to the
    # best-effort `looks_like_unredacted_secret` heuristic scanner.
    assert secret_value not in redacted
    # Before this fix, `\b` failed to match right after an `_`/`-` prefix, so the
    # original raw text with an env-var-style prefix would itself have gone
    # completely undetected — confirm the pre-redaction text *would* have been
    # flagged, proving the detector itself (not just this test) sees it now.
    assert looks_like_unredacted_secret(raw)


def test_smtp_password_specifically_is_caught() -> None:
    """The literal regression this fix targets: `\\b` fails between `_` and the
    credential word, so `SMTP_PASSWORD=` previously passed through unredacted."""
    redacted, kinds = redact_objective_text("SMTP_PASSWORD=hunter2secret")
    assert "password" in kinds
    assert "hunter2secret" not in redacted
    assert "SMTP_PASSWORD=***REDACTED***" == redacted


def test_plain_text_without_secrets_is_unchanged() -> None:
    text = "Add SMTP configuration support to the mail adapter"
    redacted, kinds = redact_objective_text(text)
    assert redacted == text
    assert kinds == []


@pytest.mark.asyncio
async def test_real_execute_never_persists_the_raw_secret_value(tmp_path: Path, tmp_path_factory) -> None:
    repo = tmp_path / "hyeve-integration-service"
    (repo / "backend").mkdir(parents=True)
    (repo / "backend" / "config.py").write_text(
        "SMTP_HOST=''\nSMTP_PORT=587\n", encoding="utf-8", newline="\n"
    )
    (repo / "pyproject.toml").write_text(
        "[project]\nname = 'hyeve-integration-service'\n", encoding="utf-8", newline="\n"
    )
    (repo / "tests").mkdir()
    (repo / "tests" / "test_smoke.py").write_text("def test_smoke():\n    assert True\n", encoding="utf-8", newline="\n")
    _init_git_repo(repo)

    class _Settings:
        OBJECTIVE_CYCLE_REPOSITORY_ROOTS = json.dumps({"hyeve-integration-service": str(repo)})
        OPENROUTER_API_KEY = ""

    workspace_base = tmp_path_factory.mktemp("ws_base")
    secret_value = "hunter2-the-real-secret-value"

    result = await run_real_execute(
        tenant_id="tenant-1",
        cycle_id="cyc_secret1",
        objective_id="obj_secret1",
        objective=f"replace 'SMTP_HOST' with 'SMTP_HOST_X' and never store password={secret_value}",
        workhub_task_id="",
        project_id=None,
        task_number=None,
        repository_id="hyeve-integration-service",
        settings=_Settings(),
        require_workhub_load=False,
        workspace_base=workspace_base,
    )

    assert result.ok is True
    # The objective.json evidence artifact must never contain the raw secret value.
    objective_json_path = Path(result.artifacts["objective"])
    objective_payload = json.loads(objective_json_path.read_text(encoding="utf-8"))
    assert secret_value not in json.dumps(objective_payload)
    assert secret_value not in objective_payload["objective"]

    # Nor must any other written evidence artifact, or the returned detail string.
    for artifact_path in result.artifacts.values():
        path = Path(artifact_path)
        if path.is_file():
            assert secret_value not in path.read_text(encoding="utf-8", errors="replace")
    assert secret_value not in result.detail