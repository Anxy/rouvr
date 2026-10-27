from pathlib import Path

from backend.aix_core.runtime.objective.discovery import DiscoveryReport
from backend.aix_core.runtime.executor.source_patch_codegen import (
    _apply_route_refactor_content,
    local_fallback_route_file_refactor,
)


def test_route_refactor_removes_duplicates_and_normalizes_post() -> None:
    content = (
        "Route::Post('a', [AController::class, 'a']);\n"
        "Route::post('b', [AController::class, 'b']);\n"
        "Route::post('a', [BController::class, 'a']);\n"
        "Route::get('c', [BController::class, 'c']);\n"
    )
    objective = (
        "normalize Route::Post to lowercase post everywhere, "
        "remove duplicate route definitions, group routes by module with section comments"
    )
    updated, reasons = _apply_route_refactor_content(content, objective)
    assert "Route::Post" not in updated
    assert updated.count("Route::post('a'") == 1
    assert any("duplicate" in r for r in reasons)
    assert "// --- B routes ---" in updated or "// --- A routes ---" in updated


def test_local_fallback_route_file_refactor_produces_edit() -> None:
    discovery = DiscoveryReport(
        config_files={
            "web.php": (
                "Route::Post('x', [FooController::class, 'x']);\n"
                "Route::post('x', [BarController::class, 'x']);\n"
            )
        }
    )
    objective = "remove duplicate route definitions from web.php"
    result = local_fallback_route_file_refactor(discovery, objective)
    assert result is not None
    assert len(result.edits) == 1
    assert result.edits[0].path == "web.php"
    assert "Route::post('x'" in result.edits[0].new_content
    assert result.edits[0].new_content.count("Route::post('x'") == 1


def test_route_refactor_on_real_web_php_shape() -> None:
    fixture = (
        Path(__file__).resolve().parents[5]
        / "archive"
        / "legacy-stray-files-20260822"
        / "web.php"
    )
    content = fixture.read_text(encoding="utf-8", errors="replace")
    assert len(content) > 100_000, "expected full web.php fixture"
    objective = (
        "Refactor web.php: group routes by module with section comments, "
        "normalize Route::Post/Route::Get to lowercase post/get everywhere, "
        "and remove duplicate route definitions"
    )
    discovery = DiscoveryReport(config_files={"web.php": content})
    result = local_fallback_route_file_refactor(discovery, objective)
    assert result is not None
    assert len(result.edits) == 1
    new_content = result.edits[0].new_content
    assert new_content != content
    assert "// ---" in new_content
    assert "Route::Post" not in new_content
    assert any("duplicate" in r for r in result.edits[0].reason.split("; "))
