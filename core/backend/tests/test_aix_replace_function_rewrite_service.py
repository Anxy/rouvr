from __future__ import annotations

from backend.services.aix_replace_function_rewrite_service import (
    ObservedFunctionShape,
    ReplaceFunctionRewriteResponse,
    ReplaceFunctionRewriteServiceError,
    ReplaceFunctionRewriteSpec,
    build_rewrite_response_format,
    invoke_replace_function_rewrite,
    load_replace_function_rewrite_trace,
    parse_replace_function_rewrite_response,
    persist_replace_function_rewrite_trace,
)


def _spec() -> ReplaceFunctionRewriteSpec:
    return ReplaceFunctionRewriteSpec(
        relative_path="services/hyeve-codeintel-service/backend/services/codeintel_query_service.py",
        qualified_symbol="backend.services.codeintel_query_service.CodeintelQueryService.answer_context_pack",
        edit_intent="Return one marker item instead of an empty list.",
        preserve_constraints=("Preserve async.", "Preserve receiver."),
        line_targets=("return []",),
    )


def _shape() -> ObservedFunctionShape:
    return ObservedFunctionShape(
        qualified_symbol=_spec().qualified_symbol,
        function_name="answer_context_pack",
        header="async def answer_context_pack(self, request):",
        is_async=True,
        receiver_name="self",
        line_count=2,
    )


def test_parse_replace_function_rewrite_response_accepts_valid_schema_payload() -> None:
    parsed = parse_replace_function_rewrite_response(
        {
            "qualified_symbol": _spec().qualified_symbol,
            "replacement": "async def answer_context_pack(self, request):\n    return ['x']\n",
            "notes": ["kept async"],
        },
        expected_symbol=_spec().qualified_symbol,
    )

    assert parsed == ReplaceFunctionRewriteResponse(
        qualified_symbol=_spec().qualified_symbol,
        replacement="async def answer_context_pack(self, request):\n    return ['x']",
        notes=("kept async",),
    )


def test_parse_replace_function_rewrite_response_rejects_missing_qualified_symbol() -> None:
    try:
        parse_replace_function_rewrite_response(
            {"replacement": "async def answer_context_pack(self, request):\n    return ['x']\n"},
            expected_symbol=_spec().qualified_symbol,
        )
    except ReplaceFunctionRewriteServiceError as exc:
        assert exc.code == "REWRITE_RESPONSE_QUALIFIED_SYMBOL_REQUIRED"
    else:
        raise AssertionError("expected ReplaceFunctionRewriteServiceError")


def test_parse_replace_function_rewrite_response_rejects_missing_replacement() -> None:
    try:
        parse_replace_function_rewrite_response(
            {"qualified_symbol": _spec().qualified_symbol},
            expected_symbol=_spec().qualified_symbol,
        )
    except ReplaceFunctionRewriteServiceError as exc:
        assert exc.code == "REWRITE_RESPONSE_REPLACEMENT_REQUIRED"
    else:
        raise AssertionError("expected ReplaceFunctionRewriteServiceError")


def test_parse_replace_function_rewrite_response_rejects_qualified_symbol_mismatch() -> None:
    try:
        parse_replace_function_rewrite_response(
            {
                "qualified_symbol": "backend.services.codeintel_query_service.CodeintelQueryService.wrong_name",
                "replacement": "async def answer_context_pack(self, request):\n    return ['x']\n",
            },
            expected_symbol=_spec().qualified_symbol,
        )
    except ReplaceFunctionRewriteServiceError as exc:
        assert exc.code == "REWRITE_RESPONSE_SYMBOL_MISMATCH"
    else:
        raise AssertionError("expected ReplaceFunctionRewriteServiceError")


def test_parse_replace_function_rewrite_response_rejects_tool_name_contamination() -> None:
    try:
        parse_replace_function_rewrite_response(
            {
                "qualified_symbol": _spec().qualified_symbol,
                "replacement": "async def answer_context_pack(self, request):\n    return ['x']\n",
                "tool": "edit_file",
            },
            expected_symbol=_spec().qualified_symbol,
        )
    except ReplaceFunctionRewriteServiceError as exc:
        assert exc.code == "REWRITE_RESPONSE_CONTAMINATED"
        assert exc.context["extra_fields"] == ["tool"]
    else:
        raise AssertionError("expected ReplaceFunctionRewriteServiceError")


