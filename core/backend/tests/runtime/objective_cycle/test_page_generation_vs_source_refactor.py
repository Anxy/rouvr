import pytest

from backend.aix_core.runtime.executor.real_execute import (
    looks_like_markdown_doc_objective,
    looks_like_page_generation_objective,
    _run_docs_proposal_execute,
)


def test_php_refactor_with_dashboard_domain_is_not_page_generation() -> None:
    objective = (
        r"Refactor D:\v2.1\Visions\CronJobController.php: group public cron action "
        "methods by domain with clear section comments (Fee, Broadcast/FCM, Student, "
        "Payments/Webhooks, Dashboard/Reports), extract repeated DB/query boilerplate "
        "into private helper methods inside the same file, remove unused use-imports, "
        "and keep all public method names and HTTP/middleware behavior unchanged."
    )
    assert looks_like_page_generation_objective(objective) is False


def test_html_dashboard_page_request_still_is_page_generation() -> None:
    assert looks_like_page_generation_objective(
        "Create an HTML and CSS dashboard page with charts"
    ) is True


def test_empty_directory_named_web_app_is_not_page_generation() -> None:
    from backend.aix_core.runtime.executor.real_execute import (
        looks_like_create_empty_directory_objective,
        resolve_empty_directory_marker_path,
    )

    objective = (
        "Create an empty directory named my-web-app at my-web-app. "
        "Track it with my-web-app/.gitkeep. Do not modify any existing files."
    )
    assert looks_like_create_empty_directory_objective(objective) is True
    assert looks_like_page_generation_objective(objective) is False
    assert resolve_empty_directory_marker_path(objective=objective) == "my-web-app/.gitkeep"


def test_nursery_poems_markdown_is_docs_generation() -> None:
    assert looks_like_markdown_doc_objective(
        "create like this around 10 poems in a .md file"
    ) is True
    assert looks_like_page_generation_objective(
        "create like this around 10 poems in a .md file"
    ) is False


def test_php_create_helper_is_not_docs_generation() -> None:
    assert looks_like_markdown_doc_objective(
        "create a helper method in CronJobController.php"
    ) is False


@pytest.mark.asyncio
async def test_docs_proposal_writes_ten_nursery_poems() -> None:
    from pathlib import Path

    workspace = Path(r"D:\v2.1\AIX\.tmp\docs-proposal-test")
    if workspace.exists():
        import shutil

        shutil.rmtree(workspace, ignore_errors=True)
    workspace.mkdir(parents=True, exist_ok=True)
    result = await _run_docs_proposal_execute(
        objective="create like this around 10 poems in a .md file",
        workspace=workspace,
        workhub_loaded=False,
        artifacts={},
    )
    assert result.ok is True
    assert result.code == "PROPOSAL_READY"
    md_path = workspace / "proposed" / "nursery-good-habits-poems.md"
    assert md_path.is_file()
    text = md_path.read_text(encoding="utf-8")
    assert text.count("## ") >= 10
    assert "Good Habits" in text
    assert "Please" in text
