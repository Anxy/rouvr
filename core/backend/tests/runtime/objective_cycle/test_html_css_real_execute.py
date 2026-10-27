"""Tests for HTML/CSS page real-execute path with token usage."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.aix_core.runtime.executor.openrouter_codegen import HtmlCssBundle, OpenRouterUsage, local_fallback_html_css_bundle
from backend.aix_core.runtime.executor.real_execute import run_real_execute

_WS_ROOT = Path(r"D:\v2.1\AIX\.tmp\aix-cycle-test-workspaces")


@pytest.fixture
def workspace_base(request: pytest.FixtureRequest) -> Path:
    base = _WS_ROOT / request.node.name
    if base.exists():
        for child in sorted(base.rglob("*"), reverse=True):
            if child.is_file() or child.is_symlink():
                child.unlink(missing_ok=True)
            elif child.is_dir():
                child.rmdir()
        base.rmdir()
    base.mkdir(parents=True, exist_ok=True)
    return base


@pytest.mark.asyncio
async def test_html_css_objective_writes_page_tests_and_tokens(
    workspace_base: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _fake_generate(
        *, objective: str, settings: object | None = None, **_kwargs: object
    ) -> HtmlCssBundle:
        bundle = local_fallback_html_css_bundle(objective)
        bundle.usage = OpenRouterUsage(
            prompt_tokens=120,
            completion_tokens=80,
            total_tokens=200,
            model="openai/gpt-4.1-mini",
            provider="openrouter",
            ok=True,
            detail="mocked",
        )
        return bundle

    monkeypatch.setattr(
        "backend.aix_core.runtime.executor.real_execute.generate_html_css_page",
        _fake_generate,
    )
    result = await run_real_execute(
        tenant_id="tenant-1",
        cycle_id="cyc_html1",
        objective_id="obj_html1",
        objective="Create an HTML and CSS contact page with validation tests",
        workhub_task_id="wh-1",
        project_id=None,
        task_number=None,
        repository_id="aix-frontend",
        require_workhub_load=False,
        workspace_base=workspace_base,
        demo_capability=True,
    )
    assert result.ok is True
    assert result.token_usage is not None
    assert result.token_usage["total_tokens"] == 200
    assert result.token_usage["prompt_tokens"] == 120
    assert result.token_usage["completion_tokens"] == 80
    workspace = Path(result.workspace)
    assert (workspace / "proposed" / "index.html").is_file()
    assert (workspace / "proposed" / "styles.css").is_file()
    assert (workspace / "proposed" / "tests" / "positive_cases.json").is_file()
    assert (workspace / "proposed" / "tests" / "negative_cases.json").is_file()
    assert (workspace / "evidence" / "token_usage.json").is_file()
    assert "total=200" in result.detail