def test_parse_replace_function_rewrite_response_rejects_planner_step_contamination() -> None:
    try:
        parse_replace_function_rewrite_response(
            {
                "qualified_symbol": _spec().qualified_symbol,
                "replacement": "async def answer_context_pack(self, request):\n    return ['x']\n",
                "steps": [],
            },
            expected_symbol=_spec().qualified_symbol,
        )
    except ReplaceFunctionRewriteServiceError as exc:
        assert exc.code == "REWRITE_RESPONSE_CONTAMINATED"
        assert exc.context["extra_fields"] == ["steps"]
    else:
        raise AssertionError("expected ReplaceFunctionRewriteServiceError")


def test_parse_replace_function_rewrite_response_rejects_fence_contamination() -> None:
    try:
        parse_replace_function_rewrite_response(
            {
                "qualified_symbol": _spec().qualified_symbol,
                "replacement": "```python\nasync def answer_context_pack(self, request):\n    return ['x']\n```",
            },
            expected_symbol=_spec().qualified_symbol,
        )
    except ReplaceFunctionRewriteServiceError as exc:
        assert exc.code == "REWRITE_RESPONSE_CONTAMINATED"
        assert exc.context["reason"] == "fenced_replacement"
    else:
        raise AssertionError("expected ReplaceFunctionRewriteServiceError")


def test_invoke_replace_function_rewrite_uses_structured_schema_and_retains_notes() -> None:
    captured: dict[str, object] = {}

    def _fake_request(**kwargs):
        captured.update(kwargs)
        return (
            {
                "qualified_symbol": _spec().qualified_symbol,
                "replacement": "async def answer_context_pack(self, request):\n    return ['x']\n",
                "notes": ["diagnostic only"],
            },
            {"provider": "openrouter"},
        )

    parsed, diagnostics = invoke_replace_function_rewrite(
        tenant_id="tenant",
        project_id="project",
        correlation_id="corr",
        workspace_root="C:/dev/hyeve",
        observed_source="async def answer_context_pack(self, request):\n    return []\n",
        spec=_spec(),
        shape=_shape(),
        provider="openrouter",
        model="model-x",
        authorization="Bearer test",
        request_json=_fake_request,
    )

    assert parsed.notes == ("diagnostic only",)
    assert diagnostics["rewrite_response_notes"] == ["diagnostic only"]
    assert captured["response_format"] == build_rewrite_response_format()
    assert diagnostics["observed_source_hash"]
    assert diagnostics["rewrite_response_parsed"]["qualified_symbol"] == _spec().qualified_symbol


def test_build_rewrite_response_format_requires_all_declared_properties_for_strict_schema() -> None:
    response_format = build_rewrite_response_format()
    schema = response_format["json_schema"]["schema"]

    assert response_format["json_schema"]["strict"] is True
    assert schema["required"] == ["qualified_symbol", "replacement", "notes"]
    assert set(schema["properties"]) == {"qualified_symbol", "replacement", "notes"}


def test_persist_replace_function_rewrite_trace_round_trips_heavy_artifacts(
    tmp_path, monkeypatch
) -> None:
    from backend.services import aix_replace_function_rewrite_service as module

    monkeypatch.setattr(module, "get_rewrite_trace_root", lambda: tmp_path)

    trace = persist_replace_function_rewrite_trace(
        tenant_id="tenant",
        project_id="project",
        correlation_id="corr",
        attempt=1,
        observed_source_hash="abc123",
        qualified_symbol=_spec().qualified_symbol,
        validation_error_input=None,
        raw_response={"qualified_symbol": _spec().qualified_symbol, "replacement": "async def answer_context_pack(...): pass"},
        parsed_response={"qualified_symbol": _spec().qualified_symbol, "replacement": "async def answer_context_pack(...): pass"},
        validation_error=None,
    )

    loaded = load_replace_function_rewrite_trace(trace_ref=trace.trace_ref)
    assert loaded["trace_id"] == trace.trace_id
    assert loaded["qualified_symbol"] == _spec().qualified_symbol
    assert loaded["raw_response"]["qualified_symbol"] == _spec().qualified_symbol
