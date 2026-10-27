from backend.aix_core.runtime.objective.discovery import DiscoveryReport
from backend.aix_core.runtime.executor.multi_step_execute import (
    _merge_codegen_usage,
    _usage_to_token_dict,
)
from backend.aix_core.runtime.executor.openrouter_codegen import OpenRouterUsage
from backend.aix_core.runtime.executor.source_patch_codegen import local_fallback_route_file_refactor


def test_local_route_refactor_reports_token_usage() -> None:
    discovery = DiscoveryReport(
        config_files={
            "web.php": (
                "Route::Post('x', [FooController::class, 'x']);\n"
                "Route::post('x', [BarController::class, 'x']);\n"
            )
        }
    )
    result = local_fallback_route_file_refactor(discovery, "remove duplicate route definitions from web.php")
    assert result is not None
    merged = _merge_codegen_usage(None, result.usage)
    usage = _usage_to_token_dict(merged)
    assert usage is not None
    assert usage["provider"] == "local"
    assert usage["model"] == "local-fallback"
    assert usage["ok"] is True
