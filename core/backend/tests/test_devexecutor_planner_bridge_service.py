from __future__ import annotations

import importlib
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from backend.services import devexecutor_planner_bridge_service as planner_bridge
from backend.services.aix_quality_enforcement_service import QualityEnforcementError
from backend.services.devexecutor_planner_bridge_service import (
    PlannerBridgeError,
    _build_planner_completion_evidence,
    _build_planner_trace_packet_id,
    _enforce_declared_validation_step,
    _extract_plan_output_text,
    _lint_semantic_noop,
    _normalize_planner_steps,
    _parse_plan_output,
    _planner_http_client,
    _planner_schema_validation_errors,
    _repair_plan_output_text,
    _resolve_planner_base_url,
    plan_steps_via_llm,
    request_planner_json_via_llm,
    validate_edit_grounding,
)
from backend.services.devexecutor_planner_request_envelope_service import build_planner_request_envelope_with_accounting
from backend.services.devexecutor_tool_contract_registry_service import build_planner_contract_text
from backend.services.devexecutor_tool_loop_service import execute_tool_loop


def _find_free_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class _PlannerSeamHandler(BaseHTTPRequestHandler):
    response_payload: dict[str, object] = {"data": {"output_text": '{"steps":[{"tool":"get_status","args":{}}]}'}}
    response_sequence: list[dict[str, object]] = []
    requests: list[dict[str, object]] = []

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or "0")
        body = self.rfile.read(length).decode("utf-8")
        self.__class__.requests.append({"path": self.path, "headers": dict(self.headers), "body": json.loads(body)})
        response_payload = (
            self.__class__.response_sequence.pop(0)
            if self.__class__.response_sequence
            else self.__class__.response_payload
        )
        payload = json.dumps(response_payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return None


def _compiled_planner_user_payload(body: dict[str, Any]) -> dict[str, Any]:
    messages = cast(list[dict[str, Any]], body["messages"])
    return cast(dict[str, Any], json.loads(str(messages[1]["content"])))


def _valid_planner_packet() -> dict[str, Any]:
    return {
        "schema_name": "planner_slice.current",
        "schema_version": "current",
        "task_number": 143,
        "slice_id": "slice-143-001",
        "objective": "Fix route behavior",
        "target_file": "backend/routers/x.py",
        "target_symbol": None,
        "bounded_mutation_goal": "Return 422 on invalid input.",
        "allowed_surface": {
            "anchor": {
                "symbol": None,
                "compact_snippet": "if invalid_input:\n    return error_422()",
            }
        },
        "validation_command": "pytest tests/test_x.py -q",
        "planner_mode": "generic",
        "output_schema": {
            "type": "edit",
            "fields": ["type", "file", "search_range", "edit_range_within_read", "replacement_text", "command"],
        },
        "hard_failure_rules": ["insufficient context -> fail immediately with reason"],
    }


def _valid_gateway_body() -> dict[str, Any]:
    return {
        "provider": "slavx",
        "model": "model",
        "messages": [
            {"role": "system", "content": "Return JSON."},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "schema": "aix.utf_task.current",
                        "task_id": "corr",
                        "task_type": "planner_json_request",
                        "params": {"instruction": "return plan"},
                        "actions": ["return_json_object"],
                    },
                    separators=(",", ":"),
                ),
            },
        ],
    }


def _packet_bound_single_symbol_planner_packet() -> dict[str, Any]:
    packet = _valid_planner_packet()
    packet.pop("tool_policy", None)
    packet.pop("acceptance_criteria", None)
    packet.pop("codeintel_anchor", None)
    packet.pop("implementation_hints", None)
    packet.pop("provenance", None)
    packet.pop("lineage", None)
    packet["task_number"] = 2846
    packet["slice_id"] = "slice-2846-real-workspace-valid-001"
    packet["objective"] = "Real packet-present workspace-valid replay after transport restoration"
    packet["target_file"] = "backend/services/example.py"
    packet["target_symbol"] = "backend.services.example.run"
    packet["allowed_surface"] = {
        "anchor": {
            "symbol": "backend.services.example.run",
            "compact_snippet": "def run() -> None:\n    value = 1\n    return None",
        }
    }
    packet["validation_command"] = "python -m py_compile backend/services/example.py"
    packet["planner_mode"] = "packet_bound_replace_function"
    packet["output_schema"] = {
        "type": "replace_function",
        "fields": [
            "type",
            "file",
            "symbol",
            "qualified_symbol",
            "edit_intent",
            "preserve_constraints",
            "line_targets",
            "command",
        ],
    }
    packet["hard_failure_rules"] = [
        "edit_file is forbidden for this lane; use read_range(symbol)+replace_function only",
        "replace_function must carry bounded rewrite-spec fields only, never a direct replacement body",
        "if the symbol-scoped preread or rewrite spec cannot be grounded, fail immediately with reason",
    ]
    return packet


def test_build_planner_contract_text_requires_replace_function_for_packet_bound_single_symbol_lane() -> None:
    text = build_planner_contract_text(_packet_bound_single_symbol_planner_packet())

    assert "the only allowed mutation primitive is replace_function" in text
    assert "Do not emit edit_file" in text
    assert "Example replace_function plan:" in text


def test_normalize_planner_steps_accepts_structured_replace_function_step() -> None:
    steps = _normalize_planner_steps(
        [
            {
                "type": "read_range",
                "file": "services/hyeve-codeintel-service/backend/services/codeintel_query_service.py",
                "symbol": "backend.services.codeintel_query_service.CodeintelQueryService.answer_context_pack",
            },
            {
                "type": "replace_function",
                "file": "services/hyeve-codeintel-service/backend/services/codeintel_query_service.py",
                "qualified_symbol": "backend.services.codeintel_query_service.CodeintelQueryService.answer_context_pack",
                "edit_intent": "Return 1 instead of the previous value.",
                "preserve_constraints": ["preserve exact function name"],
            },
            {"type": "run_validation_command", "command": "pytest tests/test_example.py -q"},
        ],
        max_steps=4,
        planner_packet={
            "schema_name": "planner_slice.current",
            "planner_mode": "packet_bound_replace_function",
            "task_number": 325,
            "target_file": "services/hyeve-codeintel-service/backend/services/codeintel_query_service.py",
            "validation_command": "python -m pytest services/hyeve-codeintel-service/tests/test_codeintel_query_service.py -q --tb=short -k skips_duplicate_test_prefix",
        },
    )

    assert steps[1]["tool"] == "replace_function"
    assert steps[1]["args"]["function_name"].endswith("answer_context_pack")
    assert steps[1]["args"]["edit_intent"] == "Return 1 instead of the previous value."


def test_planner_schema_validation_accepts_replace_function_shape() -> None:
    errors = _planner_schema_validation_errors(
        {
            "steps": [
                {
                    "type": "replace_function",
                    "file": "backend/services/example.py",
                    "qualified_symbol": "Example.run",
                    "edit_intent": "Return 1 instead of the previous value.",
                },
                {"type": "run_validation_command", "command": "pytest tests/test_example.py -q"},
            ]
        }
    )

    assert errors == []


def test_normalize_planner_steps_rejects_direct_replace_function_body_for_codeintel_325_lane() -> None:
    with pytest.raises(PlannerBridgeError, match="PLANNER_REPLACE_FUNCTION_DIRECT_BODY_FORBIDDEN"):
        _normalize_planner_steps(
            [
                {
                    "type": "replace_function",
                    "file": "services/hyeve-codeintel-service/backend/services/codeintel_query_service.py",
                    "qualified_symbol": "backend.services.codeintel_query_service.CodeintelQueryService.answer_context_pack",
                    "replacement": "async def answer_context_pack(self, request):\n    return ['x']\n",
                }
            ],
            max_steps=4,
            planner_packet={
                "schema_name": "planner_slice.current",
                "planner_mode": "packet_bound_replace_function",
                "task_number": 325,
                "target_file": "services/hyeve-codeintel-service/backend/services/codeintel_query_service.py",
                "validation_command": "python -m pytest services/hyeve-codeintel-service/tests/test_codeintel_query_service.py -q --tb=short -k skips_duplicate_test_prefix",
            },
        )


def test_plan_steps_via_llm_rejects_edit_file_contract_violation_for_codeintel_325_lane(tmp_path: Path) -> None:
    from backend.services import devexecutor_planner_bridge_service as planner_module

    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(parents=True, exist_ok=True)
    planner_packet = {
        "schema_name": "planner_slice.current",
        "schema_version": "current",
        "planner_mode": "packet_bound_replace_function",
        "task_number": 325,
        "slice_id": "slice-325-001",
        "objective": "Repair duplicate test_ prefix via replace_function only.",
        "target_file": "services/hyeve-codeintel-service/backend/services/codeintel_query_service.py",
        "target_symbol": "backend.services.codeintel_query_service.CodeintelQueryService.answer_context_pack",
        "bounded_mutation_goal": (
            "Read the grounded symbol, emit replace_function only, and run the declared validation command."
        ),
        "allowed_surface": {
            "anchor": {
                "symbol": "backend.services.codeintel_query_service.CodeintelQueryService.answer_context_pack",
                "compact_snippet": "async def answer_context_pack(...):\n    return payload\n",
            }
        },
        "validation_command": (
            "python -m pytest services/hyeve-codeintel-service/tests/test_codeintel_query_service.py "
            "-q --tb=short -k skips_duplicate_test_prefix"
        ),
        "output_schema": {
            "type": "edit",
            "fields": [
                "type",
                "file",
                "qualified_symbol",
                "edit_intent",
                "preserve_constraints",
                "line_targets",
                "command",
            ],
        },
        "hard_failure_rules": [
            "edit_file is forbidden for this lane; use read_range(symbol)+replace_function only",
        ],
    }
    violating_payload: dict[str, object] = {
        "data": {
            "output_text": json.dumps(
                {
                    "steps": [
                        {
                            "type": "read_range",
                            "file": "services/hyeve-codeintel-service/backend/services/codeintel_query_service.py",
                            "symbol": "backend.services.codeintel_query_service.CodeintelQueryService.answer_context_pack",
                        },
                        {
                            "type": "edit_file",
                            "file": "services/hyeve-codeintel-service/backend/services/codeintel_query_service.py",
                            "grounding_source": "anchor",
                            "search_range": {"start_line": 10, "end_line": 12},
                            "replacement_text": "value = ['x']\n",
                        },
                        {"type": "run_validation_command", "command": planner_packet["validation_command"]},
                    ]
                }
            )
        }
    }
    _PlannerSeamHandler.requests = []
    _PlannerSeamHandler.response_sequence = [violating_payload]
    _PlannerSeamHandler.response_payload = violating_payload
    port = _find_free_tcp_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), _PlannerSeamHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    previous_planner = planner_module.settings.RUNTIME_PLANNER_API_URL
    previous_core = planner_module.settings.CORE_API_URL
    planner_module.settings.RUNTIME_PLANNER_API_URL = f"http://127.0.0.1:{port}"
    planner_module.settings.CORE_API_URL = f"http://127.0.0.1:{port}"
    thread.start()
    try:
        with pytest.raises(PlannerBridgeError, match="PLANNER_CONTRACT_VIOLATION") as exc_info:
            plan_steps_via_llm(
                tenant_id="default",
                project_id="proj",
                correlation_id="corr-contract-violation-325",
                workspace_root=str(workspace_root),
                prompt="Repair duplicate prefix via replace_function only.",
                planner_packet=planner_packet,
                provider="slavx",
                authorization="Bearer token",
            )
    finally:
        planner_module.settings.RUNTIME_PLANNER_API_URL = previous_planner
        planner_module.settings.CORE_API_URL = previous_core
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        _PlannerSeamHandler.response_sequence = []

    assert len(_PlannerSeamHandler.requests) == 1
    assert "step_kind" in str(exc_info.value)
    assert "local_trace_ref=" in str(exc_info.value)


def test_enforce_declared_validation_step_accepts_replace_function_as_mutation() -> None:
    steps = _enforce_declared_validation_step(
        [
            {
                "tool": "replace_function",
                "args": {
                    "relative_path": "backend/services/example.py",
                    "qualified_symbol": "Example.run",
                    "replacement": "def run(self):\n    return 1\n",
                },
            }
        ],
        planner_packet={"validation_command": "pytest tests/test_example.py -q"},
    )

    assert steps[-1]["tool"] == "cmd.run"
    assert steps[-1]["args"]["command_text"] == "pytest tests/test_example.py -q"


def test_extract_plan_output_text_prefers_output_text() -> None:
    text = _extract_plan_output_text({"output_text": '{"steps":[{"tool":"get_status","args":{}}]}'})
    assert text.startswith('{"steps"')


def test_extract_plan_output_text_reads_nested_choice_message_content() -> None:
    data = {
        "response_json": {
            "id": "chatcmpl-1",
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": '{"steps":[{"tool":"get_status","args":{}}]}',
                    }
                }
            ],
        }
    }
    text = _extract_plan_output_text(data)
    assert '"steps"' in text


def test_extract_plan_output_text_accepts_plain_content_json_mode() -> None:
    data = {
        "response_json": {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": '{"steps":[{"tool":"get_status","args":{}}]}',
                        "tool_calls": None,
                        "function_call": None,
                    }
                }
            ]
        }
    }
    text = _extract_plan_output_text(data)
    assert '"steps"' in text


@st.composite
def _grounded_edit_payloads(draw):
    head = draw(st.text(alphabet=st.characters(min_codepoint=97, max_codepoint=122), min_size=1, max_size=8))
    search = draw(st.text(alphabet=st.characters(min_codepoint=97, max_codepoint=122), min_size=1, max_size=8))
    tail = draw(st.text(alphabet=st.characters(min_codepoint=97, max_codepoint=122), min_size=1, max_size=8))
    replacement_suffix = draw(
        st.text(alphabet=st.characters(min_codepoint=97, max_codepoint=122), min_size=1, max_size=4)
    )
    read_buffer = f"{head}{search}{tail}"
    assume(read_buffer.count(search) == 1)
    return {
        "read_buffer": read_buffer,
        "search": search,
        "replace": f"{search}{replacement_suffix}",
    }


@settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(payload=_grounded_edit_payloads())
def test_normalize_planner_steps_property_accepts_grounded_edit_sequences(payload) -> None:
    planner_packet = {
        "task_intent": "code_change_required",
        "target_file": "a.py",
        "validation_command": "pytest tests/test_x.py -q --tb=short",
    }

    steps = _normalize_planner_steps(
        [
            {"tool": "read_file", "args": {"relative_path": "a.py", "content": payload["read_buffer"]}},
            {
                "tool": "edit_file",
                "args": {
                    "relative_path": "a.py",
                    "search": payload["search"],
                    "replace": payload["replace"],
                    "read_source": {"step_idx": 0, "file": "a.py"},
                },
            },
            {
                "tool": "cmd.run",
                "args": {"command_text": "pytest tests/test_x.py -q --tb=short"},
            },
        ],
        max_steps=8,
        planner_packet=planner_packet,
    )

    assert steps[1]["tool"] == "edit_file"
    assert steps[1]["args"]["read_source"]["step_idx"] == 0
    assert steps[1]["args"]["read_source"]["file"] == "a.py"
    assert steps[-1]["tool"] == "cmd.run"


@settings(max_examples=20, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    search=st.text(alphabet=st.characters(min_codepoint=97, max_codepoint=122), min_size=1, max_size=12),
    prefix=st.text(alphabet=st.characters(min_codepoint=97, max_codepoint=122), min_size=1, max_size=8),
    suffix=st.text(alphabet=st.characters(min_codepoint=97, max_codepoint=122), min_size=1, max_size=8),
)
def test_normalize_planner_steps_property_rejects_identity_edits(search, prefix, suffix) -> None:
    read_buffer = f"{prefix}{search}{suffix}"
    with pytest.raises(PlannerBridgeError, match="edit_file_identity_delta"):
        _normalize_planner_steps(
            [
                {"tool": "read_file", "args": {"relative_path": "a.py", "content": read_buffer}},
                {
                    "tool": "edit_file",
                    "args": {
                        "relative_path": "a.py",
                        "search": search,
                        "replace": search,
                        "read_source": {"step_idx": 0, "file": "a.py"},
                    },
                },
                {
                    "tool": "cmd.run",
                    "args": {"command_text": "pytest tests/test_x.py -q --tb=short"},
                },
            ],
            max_steps=8,
            grounded_source=read_buffer,
            planner_packet={
                "task_intent": "code_change_required",
                "target_file": "a.py",
                "validation_command": "pytest tests/test_x.py -q --tb=short",
            },
        )


def test_plan_steps_via_llm_repairs_invalid_json_content_then_succeeds(monkeypatch) -> None:
    from backend.services import devexecutor_planner_bridge_service as module

    payloads = iter(
        [
            (
                {
                    "response_json": {
                        "choices": [
                            {
                                "message": {
                                    "role": "assistant",
                                    "content": '{"steps": [}',
                                    "tool_calls": None,
                                    "function_call": None,
                                }
                            }
                        ]
                    }
                },
                {
                    "attempts": 1,
                    "timeout_seconds": 30,
                    "max_retries": 0,
                    "retry_backoff_ms": 0,
                    "target_url": "http://planner",
                },
            ),
            (
                {
                    "response_json": {
                        "choices": [
                            {
                                "message": {
                                    "role": "assistant",
                                    "content": (
                                        '{"steps":[{"type":"read_range","file":"a.py","output_content":"foo\\n"},'
                                        '{"type":"edit_file","file":"a.py","search":"foo\\n","replace":"bar\\n",'
                                        '"read_source":{"step_idx":0}}]}'
                                    ),
                                    "tool_calls": None,
                                    "function_call": None,
                                }
                            }
                        ]
                    }
                },
                {
                    "attempts": 1,
                    "timeout_seconds": 30,
                    "max_retries": 0,
                    "retry_backoff_ms": 0,
                    "target_url": "http://planner",
                },
            ),
        ]
    )

    def _fake_request(**_kwargs):
        return next(payloads)

    monkeypatch.setattr(module, "_planner_request_payload", _fake_request)
    previous_shadow = module.settings.AYRPACK_SHADOW_WRITE_ENABLED
    module.settings.AYRPACK_SHADOW_WRITE_ENABLED = False
    try:
        steps, diagnostics = plan_steps_via_llm(
            tenant_id="default",
            project_id="proj",
            correlation_id="corr-json-repair",
            workspace_root="/workspace/hyeve",
            prompt="do it",
            planner_packet=_valid_planner_packet(),
            provider="openrouter",
            model="openrouter/deepseek/deepseek-r1",
            max_retries=0,
        )
    finally:
        module.settings.AYRPACK_SHADOW_WRITE_ENABLED = previous_shadow

    assert steps[0]["tool"] == "read_range"
    assert steps[1]["tool"] == "edit_file"
    assert diagnostics["json_repair_attempts_used"] == 1


def test_parse_plan_output_handles_markdown_json_fence() -> None:
    parsed = _parse_plan_output("""```json\n{"steps":[{"tool":"get_status","args":{}}]}\n```""")
    assert isinstance(parsed, dict)
    assert isinstance(parsed.get("steps"), list)


def test_repair_plan_output_text_closes_unterminated_json() -> None:
    broken = '{"steps":[{"tool":"list_dir","args":{"relative_path":"README'
    repaired = _repair_plan_output_text(broken)
    assert repaired is not None
    parsed = json.loads(repaired)
    assert isinstance(parsed, dict)
    assert isinstance(parsed.get("steps"), list)


def test_planner_schema_validation_rejects_tool_edit_file_read_source() -> None:
    parsed = {
        "steps": [
            {
                "tool": "read_range",
                "args": {"relative_path": "backend/routers/x.py", "start_line": 10, "end_line": 24},
                "output_content": "before = True\n",
            },
            {
                "tool": "edit_file",
                "args": {
                    "relative_path": "backend/routers/x.py",
                    "search_replace_blocks": "<<<<<<< SEARCH\nbefore = True\n=======\nafter = True\n>>>>>>> REPLACE",
                    "read_source": {"step_idx": 0},
                },
            },
            {"tool": "cmd.run", "args": {"command_text": "pytest tests/test_x.py -q"}},
        ]
    }

    assert _planner_schema_validation_errors(parsed) == ["step[1]_edit_file_read_source_forbidden"]


def test_planner_schema_validation_allows_tool_edit_file_without_read_source_for_normalizer_grounding() -> None:
    parsed = {
        "steps": [
            {
                "tool": "read_range",
                "args": {"relative_path": "backend/routers/x.py", "start_line": 10, "end_line": 24},
                "output_content": "before = True\n",
            },
            {
                "tool": "edit_file",
                "args": {
                    "relative_path": "backend/routers/x.py",
                    "grounding_source": "anchor",
                    "search_replace_blocks": "<<<<<<< SEARCH\nbefore = True\n=======\nafter = True\n>>>>>>> REPLACE",
                },
            },
        ]
    }

    assert _planner_schema_validation_errors(parsed) == []


def test_planner_schema_validation_rejects_type_edit_file_read_source() -> None:
    parsed = {
        "steps": [
            {
                "type": "read_range",
                "file": "backend/routers/x.py",
                "start_line": 10,
                "end_line": 24,
                "output_content": "before = True\n",
            },
            {
                "type": "edit_file",
                "file": "backend/routers/x.py",
                "search": "before = True\n",
                "replace": "after = True\n",
                "read_source": {"step_idx": 0},
            },
            {
                "type": "cmd.run",
                "args": {"command_text": "pytest tests/test_x.py -q"},
            },
        ]
    }

    assert _planner_schema_validation_errors(parsed) == ["step[1]_edit_file_read_source_forbidden"]


def test_planner_schema_validation_allows_type_edit_file_without_read_source_for_normalizer_grounding() -> None:
    parsed = {
        "steps": [
            {
                "type": "read_range",
                "file": "backend/routers/x.py",
                "start_line": 10,
                "end_line": 24,
                "output_content": "before = True\n",
            },
            {
                "type": "edit_file",
                "file": "backend/routers/x.py",
                "grounding_source": "anchor",
                "search": "before = True\n",
                "replace": "after = True\n",
            },
        ]
    }

    assert _planner_schema_validation_errors(parsed) == []


def test_planner_schema_validation_requires_edit_file_grounding_source() -> None:
    parsed = {
        "steps": [
            {
                "type": "edit_file",
                "file": "backend/routers/x.py",
                "search": "before = True\n",
                "replace": "after = True\n",
            }
        ]
    }

    assert _planner_schema_validation_errors(parsed) == ["step[0]_edit_file_missing_grounding_source"]


def test_planner_schema_validation_requires_read_step_idx_for_read_grounded_edit() -> None:
    parsed = {
        "steps": [
            {
                "type": "edit_file",
                "file": "backend/routers/x.py",
                "grounding_source": "read_range",
                "edit_range_within_read": {"start_line": 1, "end_line": 1},
                "replace": "after = True\n",
            }
        ]
    }

    assert _planner_schema_validation_errors(parsed) == ["step[0]_edit_file_missing_read_step_idx"]


def test_planner_schema_validation_requires_edit_range_within_read_for_read_grounded_edit() -> None:
    parsed = {
        "steps": [
            {
                "type": "edit_file",
                "file": "backend/routers/x.py",
                "grounding_source": "read_range",
                "read_step_idx": 0,
                "replace": "after = True\n",
            }
        ]
    }

    assert _planner_schema_validation_errors(parsed) == ["step[0]_edit_file_missing_edit_range_within_read"]


def test_planner_schema_validation_rejects_search_for_form_b() -> None:
    parsed = {
        "steps": [
            {
                "type": "edit_file",
                "file": "backend/routers/x.py",
                "grounding_source": "read_range",
                "read_step_idx": 0,
                "edit_range_within_read": {"start_line": 1, "end_line": 1},
                "search": "before = True\n",
                "replace": "after = True\n",
            }
        ]
    }

    assert _planner_schema_validation_errors(parsed) == ["step[0]_edit_file_read_range_search_forbidden"]


def test_planner_schema_validation_rejects_malformed_tool_edit_file_search_replace_blocks() -> None:
    parsed = {
        "steps": [
            {
                "tool": "edit_file",
                "args": {
                    "relative_path": "backend/routers/x.py",
                    "grounding_source": "anchor",
                    "search_replace_blocks": "<<<<<<< SEARCH\n=======\nafter = True\n>>>>>>> REPLACE",
                },
            }
        ]
    }

    assert _planner_schema_validation_errors(parsed) == ["step[0]_edit_file_malformed_search_replace_blocks"]


def test_planner_schema_validation_rejects_type_edit_file_search_replace_blocks_even_when_well_formed() -> None:
    parsed = {
        "steps": [
            {
                "type": "edit_file",
                "file": "backend/routers/x.py",
                "grounding_source": "anchor",
                "search": "before = True\n",
                "replace": "after = True\n",
                "search_replace_blocks": "<<<<<<< SEARCH\nbefore = True\n=======\nafter = True\n>>>>>>> REPLACE",
            }
        ]
    }

    errors = _planner_schema_validation_errors(parsed)
    assert "step[0]_edit_file_textual_patch_fields_forbidden" in errors


def test_planner_schema_validation_accepts_structured_anchor_edit() -> None:
    parsed = {
        "steps": [
            {
                "type": "edit_file",
                "file": "backend/service.py",
                "grounding_source": "anchor",
                "search_range": {"start_line": 10, "end_line": 12},
                "replacement_text": "updated text",
            },
            {"type": "run_validation_command", "command": "pytest -q"},
        ]
    }

    assert _planner_schema_validation_errors(parsed) == []


def test_parse_plan_output_recovers_repairable_json() -> None:
    parsed = _parse_plan_output('{"steps":[{"tool":"get_status","args":{}}]')
    assert isinstance(parsed, dict)
    assert isinstance(parsed.get("steps"), list)


def test_parse_plan_output_accepts_python_literal_dict() -> None:
    parsed = _parse_plan_output("{'steps':[{'tool':'get_status','args':{}}]}")
    assert isinstance(parsed, dict)
    assert parsed["steps"][0]["tool"] == "get_status"


def test_extract_plan_output_text_raises_when_missing_payload() -> None:
    with pytest.raises(PlannerBridgeError) as exc:
        _extract_plan_output_text({})
    assert "PLANNER_EMPTY_OUTPUT" in str(exc.value)


def test_extract_plan_output_text_classifies_provider_empty_response() -> None:
    with pytest.raises(PlannerBridgeError) as exc:
        _extract_plan_output_text(
            {
                "response_json": {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": None,
                                "tool_calls": None,
                                "function_call": None,
                            }
                        }
                    ]
                }
            }
        )
    assert "PROVIDER_EMPTY_RESPONSE" in str(exc.value)


def test_extract_plan_output_text_classifies_provider_reasoning_exhaustion() -> None:
    with pytest.raises(PlannerBridgeError) as exc:
        _extract_plan_output_text(
            {
                "usage": {"completion_tokens_details": {"reasoning_tokens": 2985}},
                "response_json": {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": None,
                                "tool_calls": None,
                                "function_call": None,
                            }
                        }
                    ]
                },
            }
        )
    text = str(exc.value)
    assert "PROVIDER_REASONING_EXHAUSTION" in text
    assert "reasoning_tokens=2985" in text


def test_extract_plan_output_text_raises_upstream_failed_details() -> None:
    with pytest.raises(PlannerBridgeError) as exc:
        _extract_plan_output_text(
            {
                "status": "failed",
                "provider": "slavx",
                "resolved_provider": "openai",
                "error": "INTEGRATION.PROVIDER_HTTP_ERROR",
            }
        )
    text = str(exc.value)
    assert "PLANNER_UPSTREAM_FAILED" in text
    assert "provider=slavx" in text
    assert "resolved_provider=openai" in text


def test_planner_http_client_uses_explicit_transport_budgets() -> None:
    with _planner_http_client(timeout_seconds=90) as client:
        assert client.timeout.connect == 10.0
        assert client.timeout.read == 90.0
        assert client.timeout.write == 30.0
        assert client.timeout.pool == 10.0
        assert getattr(client, "_trust_env", True) is False


def test_normalize_planner_steps_accepts_stringified_step_objects() -> None:
    steps = _normalize_planner_steps(
        {
            "steps": [
                '{"tool":"search","args":{"query":"planner bridge"}}',
                {"tool": "get_status", "args": {}},
            ]
        },
        max_steps=8,
    )
    assert steps[0]["tool"] == "search"
    assert steps[0]["args"]["query"] == "planner bridge"
    assert steps[1]["tool"] == "get_status"


def test_normalize_planner_steps_accepts_nested_step_and_tool_aliases() -> None:
    steps = _normalize_planner_steps(
        {"steps": [{"step": {"action": "read_file", "parameters": {"path": "README.md"}}}]},
        max_steps=4,
    )
    assert steps == [{"tool": "read_file", "args": {"path": "README.md"}, "step_idx": 0}]


def test_accepts_self_contained_edit_without_read_source() -> None:
    steps = _normalize_planner_steps(
        {
            "steps": [
                {"tool": "read_range", "args": {"relative_path": "a.py"}},
                {
                    "type": "edit_file",
                    "file": "a.py",
                    "grounding_source": "anchor",
                    "search": "x",
                    "replace": "y",
                },
            ]
        },
        max_steps=4,
    )
    assert steps[1]["tool"] == "edit_file"
    assert "read_source" not in steps[1]["args"]
    assert steps[1]["args"]["grounding_source"] == "anchor"


def test_rejects_edit_with_cross_step_read_source() -> None:
    with pytest.raises(PlannerBridgeError) as exc:
        _normalize_planner_steps(
            {
                "steps": [
                    {
                        "type": "edit_file",
                        "file": "a.py",
                        "search": "bar",
                        "replace": "baz",
                        "read_source": {"step_idx": 0},
                    },
                ]
            },
            max_steps=4,
        )
    assert "PLANNER_EDIT_CROSS_STEP_LINKAGE_FORBIDDEN" in str(exc.value)


def test_normalize_planner_steps_accepts_read_grounded_edit_with_explicit_step_idx() -> None:
    steps = _normalize_planner_steps(
        {
            "steps": [
                {
                    "type": "read_range",
                    "file": "a.py",
                    "start_line": 1,
                    "end_line": 2,
                    "output_content": "before = True\n",
                },
                {
                    "type": "edit_file",
                    "file": "a.py",
                    "grounding_source": "read_range",
                    "read_step_idx": 0,
                    "edit_range_within_read": {"start_line": 1, "end_line": 1},
                    "replace": "after = True\n",
                },
                {"type": "run_validation_command", "command": "pytest -q"},
            ]
        },
        max_steps=4,
        planner_packet={
            "schema_name": "planner_slice.current",
            "target_file": "services/hyeve-codeintel-service/backend/service.py",
        },
    )

    assert steps[1]["tool"] == "edit_file"
    assert steps[1]["args"]["grounding_source"] == "read_range"
    assert steps[1]["args"]["read_step_idx"] == 0
    assert steps[1]["args"]["edit_range_within_read"] == {"start_line": 1, "end_line": 1}
    assert steps[1]["args"]["__aix_v2_protocol_form"] == "B"


def test_normalize_planner_steps_rejects_read_grounded_edit_without_step_idx() -> None:
    with pytest.raises(PlannerBridgeError) as exc_info:
        _normalize_planner_steps(
            {
                "steps": [
                    {
                        "type": "edit_file",
                        "file": "a.py",
                        "grounding_source": "read_range",
                        "edit_range_within_read": {"start_line": 1, "end_line": 1},
                        "replace": "after = True\n",
                    }
                ]
            },
            max_steps=4,
        )

    assert "PLANNER_EDIT_MISSING_READ_PROVENANCE" in str(exc_info.value)


def test_normalize_planner_steps_rejects_read_grounded_edit_with_search_text() -> None:
    with pytest.raises(PlannerBridgeError) as exc_info:
        _normalize_planner_steps(
            {
                "steps": [
                    {
                        "type": "read_range",
                        "file": "a.py",
                        "start_line": 1,
                        "end_line": 2,
                    },
                    {
                        "type": "edit_file",
                        "file": "a.py",
                        "grounding_source": "read_range",
                        "read_step_idx": 0,
                        "edit_range_within_read": {"start_line": 1, "end_line": 1},
                        "search": "before = True\n",
                        "replace": "after = True\n",
                    },
                    {"type": "run_validation_command", "command": "pytest -q"},
                ]
            },
            max_steps=4,
            planner_packet={
                "schema_name": "planner_slice.current",
                "target_file": "services/hyeve-codeintel-service/backend/service.py",
            },
        )

    assert "PLANNER_EDIT_READ_RANGE_SEARCH_FORBIDDEN" in str(exc_info.value)


def test_normalize_planner_steps_rejects_invalid_v2_plan_shape_without_validation_step() -> None:
    with pytest.raises(PlannerBridgeError) as exc_info:
        _normalize_planner_steps(
            {
                "steps": [
                    {
                        "type": "edit_file",
                        "file": "a.py",
                        "grounding_source": "anchor",
                        "search": "before",
                        "replace": "after",
                    }
                ]
            },
            max_steps=4,
            planner_packet={
                "schema_name": "planner_slice.current",
                "target_file": "services/hyeve-codeintel-service/backend/service.py",
            },
        )

    assert "PLANNER_V2_PROTOCOL_INVALID" in str(exc_info.value)


def test_hydrates_search_range_edit_from_workspace_file_without_read_source(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    target = workspace_root / "a.py"
    target.write_text("line1\nbefore = True\nline3\n", encoding="utf-8")

    steps = _normalize_planner_steps(
        {
            "steps": [
                {
                    "type": "edit_file",
                    "file": "a.py",
                    "search_range": {"start_line": 2, "end_line": 2},
                    "replace": "after = True\n",
                }
            ]
        },
        max_steps=4,
        workspace_root=str(workspace_root),
    )

    assert steps[0]["tool"] == "edit_file"
    assert steps[0]["args"]["__aix_derived_search_range"]["mode"] == "workspace_file_lines"
    assert steps[0]["args"]["search_replace_blocks"] == (
        "<<<<<<< SEARCH\nbefore = True\n=======\nafter = True\n>>>>>>> REPLACE"
    )


def test_read_range_trace_fields_do_not_leak_into_executable_args() -> None:
    steps = _normalize_planner_steps(
        {
            "steps": [
                {
                    "type": "read_range",
                    "file": "a.py",
                    "symbol": "target",
                    "context_lines": 5,
                    "output_content": "foo\n",
                    "content": "foo\n",
                    "source": "planner_output_content",
                }
            ]
        },
        max_steps=4,
    )
    assert steps[0]["tool"] == "read_range"
    assert steps[0]["args"] == {"relative_path": "a.py", "symbol": "target", "context_lines": 5}
    assert steps[0]["output_content"] == "foo\n"
    assert steps[0]["content"] == "foo\n"
    assert steps[0]["source"] == "planner_output_content"


def test_contract_rejects_legacy_search_replace_blocks() -> None:
    with pytest.raises(PlannerBridgeError) as exc:
        _normalize_planner_steps(
            {
                "steps": [
                    {"type": "read_range", "file": "a.py", "output_content": "x"},
                    {
                        "type": "edit_file",
                        "file": "a.py",
                        "args": {"search_replace_blocks": [{"search": "x", "replace": "y"}]},
                    },
                ]
            },
            max_steps=4,
        )
    assert "PLANNER_LEGACY_SHAPE" in str(exc.value)


def test_normalize_planner_steps_accepts_stringified_list_payload() -> None:
    steps = _normalize_planner_steps(
        {"steps": '[{"tool":"search","args":{"query":"registry"}}]'},
        max_steps=4,
    )
    assert steps == [{"tool": "search", "args": {"query": "registry"}}]


def test_normalize_planner_steps_accepts_python_literal_list_payload() -> None:
    steps = _normalize_planner_steps(
        {"steps": "[{'tool':'search','args':{'query':'registry'}}]"},
        max_steps=4,
    )
    assert steps == [{"tool": "search", "args": {"query": "registry"}}]


def test_normalize_planner_steps_accepts_stringified_single_step_payload() -> None:
    steps = _normalize_planner_steps(
        {"steps": '{"tool":"get_status","args":{}}'},
        max_steps=4,
    )
    assert steps == [{"tool": "get_status", "args": {}}]


def test_normalize_planner_steps_raises_structured_insufficient_context() -> None:
    with pytest.raises(PlannerBridgeError) as exc:
        _normalize_planner_steps(
            {
                "error": "PLANNER_INSUFFICIENT_CONTEXT",
                "missing_fields": ["target_files", "mutation_instructions"],
                "reason": "packet does not ground a concrete mutation target",
            },
            max_steps=4,
        )
    assert str(exc.value) == (
        'PLANNER_INSUFFICIENT_CONTEXT:{"missing_fields": ["target_files", "mutation_instructions"], '
        '"reason": "packet does not ground a concrete mutation target"}'
    )


def test_normalize_planner_steps_accepts_wrapped_stringified_steps_payload() -> None:
    steps = _normalize_planner_steps(
        {"steps": '{"steps":"[{\\"tool\\":\\"get_status\\",\\"args\\":{}}]"}'},
        max_steps=4,
    )
    assert steps == [{"tool": "get_status", "args": {}}]


def test_normalize_planner_steps_reports_step_type_context() -> None:
    with pytest.raises(PlannerBridgeError) as exc:
        _normalize_planner_steps({"steps": [123]}, max_steps=4)
    assert "step_type=int" in str(exc.value)


def test_normalize_planner_steps_ignores_known_separator_noise() -> None:
    steps = _normalize_planner_steps(
        {"steps": ["tool.split", {"tool": "get_status", "args": {}}]},
        max_steps=4,
    )
    assert steps == [{"tool": "get_status", "args": {}}]


def test_normalize_planner_steps_rejects_evidence_attach_tool() -> None:
    with pytest.raises(PlannerBridgeError) as exc_info:
        _normalize_planner_steps(
            {
                "steps": [
                    {"tool": "evidence.attach", "args": {"description": "placeholder"}},
                    {"tool": "read_file", "args": {"path": "README.md"}},
                ]
            },
            max_steps=4,
        )
    assert "PLANNER_INVALID_TOOL" in str(exc_info.value)
    assert "evidence.attach" in str(exc_info.value)


def test_normalize_planner_steps_rejects_replace_range_tool() -> None:
    with pytest.raises(PlannerBridgeError) as exc_info:
        _normalize_planner_steps(
            {
                "steps": [
                    {"tool": "read_range", "args": {"relative_path": "demo.txt", "start_line": 1, "end_line": 2}},
                    {
                        "tool": "replace_range",
                        "args": {"relative_path": "demo.txt", "start_line": 2, "end_line": 2, "replacement": "x"},
                    },
                ]
            },
            max_steps=4,
        )
    assert "PLANNER_INVALID_TOOL" in str(exc_info.value)
    assert "replace_range" in str(exc_info.value)


def test_normalize_planner_steps_rejects_legacy_edit_file_tool_shape() -> None:
    with pytest.raises(PlannerBridgeError) as exc_info:
        _normalize_planner_steps(
            {
                "steps": [
                    {"tool": "read_file", "args": {"relative_path": "scripts/wh.py"}},
                    {
                        "tool": "edit_file",
                        "args": {
                            "relative_path": "scripts/wh.py",
                            "search_replace_blocks": ("<<<<<<< SEARCH\nold\n=======\nnew\n>>>>>>> REPLACE\n"),
                        },
                    },
                ]
            },
            max_steps=4,
        )
    assert "PLANNER_EDIT_MISSING_READ_SOURCE" in str(exc_info.value)


def test_edit_rejected_when_edit_file_has_no_prior_same_file_read() -> None:
    with pytest.raises(PlannerBridgeError) as exc_info:
        _normalize_planner_steps(
            {
                "steps": [
                    {
                        "tool": "edit_file",
                        "args": {
                            "relative_path": "a.py",
                            "search_replace_blocks": "<<<<<<< SEARCH\nfoo()\n=======\nbar()\n>>>>>>> REPLACE",
                        },
                    }
                ]
            },
            max_steps=4,
        )
    assert "PLANNER_EDIT_MISSING_READ_SOURCE" in str(exc_info.value)


def test_edit_rejected_when_search_not_in_prior_read_buffer() -> None:
    with pytest.raises(PlannerBridgeError) as exc_info:
        _normalize_planner_steps(
            {
                "steps": [
                    {
                        "tool": "read_range",
                        "args": {
                            "relative_path": "a.py",
                            "start_line": 1,
                            "end_line": 2,
                            "content": "def foo():\n    return 1\n",
                        },
                    },
                    {
                        "tool": "edit_file",
                        "args": {
                            "relative_path": "a.py",
                            "search_replace_blocks": "<<<<<<< SEARCH\ndef foo():\n return 1\n=======\ndef foo():\n    return 2\n>>>>>>> REPLACE",
                        },
                    },
                ]
            },
            max_steps=4,
        )
    assert "PLANNER_EDIT_SEARCH_NOT_FROM_READ" in str(exc_info.value)


def test_edit_accepted_when_search_exact_match_in_prior_read_buffer() -> None:
    steps = _normalize_planner_steps(
        {
            "steps": [
                {
                    "tool": "read_range",
                    "args": {
                        "relative_path": "a.py",
                        "start_line": 1,
                        "end_line": 2,
                        "content": "def foo():\n    return 1\n",
                    },
                },
                {
                    "type": "edit_file",
                    "file": "a.py",
                    "search": "def foo():\n    return 1\n",
                    "replace": "def foo():\n    return 2\n",
                    "read_source": {"step_idx": 0},
                },
            ]
        },
        max_steps=4,
    )
    assert steps[1]["tool"] == "edit_file"
    assert steps[1]["args"]["read_source"]["step_idx"] == 0


def test_edit_accepted_when_legacy_raw_edit_omits_read_source_but_prior_read_exists() -> None:
    steps = _normalize_planner_steps(
        {
            "steps": [
                {
                    "type": "read_range",
                    "file": "a.py",
                    "start_line": 1,
                    "end_line": 2,
                    "output_content": "def foo():\n    return 1\n",
                },
                {
                    "type": "edit_file",
                    "file": "a.py",
                    "search": "def foo():\n    return 1\n",
                    "replace": "def foo():\n    return 2\n",
                },
            ]
        },
        max_steps=4,
    )
    assert steps[1]["tool"] == "edit_file"
    assert steps[1]["args"]["read_source"]["step_idx"] == 0
    assert steps[1]["args"]["read_source"]["file"] == "a.py"


def test_normalize_planner_steps_normalizes_edit_file_list_bare_search_replace_blocks() -> None:
    with pytest.raises(PlannerBridgeError) as exc_info:
        _normalize_planner_steps(
            {
                "steps": [
                    {
                        "tool": "edit_file",
                        "args": {
                            "relative_path": "scripts/wh.py",
                            "search_replace_blocks": ["SEARCH\nold\nREPLACE\nnew\n"],
                        },
                    }
                ]
            },
            max_steps=4,
        )
    assert "PLANNER_EDIT_MISSING_READ_SOURCE" in str(exc_info.value)


def test_normalize_planner_steps_does_not_canonicalize_empty_bare_search_replace_blocks() -> None:
    with pytest.raises(PlannerBridgeError) as exc_info:
        _normalize_planner_steps(
            {
                "steps": [
                    {
                        "tool": "edit_file",
                        "args": {
                            "relative_path": "scripts/wh.py",
                            "search_replace_blocks": ["SEARCH\nREPLACE\n"],
                        },
                    }
                ]
            },
            max_steps=4,
        )
    assert "PLANNER_EDIT_MISSING_SEARCH" in str(exc_info.value)


def test_normalize_planner_steps_normalizes_edit_file_root_alias_args() -> None:
    with pytest.raises(PlannerBridgeError) as exc_info:
        _normalize_planner_steps(
            {
                "steps": [
                    {
                        "tool": "edit_file",
                        "args": {
                            "relative_path": "scripts/wh.py",
                            "search": "old\n",
                            "replace_text": "new\n",
                        },
                    }
                ]
            },
            max_steps=4,
        )
    assert "PLANNER_EDIT_MISSING_READ_SOURCE" in str(exc_info.value)


def test_normalize_planner_steps_normalizes_stringified_structured_edit_blocks() -> None:
    with pytest.raises(PlannerBridgeError) as exc_info:
        _normalize_planner_steps(
            {
                "steps": [
                    {
                        "tool": "edit_file",
                        "args": {
                            "relative_path": "scripts/wh.py",
                            "search_replace_blocks": '[{"search":"old\\n","replace":"new\\n"}]',
                        },
                    }
                ]
            },
            max_steps=4,
        )
    assert "PLANNER_EDIT_MISSING_READ_SOURCE" in str(exc_info.value)


def test_normalize_planner_steps_rejects_semantic_noop_edit_file_candidate() -> None:
    with pytest.raises(PlannerBridgeError) as exc_info:
        _normalize_planner_steps(
            {
                "steps": [
                    {
                        "tool": "read_range",
                        "args": {
                            "relative_path": "scripts/wh.py",
                            "start_line": 1,
                            "end_line": 1,
                            "content": "foo()\n",
                        },
                    },
                    {
                        "type": "edit_file",
                        "file": "scripts/wh.py",
                        "search": "foo()\n",
                        "replace": "foo()\n",
                        "read_source": {"step_idx": 0},
                    },
                ]
            },
            max_steps=4,
        )
    message = str(exc_info.value)
    assert "MUTATION_REQUIRED_BUT_ABSENT" in message
    assert "edit_file_identity_delta" in message


def test_normalize_planner_steps_rejects_identity_edit_file_before_dispatch() -> None:
    with pytest.raises(PlannerBridgeError) as exc_info:
        _normalize_planner_steps(
            {
                "steps": [
                    {
                        "type": "edit_file",
                        "file": "scripts/wh.py",
                        "search": "foo()\n",
                        "replace": "foo()\n",
                        "read_source": {"step_idx": 0},
                    }
                ]
            },
            max_steps=2,
            planner_packet={"grounded_symbol_source": "foo()\n", "target_file": "scripts/wh.py"},
        )
    message = str(exc_info.value)
    assert "MUTATION_REQUIRED_BUT_ABSENT" in message
    assert "edit_file_identity_delta" in message


def test_rejects_search_equals_replace_even_with_grounding() -> None:
    grounded = "return RunResult(ok=True)"
    steps: list[dict[str, Any]] = [
        {"tool": "read_range", "args": {"relative_path": "a.py", "content": grounded}, "step_idx": 0},
        {
            "tool": "edit_file",
            "args": {
                "relative_path": "a.py",
                "search_replace_blocks": f"<<<<<<< SEARCH\n{grounded}\n=======\n{grounded}\n>>>>>>> REPLACE",
                "read_source": {"step_idx": 0},
            },
        },
    ]
    with pytest.raises(PlannerBridgeError) as exc_info:
        _lint_semantic_noop(steps, grounded)
    assert "MUTATION_REQUIRED_BUT_ABSENT" in str(exc_info.value)


def test_normalize_planner_steps_rejects_planner_emitted_synthetic_read_step_zero() -> None:
    with pytest.raises(PlannerBridgeError) as exc_info:
        _normalize_planner_steps(
            {
                "steps": [
                    {
                        "type": "read_range",
                        "file": "scripts/wh.py",
                        "symbol": "main",
                        "context_lines": 10,
                        "step_idx": 0,
                        "output_content": "to be populated by read",
                    }
                ]
            },
            max_steps=2,
        )
    assert "PLANNER_INVALID_SYNTHETIC_READ" in str(exc_info.value)


def test_normalize_planner_steps_rejects_read_only_step_zero_plan_without_mutation() -> None:
    with pytest.raises(
        QualityEnforcementError,
        match="PLANNER_NO_MUTATING_STEP_FOR_CODE_CHANGE",
    ):
        _normalize_planner_steps(
            {
                "steps": [
                    {
                        "tool": "read_range",
                        "args": {"relative_path": "scripts/wh.py", "start_line": 1, "end_line": 10},
                        "step_idx": 0,
                    }
                ]
            },
            max_steps=2,
            planner_packet={"task_intent": "code_change_required"},
        )


def test_contract_text_includes_noop_prohibition() -> None:
    text = build_planner_contract_text(
        {
            "grounded_symbol_source": "def f():\n    pass\n",
            "target_symbol": "f",
            "target_file": "a.py",
        }
    )
    assert "Forbidden: any search string that is not an exact substring of the live target file" in text
    assert "failure_analysis" in text
    assert "final step of every real mutation plan must be run_validation_command" in text
    assert '"read_source":{"step_idx"' not in text
    assert "grounded_symbol_source" not in text
    assert "def f():" not in text
    assert "TERMINAL OUTCOME RULE (MANDATORY):" in text
    assert "Any edit_file containing read_source, step_idx, buffer_ref, or output_content" in text
    assert "Any read_range containing step_idx or output_content" in text
    assert "planner_packet.validation_command" in text


def test_contract_text_requires_real_anchor_read_when_anchor_window_is_present() -> None:
    text = build_planner_contract_text(
        {
            "grounded_symbol_source": "def f():\n    pass\n",
            "target_symbol": "f",
            "target_file": "a.py",
            "codeintel_anchor": {
                "file_path": "a.py",
                "symbol": "f",
                "signature": "def f():",
                "start_line": 10,
                "end_line": 12,
            },
        }
    )
    assert (
        "If the compact anchor snippet is sufficient, emit a directly grounded same-file edit_file using exact anchor text only."
        in text
    )
    assert (
        "Path A: if the compact anchor already contains the exact mutation hotspot, emit a direct same-file edit_file whose SEARCH is copied verbatim from that compact anchor."
        in text
    )
    assert (
        "Path B: if the compact anchor is insufficient, emit read_range on the declared anchor window first, then emit Form B using read_step_idx=0 plus edit_range_within_read on that same-file read output."
        in text
    )
    assert "Do not emit output_content for any anchor-window read_range." in text
    assert "planner_packet.codeintel_anchor.output_content" not in text
    assert "step 2 edit_file must remain self-contained" in text
    assert "Do not rewrite the entire anchored window" in text
    assert "return failure_analysis instead of attempting a broad rewrite" in text
    assert "at most 3 steps total" in text
    assert "must contain only type, file, start_line, and end_line" in text
    assert "do not scaffold with additional discovery steps before edit_file" in text
    assert '{"type":"read_range","file":str,"start_line":int,"end_line":int}' in text
    assert "Forbidden: any search string that is not an exact substring of the live target file" in text
    assert "Anchor-direct example:" in text


def test_build_planner_completion_evidence_marks_incomplete_json() -> None:
    evidence = _build_planner_completion_evidence(
        raw_output='{"steps":[{"type":"read_range","file":"a.py"},',
        transport_diag={
            "finish_reason": "stop",
            "reasoning_tokens": 0,
            "normalized_usage": {"completion_tokens": 500, "prompt_tokens": 9000},
        },
    )

    assert evidence["completion_state"] == "incomplete_json_object"
    assert evidence["json_closed_cleanly"] is False
    assert evidence["finish_reason"] == "stop"
    assert evidence["completion_tokens"] == 500
    assert evidence["first_top_level_fields"] == ["steps"]


def test_contract_text_requires_grounded_structural_patch_and_terminal_validation() -> None:
    text = build_planner_contract_text(
        {
            "grounded_symbol_source": "def f():\n    pass\n",
            "target_symbol": "f",
            "target_file": "a.py",
            "validation_command": "python -m pytest tests/test_a.py -q --tb=short",
            "codeintel_anchor": {
                "file_path": "a.py",
                "symbol": "f",
                "signature": "def f():",
                "start_line": 10,
                "end_line": 12,
            },
        }
    )
    assert "grounded structural patch" in text
    assert "search_replace_blocks" in text
    assert "final non-negotiable acceptance gate" in text
    assert "exit code 0 with PASSED output" in text


def test_grounded_symbol_source_creates_virtual_read() -> None:
    steps = _normalize_planner_steps(
        {
            "steps": [
                {
                    "type": "edit_file",
                    "file": "service.py",
                    "search": "return RunResult(ok=True)",
                    "replace": "return RunResult(ok=False)",
                    "read_source": {"step_idx": 0},
                }
            ]
        },
        max_steps=4,
        grounded_source="def _build_envelope_rejection():\n    return RunResult(ok=True)\n",
        planner_packet={
            "planner_mode": "generic",
            "target_symbol": "_build_envelope_rejection",
            "target_file": "service.py",
            "grounded_symbol_source": "def _build_envelope_rejection():\n    return RunResult(ok=True)\n",
        },
    )
    assert len(steps) == 1
    assert steps[0]["tool"] == "edit_file"
    assert steps[0]["args"]["read_source"]["step_idx"] == 0
    assert steps[0]["planner_grounded_buffer"] == "def _build_envelope_rejection():\n    return RunResult(ok=True)"
    assert steps[0]["planner_grounded_source"] == "grounded_symbol_source"


def test_grounded_symbol_source_does_not_create_virtual_read_when_anchor_window_must_be_read() -> None:
    with pytest.raises(PlannerBridgeError) as exc_info:
        _normalize_planner_steps(
            {
                "steps": [
                    {
                        "type": "edit_file",
                        "file": "service.py",
                        "search": "return RunResult(ok=True)",
                        "replace": "return RunResult(ok=False)",
                        "read_source": {"step_idx": 0},
                    }
                ]
            },
            max_steps=4,
            grounded_source="def _build_envelope_rejection():\n    return RunResult(ok=True)\n",
            planner_packet={
                "planner_mode": "generic",
                "target_symbol": "_build_envelope_rejection",
                "target_file": "service.py",
                "grounded_symbol_source": "def _build_envelope_rejection():\n    return RunResult(ok=True)\n",
                "enforce_read_before_write": True,
                "tool_policy": {"execution_lane": "planning_safe"},
                "codeintel_anchor": {
                    "file_path": "service.py",
                    "symbol": "_build_envelope_rejection",
                    "signature": "def _build_envelope_rejection() -> RunResult:",
                    "start_line": 10,
                    "end_line": 14,
                    "snapshot_id": "snap-1",
                    "commit_sha": "abc123",
                    "symbol_source_hash": "hash-1",
                },
            },
        )
    assert "PLANNER_EDIT_MISSING_READ" in str(exc_info.value)
    assert "no_prior_same_file_read" in str(exc_info.value)


def test_explicit_same_file_read_supersedes_synthetic_grounded_read_source() -> None:
    steps = _normalize_planner_steps(
        {
            "steps": [
                {
                    "type": "read_range",
                    "file": "service.py",
                    "start_line": 10,
                    "end_line": 14,
                    "output_content": 'details = {\n    "a": 1,\n}\n',
                },
                {
                    "type": "edit_file",
                    "file": "service.py",
                    "search": 'details = {\n    "a": 1,\n}\n',
                    "replace": 'details = {\n    "a": 1,\n    "b": 2,\n}\n',
                    "read_source": {"step_idx": 0},
                },
            ]
        },
        max_steps=6,
        grounded_source='details = {\n    "a": 1,\n}\n',
        planner_packet={
            "planner_mode": "generic",
            "target_symbol": "_build_envelope_rejection",
            "target_file": "service.py",
            "grounded_symbol_source": 'details = {\n    "a": 1,\n}\n',
        },
    )
    assert steps[0]["tool"] == "read_range"
    assert steps[0]["step_idx"] == 1
    assert steps[1]["args"]["read_source"]["step_idx"] == 1
    assert steps[1]["args"]["read_source"]["file"] == "service.py"
    assert "planner_grounded_buffer" not in steps[1]


def test_rejects_incomplete_multiline_search_anchor() -> None:
    grounded = 'details = {\n    "a": 1,\n    "b": 2,\n}\n'
    with pytest.raises(PlannerBridgeError) as exc_info:
        _normalize_planner_steps(
            {
                "steps": [
                    {
                        "type": "edit_file",
                        "file": "a.py",
                        "search": '    "a": 1,\n    "b": 2,\n    ',
                        "replace": '    "a": 1,\n    "b": 2,\n    "c": 3,\n',
                        "read_source": {"step_idx": 0},
                    }
                ]
            },
            max_steps=4,
            grounded_source=grounded,
            planner_packet={
                "planner_mode": "generic",
                "target_symbol": "details",
                "target_file": "a.py",
                "grounded_symbol_source": grounded,
            },
        )
    assert "PLANNER_EDIT_INCOMPLETE_SEARCH_ANCHOR" in str(exc_info.value)


def test_validate_edit_grounding_rejects_non_unique_multiline_anchor() -> None:
    read_buffer = 'target = {\n    "a": 1,\n}\n\nother = {\n    "a": 1,\n}\n'
    edit_step = {
        "tool": "edit_file",
        "args": {
            "relative_path": "a.py",
            "search_replace_blocks": 'SEARCH\n    "a": 1,\nREPLACE\n    "a": 2,\n',
            "read_source": {"step_idx": 0},
        },
        "step_idx": 1,
    }
    prior_steps = [
        {
            "tool": "read_range",
            "args": {"relative_path": "a.py"},
            "step_idx": 0,
            "output_content": read_buffer,
        }
    ]

    with pytest.raises(PlannerBridgeError) as exc_info:
        validate_edit_grounding(edit_step, prior_steps, {"a.py": {"text": read_buffer, "step_idx": 0}})

    assert "PLANNER_EDIT_ANCHOR_NOT_UNIQUE" in str(exc_info.value)


def test_validate_edit_grounding_accepts_unique_exact_anchor() -> None:
    read_buffer = 'target = {\n    "a": 1,\n}\n'
    edit_step = {
        "tool": "edit_file",
        "args": {
            "relative_path": "a.py",
            "search_replace_blocks": 'SEARCH\ntarget = {\n    "a": 1,\n}\nREPLACE\ntarget = {\n    "a": 2,\n}\n',
            "read_source": {"step_idx": 0},
        },
        "step_idx": 1,
    }
    prior_steps = [
        {
            "tool": "read_range",
            "args": {"relative_path": "a.py"},
            "step_idx": 0,
            "output_content": read_buffer,
        }
    ]

    validate_edit_grounding(edit_step, prior_steps, {"a.py": {"text": read_buffer, "step_idx": 0}})


def test_validate_edit_grounding_rejects_cited_read_without_buffer() -> None:
    edit_step = {
        "tool": "edit_file",
        "args": {
            "relative_path": "a.py",
            "search_replace_blocks": "<<<<<<< SEARCH\nfoo()\n=======\nbar()\n>>>>>>> REPLACE",
            "read_source": {"step_idx": 0},
        },
        "step_idx": 1,
    }
    prior_steps = [
        {
            "tool": "read_range",
            "args": {"relative_path": "a.py"},
            "step_idx": 0,
        }
    ]

    with pytest.raises(PlannerBridgeError) as exc_info:
        validate_edit_grounding(edit_step, prior_steps, {"a.py": {"text": "", "step_idx": 0}})

    assert "PLANNER_EDIT_MISSING_READ" in str(exc_info.value)
    assert "cited_read_missing_buffer" in str(exc_info.value)


def test_normalize_planner_steps_normalizes_nested_semantic_edit_payloads() -> None:
    with pytest.raises(PlannerBridgeError) as exc_info:
        _normalize_planner_steps(
            {
                "steps": [
                    {
                        "tool": "edit_file",
                        "args": {
                            "relative_path": "scripts/wh.py",
                            "payload": {
                                "changes": [
                                    {
                                        "find_text": "old\n",
                                        "updated_text": "new\n",
                                    }
                                ]
                            },
                        },
                    }
                ]
            },
            max_steps=4,
        )
    assert "PLANNER_EDIT_MISSING_SEARCH" in str(exc_info.value)


def test_resolve_planner_base_url_uses_integration_default_when_runtime_url_unset(monkeypatch) -> None:
    from backend.services import devexecutor_planner_bridge_service as module

    previous_planner = module.settings.RUNTIME_PLANNER_API_URL
    previous_core = module.settings.CORE_API_URL
    module.settings.RUNTIME_PLANNER_API_URL = None
    module.settings.CORE_API_URL = "http://api-gateway:8000"
    try:
        resolved = _resolve_planner_base_url(workspace_root="C:/dev/hyeve")
    finally:
        module.settings.RUNTIME_PLANNER_API_URL = previous_planner
        module.settings.CORE_API_URL = previous_core

    assert resolved == "http://integration:8020"


def test_resolve_planner_base_url_prefers_explicit_override(monkeypatch) -> None:
    from backend.services import devexecutor_planner_bridge_service as module

    previous_planner = module.settings.RUNTIME_PLANNER_API_URL
    previous_core = module.settings.CORE_API_URL
    module.settings.RUNTIME_PLANNER_API_URL = "http://planner.local:8010/api/v1/"
    module.settings.CORE_API_URL = "http://api-gateway:8000"
    try:
        resolved = _resolve_planner_base_url(workspace_root="C:/dev/hyeve")
    finally:
        module.settings.RUNTIME_PLANNER_API_URL = previous_planner
        module.settings.CORE_API_URL = previous_core

    assert resolved == "http://planner.local:8010"


def test_planner_base_resolution_does_not_use_core_fallback_for_planner(monkeypatch) -> None:
    from backend.services import devexecutor_planner_bridge_service as module

    previous_planner = module.settings.RUNTIME_PLANNER_API_URL
    previous_core = module.settings.CORE_API_URL
    module.settings.RUNTIME_PLANNER_API_URL = None
    module.settings.CORE_API_URL = "http://api-gateway:8000"
    try:
        diagnostics = module._planner_base_resolution(
            workspace_root="C:/dev/hyeve",
            require_explicit_runtime_base=True,
        )
    finally:
        module.settings.RUNTIME_PLANNER_API_URL = previous_planner
        module.settings.CORE_API_URL = previous_core

    assert diagnostics["planner_base_url_resolved"] == "http://integration:8020"
    assert diagnostics["planner_base_override_set"] is False
    assert diagnostics["planner_base_fallback_used"] is True
    assert diagnostics["planner_base_fallback_source"] == "DEFAULT_RUNTIME_PLANNER_API_URL"
    assert diagnostics["guard_blocked"] is False


def test_plan_steps_via_llm_retries_timeout_then_succeeds(monkeypatch) -> None:
    from backend.services import devexecutor_planner_bridge_service as module

    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {"data": {"output_text": '{"steps":[{"tool":"get_status","args":{}}]}'}}

    class _Client:
        attempts = 0

        def __init__(self, *, timeout: int) -> None:
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def post(self, target_url: str, *, headers: dict[str, str], json: dict[str, object]):
            _Client.attempts += 1
            assert headers["Authorization"] == "Bearer token"
            assert json["provider"] == "slavx"
            assert target_url == "http://integration:8020/api/integrations/llm/chat"
            if _Client.attempts == 1:
                raise httpx.ReadTimeout("timed out")
            return _Response()

    monkeypatch.setattr(module.httpx, "Client", _Client)
    monkeypatch.setattr(module, "_ensure_managed_planner_service_ready", lambda **_kwargs: {"managed": True})
    monkeypatch.setattr(module, "_should_skip_socket_preflight", lambda **_kwargs: True)
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)
    previous_planner = module.settings.RUNTIME_PLANNER_API_URL
    previous_core = module.settings.CORE_API_URL
    previous_retries = module.settings.RUNTIME_PLANNER_MAX_RETRIES
    previous_backoff = module.settings.RUNTIME_PLANNER_RETRY_BACKOFF_MS
    previous_timeout = module.settings.RUNTIME_PLANNER_TIMEOUT_SECONDS
    module.settings.RUNTIME_PLANNER_API_URL = None
    module.settings.CORE_API_URL = "http://api-gateway:8000"
    module.settings.RUNTIME_PLANNER_MAX_RETRIES = 2
    module.settings.RUNTIME_PLANNER_RETRY_BACKOFF_MS = 1
    module.settings.RUNTIME_PLANNER_TIMEOUT_SECONDS = 7
    planner_packet = _valid_planner_packet()
    try:
        steps, diagnostics = plan_steps_via_llm(
            tenant_id="default",
            project_id="proj",
            correlation_id="corr",
            workspace_root="C:/dev/hyeve",
            prompt="do it",
            planner_packet=planner_packet,
            provider="slavx",
            authorization="Bearer token",
        )
    finally:
        module.settings.RUNTIME_PLANNER_API_URL = previous_planner
        module.settings.CORE_API_URL = previous_core
        module.settings.RUNTIME_PLANNER_MAX_RETRIES = previous_retries
        module.settings.RUNTIME_PLANNER_RETRY_BACKOFF_MS = previous_backoff
        module.settings.RUNTIME_PLANNER_TIMEOUT_SECONDS = previous_timeout

    assert steps == [{"tool": "get_status", "args": {}}]
    assert diagnostics["attempts"] == 2


def test_plan_steps_via_llm_skips_socket_preflight_and_uses_http(monkeypatch) -> None:
    from backend.services import devexecutor_planner_bridge_service as module

    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"data": {"output_text": '{"steps":[{"tool":"get_status","args":{}}]}'}}

    class _Client:
        def __init__(self, *, timeout: int) -> None:
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def post(self, _target_url: str, *, headers: dict[str, str], json: dict[str, Any]):
            _ = (headers, json)
            return _Response()

    monkeypatch.setattr(module.httpx, "Client", _Client)
    planner_packet = _valid_planner_packet()

    steps, diagnostics = plan_steps_via_llm(
        tenant_id="default",
        project_id="proj",
        correlation_id="corr",
        workspace_root="C:/dev/hyeve",
        prompt="do it",
        planner_packet=planner_packet,
        provider="slavx",
        authorization="Bearer token",
    )

    assert steps == [{"tool": "get_status", "args": {}}]
    assert diagnostics["socket_preflight_attempted"] is False
    assert diagnostics["socket_preflight_skipped"] is True
    assert diagnostics["socket_preflight_ok"] is False
    assert diagnostics["socket_preflight_error"] == "skipped_internal_planner_transport"
    assert diagnostics["request_build_ms"] >= 0.0
    assert len(diagnostics["transport_attempts"]) == 1
    assert diagnostics["transport_attempts"][0]["attempt"] == 1
    assert diagnostics["transport_attempts"][0]["preflight_phase"] == "skipped"
    assert diagnostics["transport_attempts"][0]["socket_preflight_ms"] == 0.0
    assert diagnostics["transport_attempts"][0]["http_request_ms"] >= 0.0
    assert diagnostics["transport_attempts"][0]["response_parse_ms"] >= 0.0
    assert diagnostics["transport_attempts"][0]["retry_sleep_ms"] == 0.0
    assert diagnostics["transport_attempts"][0]["failure_phase"] is None
    assert diagnostics["transport_attempts"][0]["transport_failure_code"] is None
    assert diagnostics["transport_phase_timings"]["request_build_ms"] == diagnostics["request_build_ms"]
    assert diagnostics["transport_phase_timings"]["http_request_ms_total"] >= 0.0
    assert diagnostics["transport_phase_timings"]["response_parse_ms_total"] >= 0.0


def test_plan_steps_via_llm_grants_one_fresh_connect_retry_for_stale_socket(monkeypatch) -> None:
    from backend.services import devexecutor_planner_bridge_service as module

    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"data": {"output_text": '{"steps":[{"tool":"get_status","args":{}}]}'}}

    class _Client:
        attempts = 0

        def __init__(self, *, timeout: int) -> None:
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def post(self, _target_url: str, *, headers: dict[str, str], json: dict[str, object]):
            _ = (headers, json)
            _Client.attempts += 1
            if _Client.attempts == 1:
                raise httpx.ConnectError("[Errno 115] Operation now in progress")
            return _Response()

    monkeypatch.setattr(module.httpx, "Client", _Client)
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)
    previous_retries = module.settings.RUNTIME_PLANNER_MAX_RETRIES
    module.settings.RUNTIME_PLANNER_MAX_RETRIES = 0
    planner_packet = _valid_planner_packet()
    try:
        steps, diagnostics = plan_steps_via_llm(
            tenant_id="default",
            project_id="proj",
            correlation_id="corr",
            workspace_root="C:/dev/hyeve",
            prompt="do it",
            planner_packet=planner_packet,
            provider="slavx",
            authorization="Bearer token",
        )
    finally:
        module.settings.RUNTIME_PLANNER_MAX_RETRIES = previous_retries

    assert steps == [{"tool": "get_status", "args": {}}]
    assert diagnostics["attempts"] == 2
    assert diagnostics["configured_retries_used"] == 0
    assert diagnostics["fresh_connect_retry_used"] is True
    assert diagnostics["socket_preflight_attempted"] is False
    assert diagnostics["socket_preflight_skipped"] is True
    assert diagnostics["socket_preflight_error"] == "skipped_internal_planner_transport"
    assert diagnostics["failure_phase"] is None
    assert len(diagnostics["transport_attempts"]) == 2
    assert diagnostics["transport_attempts"][0]["failure_phase"] == "connect"
    assert diagnostics["transport_attempts"][0]["transport_failure_code"] == "PLANNER_TRANSPORT_STALE_SOCKET"
    assert diagnostics["transport_attempts"][1]["failure_phase"] is None
    assert diagnostics["transport_phase_timings"]["retry_sleep_ms_total"] >= 0.0


def test_resolve_planner_timeout_seconds_ceil_subsecond_budget() -> None:
    from backend.services import devexecutor_planner_bridge_service as module

    assert module._resolve_planner_timeout_seconds(0.2) == 1
    assert module._resolve_planner_timeout_seconds(0.9) == 1
    assert module._resolve_planner_timeout_seconds(1.1) == 2


def test_plan_steps_via_llm_skips_socket_preflight_for_internal_planner_alias_on_linux(monkeypatch) -> None:
    from backend.services import devexecutor_planner_bridge_service as module

    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"data": {"output_text": '{"steps":[{"tool":"get_status","args":{}}]}'}}

    class _Client:
        def __init__(self, *, timeout: int) -> None:
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def post(self, _target_url: str, *, headers: dict[str, str], json: dict[str, object]):
            _ = (headers, json)
            return _Response()

    monkeypatch.setattr(module.httpx, "Client", _Client)
    previous_planner = module.settings.RUNTIME_PLANNER_API_URL
    previous_core = module.settings.CORE_API_URL
    module.settings.RUNTIME_PLANNER_API_URL = "http://api-gateway:8000"
    module.settings.CORE_API_URL = "http://api-gateway:8000"
    planner_packet = _valid_planner_packet()
    try:
        steps, diagnostics = plan_steps_via_llm(
            tenant_id="default",
            project_id="proj",
            correlation_id="corr",
            workspace_root="/srv/rouvr/proposals/task-244",
            prompt="do it",
            planner_packet=planner_packet,
            provider="slavx",
            authorization="Bearer token",
        )
    finally:
        module.settings.RUNTIME_PLANNER_API_URL = previous_planner
        module.settings.CORE_API_URL = previous_core

    assert steps == [{"tool": "get_status", "args": {}}]
    assert diagnostics["socket_preflight_attempted"] is False
    assert diagnostics["socket_preflight_skipped"] is True
    assert diagnostics["socket_preflight_error"] == "skipped_internal_planner_transport"


def test_plan_steps_via_llm_rejects_payloads_above_hard_cap(monkeypatch) -> None:
    from backend.services import devexecutor_planner_bridge_service as module

    monkeypatch.setattr(module, "PLANNER_HARD_PAYLOAD_CAP_BYTES", 256)
    planner_packet = _valid_planner_packet()

    with pytest.raises(module.PlannerBridgeError) as exc:
        plan_steps_via_llm(
            tenant_id="default",
            project_id="proj",
            correlation_id="corr",
            workspace_root="C:/dev/hyeve",
            prompt="x" * 5000,
            planner_packet=planner_packet,
            provider="slavx",
            authorization="Bearer token",
        )

    assert "MODEL_INGRESS_GATE_REJECTED" in str(exc.value)
    assert "PROMPT_TOKEN_CAP_EXCEEDED" in str(exc.value)


def test_build_planner_request_envelope_with_accounting_excludes_rich_packet_baggage() -> None:
    planner_packet = _valid_planner_packet()
    envelope, accounting = build_planner_request_envelope_with_accounting(
        prompt="do it",
        planner_packet=planner_packet,
        task_intent="code_change_required",
        max_steps=8,
        planner_attempt_number=1,
    )

    assert envelope["schema_name"] == "aix.utf_task.current"
    assert envelope["planner_packet"]["schema_name"] == "planner_slice.current"
    assert "implementation_hints" not in envelope["planner_packet"]
    assert "grounded_symbol_source" not in envelope["planner_packet"]
    assert (
        envelope["planner_packet"]["allowed_surface"]["anchor"]["compact_snippet"]
        == "if invalid_input:\n    return error_422()"
    )
    assert accounting["planner_packet_audit"]["forbidden_fields"] == []
    assert (
        accounting["planner_packet_component_bytes"]["allowed_surface.anchor"]
        < accounting["component_bytes"]["planner_packet"]
    )


def test_plan_steps_via_llm_reports_component_accounting_on_payload_cap_failure(monkeypatch) -> None:
    from backend.services import devexecutor_planner_bridge_service as module

    monkeypatch.setattr(module, "MUTATION_PLANNER_PACKET_CAP_BYTES", 512)
    planner_packet = _valid_planner_packet()
    planner_packet["allowed_surface"]["anchor"]["compact_snippet"] = "x" * 750

    with pytest.raises(module.PlannerBridgeError) as exc:
        plan_steps_via_llm(
            tenant_id="default",
            project_id="proj",
            correlation_id="corr",
            workspace_root="C:/dev/hyeve",
            prompt="do it",
            planner_packet=planner_packet,
            planner_mode="packet_bound_replace_function",
            provider="slavx",
            authorization="Bearer token",
        )

    assert "PLANNER_PAYLOAD_CAP_EXCEEDED" in str(exc.value)


def test_build_planner_request_envelope_with_accounting_rejects_forbidden_current_fields() -> None:
    planner_packet = _valid_planner_packet()
    planner_packet["grounded_symbol_source"] = "bad"

    with pytest.raises(ValueError):
        build_planner_request_envelope_with_accounting(
            prompt="do it",
            planner_packet=planner_packet,
            task_intent="code_change_required",
            max_steps=8,
            planner_attempt_number=1,
        )


def test_plan_steps_via_llm_rejects_builder_current_contamination() -> None:
    from backend.services import devexecutor_planner_bridge_service as module

    with pytest.raises(module.PlannerBridgeError) as exc:
        plan_steps_via_llm(
            tenant_id="default",
            project_id="proj",
            correlation_id="corr",
            workspace_root="C:/dev/hyeve",
            prompt="do it",
            planner_packet={"schema_name": "aix.planner.slice_packet.current", "task_number": 325},
            planner_mode="packet_bound_replace_function",
            provider="slavx",
            authorization="Bearer token",
        )

    assert "BUILDER_CURRENT_PACKET_REQUIRED" in str(exc.value)


def test_plan_steps_via_llm_emits_current_packet_audit_bytes(monkeypatch) -> None:
    from backend.services import devexecutor_planner_bridge_service as module

    captured: dict[str, Any] = {}

    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"data": {"output_text": '{"steps":[{"tool":"get_status","args":{}}]}'}}

    class _Client:
        def __init__(self, *, timeout: int) -> None:
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def post(self, _target_url: str, *, headers: dict[str, str], json: dict[str, Any]):
            _ = headers
            metadata_payload = json.get("metadata")
            captured["metadata"] = dict(metadata_payload) if isinstance(metadata_payload, dict) else {}
            return _Response()

    monkeypatch.setattr(module.httpx, "Client", _Client)
    planner_packet = _valid_planner_packet()

    plan_steps_via_llm(
        tenant_id="default",
        project_id="proj",
        correlation_id="corr",
        workspace_root="C:/dev/hyeve",
        prompt="do it",
        planner_packet=planner_packet,
        provider="slavx",
        authorization="Bearer token",
    )

    metadata = dict(captured["metadata"] or {})
    assert metadata["planner_packet_schema_name"] == "planner_slice.current"
    assert metadata["planner_packet_accounting"]["planner_packet_serialized_bytes"] <= 8192
    assert metadata["planner_packet_accounting"]["planner_packet_audit"]["forbidden_fields"] == []


def test_validate_plan_json_accepts_run_validation_command_step() -> None:
    from backend.services.devexecutor_planner_bridge_service import _planner_schema_validation_errors

    errors = _planner_schema_validation_errors(
        {
            "steps": [
                {"type": "read_range", "file": "a.py", "start_line": 1, "end_line": 2},
                {
                    "type": "edit_file",
                    "file": "a.py",
                    "search_range": {"start_line": 1, "end_line": 2},
                    "replace": "x = 1\n",
                    "read_source": {"step_idx": 1, "file": "a.py"},
                },
                {"type": "run_validation_command", "command": "python -m pytest tests/test_a.py -q --tb=short"},
            ]
        }
    )

    assert errors == []


def test_plan_steps_via_llm_prefers_explicit_authorization_over_runtime_planner_service_token(monkeypatch) -> None:
    from backend.services import devexecutor_planner_bridge_service as module

    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"data": {"output_text": '{"steps":[{"tool":"get_status","args":{}}]}'}}

    class _Client:
        def __init__(self, *, timeout: int) -> None:
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def post(self, _target_url: str, *, headers: dict[str, str], json: dict[str, object]):
            assert headers["Authorization"] == "Bearer caller-token"
            return _Response()

    monkeypatch.setattr(module.httpx, "Client", _Client)
    previous_token = module.settings.RUNTIME_PLANNER_AUTH_TOKEN
    module.settings.RUNTIME_PLANNER_AUTH_TOKEN = "service-token"
    planner_packet = _valid_planner_packet()
    try:
        steps, diagnostics = plan_steps_via_llm(
            tenant_id="default",
            project_id="proj",
            correlation_id="corr",
            workspace_root="C:/dev/hyeve",
            prompt="do it",
            planner_packet=planner_packet,
            provider="slavx",
            authorization="Bearer caller-token",
        )
    finally:
        module.settings.RUNTIME_PLANNER_AUTH_TOKEN = previous_token

    assert steps == [{"tool": "get_status", "args": {}}]
    assert diagnostics["planner_used"] is True


def test_plan_steps_via_llm_falls_back_to_runtime_planner_service_token(monkeypatch) -> None:
    from backend.services import devexecutor_planner_bridge_service as module

    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"data": {"output_text": '{"steps":[{"tool":"get_status","args":{}}]}'}}

    class _Client:
        def __init__(self, *, timeout: int) -> None:
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def post(self, _target_url: str, *, headers: dict[str, str], json: dict[str, object]):
            assert headers["Authorization"] == "Bearer service-token"
            return _Response()

    monkeypatch.setattr(module.httpx, "Client", _Client)
    previous_token = module.settings.RUNTIME_PLANNER_AUTH_TOKEN
    module.settings.RUNTIME_PLANNER_AUTH_TOKEN = "service-token"
    planner_packet = _valid_planner_packet()
    try:
        steps, diagnostics = plan_steps_via_llm(
            tenant_id="default",
            project_id="proj",
            correlation_id="corr",
            workspace_root="C:/dev/hyeve",
            prompt="do it",
            planner_packet=planner_packet,
            provider="slavx",
            authorization=None,
        )
    finally:
        module.settings.RUNTIME_PLANNER_AUTH_TOKEN = previous_token

    assert steps == [{"tool": "get_status", "args": {}}]
    assert diagnostics["planner_used"] is True


def test_request_planner_json_via_llm_prefers_explicit_authorization_over_runtime_planner_service_token(
    monkeypatch,
) -> None:
    from backend.services import devexecutor_planner_bridge_service as module

    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"data": {"output_text": '{"child_tasks":[{"title":"x"}]}'}}

    class _Client:
        def __init__(self, *, timeout: int) -> None:
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def post(self, _target_url: str, *, headers: dict[str, str], json: dict[str, object]):
            assert headers["Authorization"] == "Bearer caller-token"
            assert json["response_format"] == {"type": "json_object"}
            return _Response()

    monkeypatch.setattr(module.httpx, "Client", _Client)
    previous_token = module.settings.RUNTIME_PLANNER_AUTH_TOKEN
    module.settings.RUNTIME_PLANNER_AUTH_TOKEN = "service-token"
    try:
        payload, diagnostics = request_planner_json_via_llm(
            tenant_id="default",
            project_id="proj",
            correlation_id="corr",
            workspace_root="C:/dev/hyeve",
            system_prompt="Return strict JSON only.",
            user_prompt="emit child tasks",
            provider="slavx",
            authorization="Bearer caller-token",
        )
    finally:
        module.settings.RUNTIME_PLANNER_AUTH_TOKEN = previous_token

    assert payload == {"child_tasks": [{"title": "x"}]}
    assert diagnostics["provider"] == "slavx"


def test_request_planner_json_via_llm_falls_back_to_runtime_planner_service_token(monkeypatch) -> None:
    from backend.services import devexecutor_planner_bridge_service as module

    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"data": {"output_text": '{"child_tasks":[{"title":"x"}]}'}}

    class _Client:
        def __init__(self, *, timeout: int) -> None:
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def post(self, _target_url: str, *, headers: dict[str, str], json: dict[str, object]):
            assert headers["Authorization"] == "Bearer service-token"
            assert json["response_format"] == {"type": "json_object"}
            return _Response()

    monkeypatch.setattr(module.httpx, "Client", _Client)
    previous_token = module.settings.RUNTIME_PLANNER_AUTH_TOKEN
    module.settings.RUNTIME_PLANNER_AUTH_TOKEN = "service-token"
    try:
        payload, diagnostics = request_planner_json_via_llm(
            tenant_id="default",
            project_id="proj",
            correlation_id="corr",
            workspace_root="C:/dev/hyeve",
            system_prompt="Return strict JSON only.",
            user_prompt="emit child tasks",
            provider="slavx",
            authorization=None,
        )
    finally:
        module.settings.RUNTIME_PLANNER_AUTH_TOKEN = previous_token

    assert payload == {"child_tasks": [{"title": "x"}]}
    assert diagnostics["provider"] == "slavx"


def test_request_planner_json_via_llm_can_emit_verifier_ingress(monkeypatch) -> None:
    from backend.services import devexecutor_planner_bridge_service as module

    captured: dict[str, object] = {}

    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"data": {"output_text": '{"failure_self_report":{"why_plan_failed":"missing anchor"}}'}}

    class _Client:
        def __init__(self, *, timeout: int) -> None:
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def post(self, _target_url: str, *, headers: dict[str, str], json: dict[str, object]):
            captured["body"] = json
            return _Response()

    monkeypatch.setattr(module.httpx, "Client", _Client)

    payload, diagnostics = request_planner_json_via_llm(
        tenant_id="default",
        project_id="proj",
        correlation_id="corr-verifier",
        workspace_root="C:/dev/hyeve",
        system_prompt="Return strict JSON only.",
        user_prompt="emit verifier self report",
        provider="slavx",
        authorization="Bearer caller-token",
        ingress_kind="verifier",
        task_type="verifier_failure_self_report",
        actions=["return_failure_self_report_json"],
        params_override={
            "project_id": "proj",
            "validation_summary": {"failed": True, "code": "ANCHOR_MISSING"},
            "evidence_refs": ["aix://planner/corr-verifier/validation_error"],
            "outcome_request": "failure_self_report",
        },
        context_override={"verifier_surface": "failure_self_report"},
    )

    body = cast(dict[str, Any], captured["body"])
    assert payload["failure_self_report"]["why_plan_failed"] == "missing anchor"
    assert body["metadata"]["ingress_schema"] == "aix.verifier_ingress.current"
    assert body["metadata"]["planner_request_shape"] == "aix.verifier_ingress.current"
    user_payload = json.loads(body["messages"][1]["content"])
    assert user_payload["schema_name"] == "aix.verifier_ingress.current"
    assert user_payload["utf_task"]["task_type"] == "verifier_failure_self_report"
    assert diagnostics["model_ingress_schema"] == "aix.verifier_ingress.current"
    assert diagnostics["prompt_token_breakdown"]["estimated_prompt_tokens"] < 1200


def test_plan_steps_via_llm_wraps_planner_packet_in_canonical_request_envelope(monkeypatch) -> None:
    from backend.services import devexecutor_planner_bridge_service as module

    captured: dict[str, object] = {}

    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "data": {
                    "output_text": json.dumps(
                        {
                            "steps": [
                                {
                                    "type": "read_range",
                                    "file": "backend/routers/x.py",
                                    "output_content": "def handler() -> None:\n    return None\n",
                                },
                                {
                                    "type": "edit_file",
                                    "file": "backend/routers/x.py",
                                    "search": "return None\n",
                                    "replace": "return 422\n",
                                    "read_source": {"step_idx": 0, "file": "backend/routers/x.py"},
                                },
                            ]
                        }
                    )
                }
            }

    class _Client:
        def __init__(self, *, timeout: int) -> None:
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def post(self, _target_url: str, *, headers: dict[str, str], json: dict[str, object]):
            captured["body"] = json
            return _Response()

    monkeypatch.setattr(module.httpx, "Client", _Client)

    planner_packet = _valid_planner_packet()
    steps, diagnostics = plan_steps_via_llm(
        tenant_id="default",
        project_id="proj",
        correlation_id="corr",
        workspace_root="C:/dev/hyeve",
        prompt="do it",
        planner_packet=planner_packet,
        provider="slavx",
        authorization="Bearer token",
    )

    assert [step["tool"] for step in steps] == ["read_range", "edit_file", "cmd.run"]
    assert diagnostics["planner_used"] is True
    assert diagnostics["planner_packet_forwarded"] is True
    assert diagnostics["planner_request_shape"] == "generic_prompt"
    assert diagnostics["planner_prompt_authority"] == "tool_contract_registry"
    assert isinstance(diagnostics["contract_fingerprint"], str)
    assert diagnostics["planner_user_packet_authority"] == "raw_user_prompt"
    assert diagnostics["planner_secondary_prompt_injected"] is False
    body = cast(dict[str, Any], captured["body"])
    assert body["metadata"]["planner_request_shape"] == "generic_prompt"
    assert body["metadata"]["model_ingress_request_id"]
    assert body["metadata"]["model_ingress_fingerprint"].startswith("sha256:")
    assert body["metadata"]["model_ingress_schema_validation"] == "passed"
    assert "model_ingress_artifact" not in body["metadata"]
    artifact_refs = body["metadata"]["model_ingress_artifact_refs"]
    assert artifact_refs["schema"] == "aix.model_ingress_presend_artifacts.current"
    assert Path(artifact_refs["request_body_ref"]).exists()
    assert diagnostics["model_ingress_artifact_refs"]["request_body_ref"] == artifact_refs["request_body_ref"]
    assert diagnostics["prompt_token_breakdown"]["estimated_prompt_tokens"] < 1200
    assert body["metadata"]["planner_packet_fingerprint"] == diagnostics["planner_packet_fingerprint"]
    assert body["metadata"]["contract_fingerprint"] == diagnostics["contract_fingerprint"]
    assert body["max_tokens"] == 4096
    assert body["timeout"] == diagnostics["timeout_seconds"]
    assert "planner_packet" not in body["metadata"]
    outbound = _compiled_planner_user_payload(body)
    assert outbound["schema_name"] == "aix.planner_ingress.current"
    assert outbound["utf_task"]["schema_name"] == "aix.utf_task.current"
    assert outbound["utf_task"]["params"]["planner_user_request"] == "do it"
    assert diagnostics["planner_user_packet_bytes"] == len(b"do it")
    assert diagnostics["planner_packet_accounting"]["planner_packet_serialized_bytes"] > 0
    assert diagnostics["planner_user_packet_bytes"] < module.MUTATION_PLANNER_PACKET_CAP_BYTES


def test_plan_steps_via_llm_planning_safe_anchor_window_seam_emits_read_then_edit_and_executes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from backend.services import devexecutor_planner_bridge_service as planner_module

    workspace_root = tmp_path / "workspace"
    target = workspace_root / "service.py"
    workspace_root.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "def _build_envelope_rejection():\n"
        "    details = {\n"
        '        "rejection_code": "OLD",\n'
        "    }\n"
        "    return details\n",
        encoding="utf-8",
    )
    planner_packet = _valid_planner_packet()
    planner_packet["target_file"] = "service.py"
    planner_packet["target_symbol"] = "_build_envelope_rejection"
    planner_packet["allowed_surface"] = {
        "anchor": {
            "symbol": "_build_envelope_rejection",
            "compact_snippet": (
                "def _build_envelope_rejection():\n"
                "    details = {\n"
                '        "rejection_code": "OLD",\n'
                "    }\n"
                "    return details"
            ),
        }
    }
    _PlannerSeamHandler.requests = []
    _PlannerSeamHandler.response_payload = {
        "data": {
            "output_text": json.dumps(
                {
                    "steps": [
                        {
                            "type": "read_range",
                            "file": "service.py",
                            "start_line": 1,
                            "end_line": 5,
                        },
                        {
                            "type": "edit_file",
                            "file": "service.py",
                            "grounding_source": "read_range",
                            "read_step_idx": 0,
                            "edit_range_within_read": {"start_line": 1, "end_line": 5},
                            "replacement_text": (
                                "def _build_envelope_rejection():\n"
                                "    details = {\n"
                                '        "rejection_code": "ENVELOPE_FORMAT_INVALID",\n'
                                "    }\n"
                                "    return details\n"
                            ),
                        },
                    ]
                }
            )
        }
    }
    port = _find_free_tcp_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), _PlannerSeamHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    previous_planner = planner_module.settings.RUNTIME_PLANNER_API_URL
    previous_core = planner_module.settings.CORE_API_URL
    planner_module.settings.RUNTIME_PLANNER_API_URL = f"http://127.0.0.1:{port}"
    planner_module.settings.CORE_API_URL = f"http://127.0.0.1:{port}"
    thread.start()
    try:
        steps, _diagnostics = plan_steps_via_llm(
            tenant_id="default",
            project_id="proj",
            correlation_id="corr-anchor-window",
            workspace_root=str(workspace_root),
            prompt="Patch the anchored rejection helper.",
            planner_packet=planner_packet,
            provider="slavx",
            authorization="Bearer token",
        )
    finally:
        planner_module.settings.RUNTIME_PLANNER_API_URL = previous_planner
        planner_module.settings.CORE_API_URL = previous_core
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert [step["tool"] for step in steps] == ["read_range", "edit_file", "cmd.run"]
    request = _PlannerSeamHandler.requests[-1]["body"]
    assert isinstance(request, dict)
    system_prompt = str(request["messages"][0]["content"])
    assert "first executable step MUST be" in system_prompt
    assert "grounded structural patch" in system_prompt
    assert "final step of every real mutation plan must be run_validation_command" in system_prompt
    outbound = _compiled_planner_user_payload(request)
    assert outbound["schema_name"] == "aix.planner_ingress.current"
    assert outbound["utf_task"]["params"]["planner_user_request"] == "Patch the anchored rejection helper."
    assert steps[1]["args"]["grounding_source"] == "read_range"
    assert steps[1]["args"]["read_step_idx"] == 0
    assert steps[1]["args"]["edit_range_within_read"] == {"start_line": 1, "end_line": 5}
    assert "ENVELOPE_FORMAT_INVALID" in steps[1]["args"]["replacement_text"]
    assert steps[2]["tool"] == "cmd.run"
    assert steps[2]["args"]["command_text"] == planner_packet["validation_command"]
    assert steps[2]["validation_contract_validation"]["status"] == "hydrated"

    def fake_run_command(**kwargs):
        command = [str(item) for item in kwargs["command"]]
        return {
            "command": command,
            "returncode": 0,
            "timed_out": False,
            "stdout": "1 PASSED\n",
            "stderr": "",
        }

    monkeypatch.setattr("backend.services.devexecutor_tool_loop_service.run_command", fake_run_command)

    transcript = execute_tool_loop(
        workspace_root=workspace_root,
        steps=steps,
        allow_prefixes=[["python"], ["git"], ["pytest"]],
    )
    assert transcript[0]["tool"] == "read_range"
    assert transcript[1]["tool"] == "edit_file"
    assert transcript[-1]["tool"] == "cmd.run"
    assert all(entry["ok"] for entry in transcript)
    assert transcript[1]["result"]["contract_validation"]["status"] == "passed"
    assert transcript[1]["result"]["contract_validation"]["mode"] == "live_file_read_range_within_read"
    assert transcript[1]["result"]["contract_validation"]["declared_validation"]["status"] == "pending"
    assert transcript[-1]["result"]["contract_validation"]["status"] == "passed"
    assert "ENVELOPE_FORMAT_INVALID" in target.read_text(encoding="utf-8")


def test_normalize_planner_steps_accepts_runtime_read_range_without_output_content() -> None:
    steps = _normalize_planner_steps(
        {
            "steps": [
                {
                    "type": "read_range",
                    "file": "service.py",
                    "start_line": 1,
                    "end_line": 5,
                }
            ]
        },
        max_steps=4,
    )
    assert steps[0]["tool"] == "read_range"
    assert steps[0]["args"] == {"relative_path": "service.py", "start_line": 1, "end_line": 5}
    assert "output_content" not in steps[0]


def test_normalize_planner_steps_rejects_read_range_placeholder_output_content() -> None:
    with pytest.raises(PlannerBridgeError, match="PLANNER_INVALID_GROUNDED_READ"):
        _normalize_planner_steps(
            {
                "steps": [
                    {
                        "type": "read_range",
                        "file": "a.py",
                        "start_line": 1,
                        "end_line": 3,
                        "output_content": "placeholder",
                    }
                ]
            },
            max_steps=4,
        )


def test_plan_steps_via_llm_planning_safe_anchor_window_rejects_edit_search_not_from_anchor_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from backend.services import devexecutor_planner_bridge_service as planner_module

    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(parents=True, exist_ok=True)
    planner_packet = _valid_planner_packet()
    planner_packet["target_file"] = "service.py"
    planner_packet["target_symbol"] = "_build_envelope_rejection"
    planner_packet["allowed_surface"]["anchor"] = {
        "symbol": "_build_envelope_rejection",
        "compact_snippet": (
            "def _build_envelope_rejection():\n"
            "    details = {\n"
            '        "rejection_code": "OLD",\n'
            "    }\n"
            "    return details"
        ),
    }
    planner_packet["validation_command"] = "python -m pytest tests/test_service.py -q --tb=short"
    _PlannerSeamHandler.requests = []
    _PlannerSeamHandler.response_payload = {
        "data": {
            "output_text": json.dumps(
                {
                    "steps": [
                        {
                            "type": "read_range",
                            "file": "service.py",
                            "start_line": 1,
                            "end_line": 5,
                        },
                        {
                            "type": "edit_file",
                            "file": "service.py",
                            "search": "totally missing search block\n",
                            "replace": "totally missing replacement\n",
                            "read_source": {"step_idx": 1, "file": "service.py"},
                        },
                    ]
                }
            )
        }
    }
    port = _find_free_tcp_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), _PlannerSeamHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    previous_planner = planner_module.settings.RUNTIME_PLANNER_API_URL
    previous_core = planner_module.settings.CORE_API_URL
    planner_module.settings.RUNTIME_PLANNER_API_URL = f"http://127.0.0.1:{port}"
    planner_module.settings.CORE_API_URL = f"http://127.0.0.1:{port}"
    thread.start()
    try:
        with pytest.raises(PlannerBridgeError, match="PLANNER_EDIT_CROSS_STEP_LINKAGE_FORBIDDEN"):
            plan_steps_via_llm(
                tenant_id="default",
                project_id="proj",
                correlation_id="corr-anchor-window-invalid-search",
                workspace_root=str(workspace_root),
                prompt="Patch the anchored rejection helper.",
                planner_packet=planner_packet,
                provider="slavx",
                authorization="Bearer token",
            )
    finally:
        planner_module.settings.RUNTIME_PLANNER_API_URL = previous_planner
        planner_module.settings.CORE_API_URL = previous_core
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_plan_steps_via_llm_planning_safe_anchor_window_appends_declared_cmd_run_when_planner_omits_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from backend.services import devexecutor_planner_bridge_service as planner_module

    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(parents=True, exist_ok=True)
    planner_packet = _valid_planner_packet()
    planner_packet["target_file"] = "service.py"
    planner_packet["allowed_surface"]["anchor"] = {"symbol": "f", "compact_snippet": "def f():\n    pass"}
    planner_packet["validation_command"] = "python -m pytest tests/test_service.py -q --tb=short"
    _PlannerSeamHandler.requests = []
    _PlannerSeamHandler.response_payload = {
        "data": {
            "output_text": json.dumps(
                {
                    "steps": [
                        {"type": "read_range", "file": "service.py", "start_line": 1, "end_line": 2},
                        {
                            "type": "edit_file",
                            "file": "service.py",
                            "grounding_source": "read_range",
                            "read_step_idx": 0,
                            "edit_range_within_read": {"start_line": 1, "end_line": 2},
                            "replacement_text": "def f():\n    return 1\n",
                        },
                    ]
                }
            )
        }
    }
    port = _find_free_tcp_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), _PlannerSeamHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    previous_planner = planner_module.settings.RUNTIME_PLANNER_API_URL
    previous_core = planner_module.settings.CORE_API_URL
    planner_module.settings.RUNTIME_PLANNER_API_URL = f"http://127.0.0.1:{port}"
    planner_module.settings.CORE_API_URL = f"http://127.0.0.1:{port}"
    thread.start()
    try:
        steps, _diagnostics = plan_steps_via_llm(
            tenant_id="default",
            project_id="proj",
            correlation_id="corr-anchor-window-cmd",
            workspace_root=str(workspace_root),
            prompt="Patch the anchored helper and validate it.",
            planner_packet=planner_packet,
            provider="slavx",
            authorization="Bearer token",
        )
    finally:
        planner_module.settings.RUNTIME_PLANNER_API_URL = previous_planner
        planner_module.settings.CORE_API_URL = previous_core
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert [step["tool"] for step in steps] == ["read_range", "edit_file", "cmd.run"]
    assert steps[-1]["args"]["command_text"] == planner_packet["validation_command"]


def test_plan_steps_via_llm_repairs_anchor_window_json_retry_by_omitting_output_content(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from backend.services import devexecutor_planner_bridge_service as planner_module

    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(parents=True, exist_ok=True)
    (workspace_root / "service.py").write_text("def f():\n    pass\n", encoding="utf-8")
    planner_packet = _valid_planner_packet()
    planner_packet["target_file"] = "service.py"
    planner_packet["allowed_surface"]["anchor"] = {"symbol": "f", "compact_snippet": "def f():\n    pass"}
    planner_packet["validation_command"] = "python -m pytest tests/test_service.py -q --tb=short"
    _PlannerSeamHandler.requests = []
    valid_retry_payload: dict[str, object] = {
        "data": {
            "output_text": json.dumps(
                {
                    "steps": [
                        {
                            "type": "read_range",
                            "file": "service.py",
                            "start_line": 1,
                            "end_line": 2,
                        },
                        {
                            "type": "edit_file",
                            "file": "service.py",
                            "grounding_source": "read_range",
                            "read_step_idx": 0,
                            "edit_range_within_read": {"start_line": 1, "end_line": 2},
                            "replacement_text": "def f():\n    return 1\n",
                        },
                    ]
                }
            )
        }
    }
    _PlannerSeamHandler.response_sequence = [
        {
            "data": {
                "output_text": (
                    "{\n"
                    '  "steps": [\n'
                    "    {\n"
                    '      "type": "read_range",\n'
                    '      "file": "service.py",\n'
                    '      "start_line": 1,\n'
                    '      "end_line": 2,\n'
                    '      "output_content": "d'
                )
            }
        },
        valid_retry_payload,
    ]
    _PlannerSeamHandler.response_payload = valid_retry_payload
    port = _find_free_tcp_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), _PlannerSeamHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    previous_planner = planner_module.settings.RUNTIME_PLANNER_API_URL
    previous_core = planner_module.settings.CORE_API_URL
    planner_module.settings.RUNTIME_PLANNER_API_URL = f"http://127.0.0.1:{port}"
    planner_module.settings.CORE_API_URL = f"http://127.0.0.1:{port}"
    thread.start()
    try:
        steps, _diagnostics = plan_steps_via_llm(
            tenant_id="default",
            project_id="proj",
            correlation_id="corr-anchor-window-json-retry",
            workspace_root=str(workspace_root),
            prompt="Patch the anchored helper and validate it.",
            planner_packet=planner_packet,
            provider="slavx",
            authorization="Bearer token",
        )
    finally:
        planner_module.settings.RUNTIME_PLANNER_API_URL = previous_planner
        planner_module.settings.CORE_API_URL = previous_core
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        _PlannerSeamHandler.response_sequence = []

    assert [step["tool"] for step in steps] == ["read_range", "edit_file", "cmd.run"]
    assert len(_PlannerSeamHandler.requests) >= 2
    retry_body = cast(dict[str, Any], _PlannerSeamHandler.requests[1]["body"])
    retry_outbound = _compiled_planner_user_payload(retry_body)
    assert retry_outbound["schema_name"] == "aix.repair_ingress.current"
    assert retry_outbound["utf_task"]["task_type"] == "devexecutor_repair_request"
    retry_context = retry_outbound["utf_task"]["params"]["repair_context"]
    assert "DO NOT inline output_content" in str(retry_context)
    assert '"completion_tokens": 500' not in str(retry_context)
    assert '"completion_state": "incomplete_json_object"' in str(retry_context)
    assert "regenerate the full JSON object from scratch" in str(retry_context)
    assert "Do NOT emit symbol, context_lines, step_idx, or output_content" in str(retry_context)


def test_plan_steps_via_llm_repairs_semantic_noop_mutation_then_succeeds(tmp_path: Path) -> None:
    from backend.services import devexecutor_planner_bridge_service as planner_module

    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(parents=True, exist_ok=True)
    (workspace_root / "service.py").write_text("def f():\n    pass\n", encoding="utf-8")
    planner_packet = _valid_planner_packet()
    planner_packet["target_file"] = "service.py"
    planner_packet["allowed_surface"]["anchor"] = {"symbol": "f", "compact_snippet": "def f():\n    pass"}
    planner_packet["validation_command"] = "python -m pytest tests/test_service.py -q --tb=short"
    _PlannerSeamHandler.requests = []
    valid_retry_payload: dict[str, object] = {
        "data": {
            "output_text": json.dumps(
                {
                    "steps": [
                        {
                            "type": "read_range",
                            "file": "service.py",
                            "start_line": 1,
                            "end_line": 2,
                        },
                        {
                            "type": "edit_file",
                            "file": "service.py",
                            "grounding_source": "read_range",
                            "read_step_idx": 0,
                            "edit_range_within_read": {"start_line": 1, "end_line": 2},
                            "replacement_text": "def f():\n    return 1\n",
                        },
                    ]
                }
            )
        }
    }
    _PlannerSeamHandler.response_sequence = [
        {
            "data": {
                "output_text": json.dumps(
                    {
                        "steps": [
                            {
                                "type": "read_range",
                                "file": "service.py",
                                "start_line": 1,
                                "end_line": 2,
                            },
                            {
                                "type": "edit_file",
                                "file": "service.py",
                                "grounding_source": "read_range",
                                "read_step_idx": 0,
                                "edit_range_within_read": {"start_line": 1, "end_line": 2},
                                "replacement_text": "def f():\n    pass\n",
                            },
                        ]
                    }
                )
            }
        },
        valid_retry_payload,
    ]
    _PlannerSeamHandler.response_payload = valid_retry_payload
    port = _find_free_tcp_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), _PlannerSeamHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    previous_planner = planner_module.settings.RUNTIME_PLANNER_API_URL
    previous_core = planner_module.settings.CORE_API_URL
    planner_module.settings.RUNTIME_PLANNER_API_URL = f"http://127.0.0.1:{port}"
    planner_module.settings.CORE_API_URL = f"http://127.0.0.1:{port}"
    thread.start()
    try:
        steps, diagnostics = plan_steps_via_llm(
            tenant_id="default",
            project_id="proj",
            correlation_id="corr-semantic-repair",
            workspace_root=str(workspace_root),
            prompt="Patch the anchored helper and validate it.",
            planner_packet=planner_packet,
            provider="slavx",
            authorization="Bearer token",
        )
    finally:
        planner_module.settings.RUNTIME_PLANNER_API_URL = previous_planner
        planner_module.settings.CORE_API_URL = previous_core
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        _PlannerSeamHandler.response_sequence = []

    assert [step["tool"] for step in steps] == ["read_range", "edit_file", "cmd.run"]
    assert diagnostics["semantic_repair_attempts_used"] == 1
    assert len(_PlannerSeamHandler.requests) >= 2
    retry_body = cast(dict[str, Any], _PlannerSeamHandler.requests[1]["body"])
    retry_messages = cast(list[dict[str, Any]], retry_body["messages"])
    assert "failed mutation semantics" in str(retry_messages[-1]["content"])
    assert "failure_analysis" in str(retry_messages[-1]["content"])


def test_plan_steps_via_llm_reports_completion_evidence_on_truncated_json_failure(tmp_path: Path) -> None:
    from backend.services import devexecutor_planner_bridge_service as planner_module

    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(parents=True, exist_ok=True)
    (workspace_root / "service.py").write_text("def f():\n    pass\n", encoding="utf-8")
    planner_packet = _valid_planner_packet()
    planner_packet["target_file"] = "service.py"
    planner_packet["allowed_surface"]["anchor"] = {"symbol": "f", "compact_snippet": "def f():\n    pass"}
    planner_packet["validation_command"] = "python -m pytest tests/test_service.py -q --tb=short"
    truncated_payload: dict[str, object] = {
        "data": {
            "output_text": (
                "{\n"
                '  "steps": [\n'
                "    {\n"
                '      "type": "read_range",\n'
                '      "file": "service.py",\n'
                '      "start_line": 1,\n'
                '      "end_line": 2\n'
                "    },\n"
            )
        }
    }
    _PlannerSeamHandler.requests = []
    _PlannerSeamHandler.response_sequence = [truncated_payload, truncated_payload, truncated_payload]
    _PlannerSeamHandler.response_payload = truncated_payload
    port = _find_free_tcp_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), _PlannerSeamHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    previous_planner = planner_module.settings.RUNTIME_PLANNER_API_URL
    previous_core = planner_module.settings.CORE_API_URL
    planner_module.settings.RUNTIME_PLANNER_API_URL = f"http://127.0.0.1:{port}"
    planner_module.settings.CORE_API_URL = f"http://127.0.0.1:{port}"
    thread.start()
    try:
        with pytest.raises(PlannerBridgeError, match="PLANNER_JSON_INVALID") as exc_info:
            plan_steps_via_llm(
                tenant_id="default",
                project_id="proj",
                correlation_id="corr-anchor-window-json-fail",
                workspace_root=str(workspace_root),
                prompt="Patch the anchored helper and validate it.",
                planner_packet=planner_packet,
                provider="slavx",
                authorization="Bearer token",
            )
    finally:
        planner_module.settings.RUNTIME_PLANNER_API_URL = previous_planner
        planner_module.settings.CORE_API_URL = previous_core
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        _PlannerSeamHandler.response_sequence = []

    detail = str(exc_info.value)
    assert "completion_evidence" in detail
    assert '"completion_state": "incomplete_json_object"' in detail
    assert '"first_top_level_fields": ["steps"]' in detail


def test_plan_steps_via_llm_semantic_noop_retry_exhaustion_raises(tmp_path: Path) -> None:
    from backend.services import devexecutor_planner_bridge_service as planner_module

    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(parents=True, exist_ok=True)
    planner_packet = _valid_planner_packet()
    planner_packet["target_file"] = "service.py"
    planner_packet["allowed_surface"]["anchor"] = {"symbol": "f", "compact_snippet": "def f():\n    pass"}
    planner_packet["validation_command"] = "python -m pytest tests/test_service.py -q --tb=short"
    _PlannerSeamHandler.requests = []
    semantic_noop_payload: dict[str, object] = {
        "data": {
            "output_text": json.dumps(
                {
                    "steps": [
                        {
                            "type": "read_range",
                            "file": "service.py",
                            "start_line": 1,
                            "end_line": 2,
                        },
                        {
                            "type": "edit_file",
                            "file": "service.py",
                            "grounding_source": "read_range",
                            "read_step_idx": 0,
                            "edit_range_within_read": {"start_line": 1, "end_line": 2},
                            "replacement_text": "def f():\n    pass\n",
                        },
                    ]
                }
            )
        }
    }
    _PlannerSeamHandler.response_sequence = [semantic_noop_payload, semantic_noop_payload]
    _PlannerSeamHandler.response_payload = semantic_noop_payload
    port = _find_free_tcp_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), _PlannerSeamHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    previous_planner = planner_module.settings.RUNTIME_PLANNER_API_URL
    previous_core = planner_module.settings.CORE_API_URL
    planner_module.settings.RUNTIME_PLANNER_API_URL = f"http://127.0.0.1:{port}"
    planner_module.settings.CORE_API_URL = f"http://127.0.0.1:{port}"
    thread.start()
    try:
        with pytest.raises(PlannerBridgeError, match="MUTATION_REQUIRED_BUT_ABSENT"):
            plan_steps_via_llm(
                tenant_id="default",
                project_id="proj",
                correlation_id="corr-semantic-repair-exhausted",
                workspace_root=str(workspace_root),
                prompt="Patch the anchored helper and validate it.",
                planner_packet=planner_packet,
                provider="slavx",
                authorization="Bearer token",
            )
    finally:
        planner_module.settings.RUNTIME_PLANNER_API_URL = previous_planner
        planner_module.settings.CORE_API_URL = previous_core
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        _PlannerSeamHandler.response_sequence = []

    assert len(_PlannerSeamHandler.requests) == 2


def test_render_verbatim_search_replace_block_preserves_terminal_replace_delimiter() -> None:
    block = planner_bridge._render_verbatim_search_replace_block("line1\n", "line1\nline2")

    assert block.endswith("\n>>>>>>> REPLACE")
    assert planner_bridge._extract_edit_file_blocks(block, depth=0) == [("line1", "line1\nline2")]


def test_hydrate_edit_step_from_search_range_renders_parseable_multiline_replace_without_terminal_newline() -> None:
    read_output = (
        "            likely_tests: list[str] = []\n"
        "            seen_test_paths: set[str] = set()\n"
        "            adjacent_files: list[str] = []\n"
    )
    edit_step = {
        "tool": "edit_file",
        "step_idx": 2,
        "args": {
            "relative_path": "service.py",
            "search_range": {"start_line": 1, "end_line": 1},
            "replace_text": (
                "            likely_tests: list[str] = []\n"
                "            seen_test_paths: set[str] = set()\n"
                "            adjacent_files: list[str] = []"
            ),
            "read_source": {"step_idx": 1, "file": "service.py"},
        },
    }
    prior_steps = [
        {
            "tool": "read_range",
            "step_idx": 1,
            "file": "service.py",
            "output_content": read_output,
            "args": {
                "relative_path": "service.py",
                "start_line": 1,
                "end_line": 3,
                "output_content": read_output,
            },
        }
    ]

    hydrated = planner_bridge._hydrate_edit_step_from_search_range(
        edit_step,
        prior_steps=prior_steps,
        planner_packet=None,
    )

    assert planner_bridge._extract_edit_file_blocks(hydrated["args"]["search_replace_blocks"], depth=0) == [
        (
            "            likely_tests: list[str] = []",
            (
                "            likely_tests: list[str] = []\n"
                "            seen_test_paths: set[str] = set()\n"
                "            adjacent_files: list[str] = []"
            ),
        )
    ]


def test_hydrate_anchor_window_edit_from_read_output_keeps_bounded_subrange_edit() -> None:
    anchor_output = (
        "async def answer_context_pack(\n"
        "    likely_tests: list[str] = []\n"
        "    seen_test_paths: set[str] = set()\n"
        ")\n"
    )
    edit_step = {
        "tool": "edit_file",
        "step_idx": 2,
        "args": {
            "relative_path": "service.py",
            "search_replace_blocks": planner_bridge._render_verbatim_search_replace_block(
                "    likely_tests: list[str] = []\n",
                "    likely_tests: list[str] = []\n    seen_test_paths.add('x')",
            ),
            "read_source": {"step_idx": 1, "file": "service.py"},
        },
    }
    prior_steps = [
        {
            "tool": "read_range",
            "step_idx": 1,
            "file": "service.py",
            "output_content": anchor_output,
            "args": {
                "relative_path": "service.py",
                "start_line": 10,
                "end_line": 13,
                "output_content": anchor_output,
            },
        }
    ]
    planner_packet = _valid_planner_packet()
    planner_packet["target_file"] = "service.py"
    planner_packet["allowed_surface"]["anchor"] = {"symbol": None, "compact_snippet": anchor_output.rstrip("\n")}

    hydrated = planner_bridge._hydrate_anchor_window_edit_from_read_output(
        edit_step,
        prior_steps=prior_steps,
        planner_packet=planner_packet,
    )

    assert hydrated == edit_step
    assert "planner_anchor_window" not in hydrated


def test_plan_steps_via_llm_allows_legacy_raw_edit_without_read_source_when_prior_read_exists(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from backend.services import devexecutor_planner_bridge_service as planner_module

    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(parents=True, exist_ok=True)
    planner_packet = _valid_planner_packet()
    planner_packet["target_file"] = "service.py"
    planner_packet["allowed_surface"]["anchor"] = {"symbol": "f", "compact_snippet": "def f():\n    pass"}
    planner_packet["validation_command"] = "python -m pytest tests/test_service.py -q --tb=short"
    _PlannerSeamHandler.requests = []
    _PlannerSeamHandler.response_payload = {
        "data": {
            "output_text": json.dumps(
                {
                    "steps": [
                        {
                            "type": "read_range",
                            "file": "service.py",
                            "start_line": 1,
                            "end_line": 2,
                            "output_content": "def f():\n    pass\n",
                        },
                        {
                            "type": "edit_file",
                            "file": "service.py",
                            "search": "def f():\n    pass\n",
                            "replace": "def f():\n    return 1\n",
                        },
                    ]
                }
            )
        }
    }
    port = _find_free_tcp_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), _PlannerSeamHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    previous_planner = planner_module.settings.RUNTIME_PLANNER_API_URL
    previous_core = planner_module.settings.CORE_API_URL
    planner_module.settings.RUNTIME_PLANNER_API_URL = f"http://127.0.0.1:{port}"
    planner_module.settings.CORE_API_URL = f"http://127.0.0.1:{port}"
    thread.start()
    try:
        steps, _diagnostics = plan_steps_via_llm(
            tenant_id="default",
            project_id="proj",
            correlation_id="corr-anchor-window-implicit-read-source",
            workspace_root=str(workspace_root),
            prompt="Patch the anchored helper and validate it.",
            planner_packet=planner_packet,
            provider="slavx",
            authorization="Bearer token",
        )
    finally:
        planner_module.settings.RUNTIME_PLANNER_API_URL = previous_planner
        planner_module.settings.CORE_API_URL = previous_core
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert [step["tool"] for step in steps] == ["read_range", "edit_file", "cmd.run"]
    assert steps[0]["step_idx"] == 0
    assert steps[1]["args"]["read_source"]["step_idx"] == 0
    assert steps[1]["args"]["read_source"]["file"] == "service.py"


def test_plan_steps_via_llm_rejects_legacy_raw_edit_without_read_source_when_no_prior_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from backend.services import devexecutor_planner_bridge_service as planner_module

    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(parents=True, exist_ok=True)
    planner_packet = _valid_planner_packet()
    planner_packet["target_file"] = "service.py"
    _PlannerSeamHandler.requests = []
    _PlannerSeamHandler.response_payload = {
        "data": {
            "output_text": json.dumps(
                {
                    "steps": [
                        {
                            "type": "edit_file",
                            "file": "service.py",
                            "search": "def f():\n    pass\n",
                            "replace": "def f():\n    return 1\n",
                        }
                    ]
                }
            )
        }
    }
    port = _find_free_tcp_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), _PlannerSeamHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    previous_planner = planner_module.settings.RUNTIME_PLANNER_API_URL
    previous_core = planner_module.settings.CORE_API_URL
    planner_module.settings.RUNTIME_PLANNER_API_URL = f"http://127.0.0.1:{port}"
    planner_module.settings.CORE_API_URL = f"http://127.0.0.1:{port}"
    thread.start()
    try:
        with pytest.raises(PlannerBridgeError, match="PLANNER_EDIT_MISSING_READ_SOURCE"):
            plan_steps_via_llm(
                tenant_id="default",
                project_id="proj",
                correlation_id="corr-missing-implicit-read-source",
                workspace_root=str(workspace_root),
                prompt="Patch the helper.",
                planner_packet=planner_packet,
                provider="slavx",
                authorization="Bearer token",
            )
    finally:
        planner_module.settings.RUNTIME_PLANNER_API_URL = previous_planner
        planner_module.settings.CORE_API_URL = previous_core
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_plan_steps_via_llm_rejects_disallowed_observer_model() -> None:
    planner_packet = _valid_planner_packet()

    with pytest.raises(PlannerBridgeError, match="PLANNER_MODEL_ROUTE_FORBIDDEN"):
        plan_steps_via_llm(
            tenant_id="default",
            project_id="proj",
            correlation_id="corr",
            workspace_root="C:/dev/hyeve",
            prompt="do it",
            planner_packet=planner_packet,
            provider="openrouter",
            model="openrouter/nvidia/nemotron-3-super-120b-a12b",
            authorization="Bearer token",
        )


def test_plan_steps_via_llm_raises_after_retry_budget(monkeypatch) -> None:
    from backend.services import devexecutor_planner_bridge_service as module

    class _Client:
        def __init__(self, *, timeout: int) -> None:
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def post(self, _target_url: str, *, headers: dict[str, str], json: dict[str, object]):
            raise httpx.ReadTimeout("timed out")

    monkeypatch.setattr(module.httpx, "Client", _Client)
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)
    previous_retries = module.settings.RUNTIME_PLANNER_MAX_RETRIES
    module.settings.RUNTIME_PLANNER_MAX_RETRIES = 1
    planner_packet = _valid_planner_packet()
    try:
        with pytest.raises(PlannerBridgeError) as exc:
            plan_steps_via_llm(
                tenant_id="default",
                project_id="proj",
                correlation_id="corr",
                workspace_root="C:/dev/hyeve",
                prompt="do it",
                planner_packet=planner_packet,
                provider="slavx",
                authorization="Bearer token",
            )
    finally:
        module.settings.RUNTIME_PLANNER_MAX_RETRIES = previous_retries

    assert "attempts=2" in str(exc.value)


def test_plan_steps_via_llm_rewrites_explicit_gateway_alias_for_hybrid_workspace(monkeypatch) -> None:
    from backend.services import devexecutor_planner_bridge_service as module

    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"data": {"output_text": '{"steps":[{"tool":"get_status","args":{}}]}'}}

    class _Client:
        calls: list[str] = []

        def __init__(self, *, timeout: int, **_kwargs: object) -> None:
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def post(self, target_url: str, *, headers: dict[str, str], json: dict[str, object]):
            _ = (headers, json)
            self.__class__.calls.append(target_url)
            if len(self.__class__.calls) == 1:
                raise httpx.ReadTimeout("timed out")
            return _Response()

    monkeypatch.setattr(module.httpx, "Client", _Client)
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)
    previous_planner = module.settings.RUNTIME_PLANNER_API_URL
    previous_core = module.settings.CORE_API_URL
    module.settings.RUNTIME_PLANNER_API_URL = "http://api-gateway:8000"
    module.settings.CORE_API_URL = "http://api-gateway:8000"
    gateway = module.HttpPlannerModelGateway()
    try:
        payload, diagnostics = gateway.request_payload(
            tenant_id="default",
            project_id="proj",
            correlation_id="corr",
            workspace_root="C:/dev/hyeve",
            body=_valid_gateway_body(),
            authorization="Bearer token",
            timeout_seconds=30,
            max_retries=1,
            retry_backoff_ms=0,
        )
    finally:
        module.settings.RUNTIME_PLANNER_API_URL = previous_planner
        module.settings.CORE_API_URL = previous_core

    assert payload == {"output_text": '{"steps":[{"tool":"get_status","args":{}}]}'}
    assert _Client.calls == [
        "http://127.0.0.1:8000/api/integrations/llm/chat",
        "http://127.0.0.1:8000/api/integrations/llm/chat",
    ]
    assert diagnostics["attempts"] == 2
    assert diagnostics["local_route_retry_used"] is False


def test_plan_steps_via_llm_does_not_retry_local_route_for_linux_workspace(monkeypatch) -> None:
    from backend.services import devexecutor_planner_bridge_service as module

    class _Client:
        calls: list[str] = []

        def __init__(self, *, timeout: int, **_kwargs: object) -> None:
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def post(self, target_url: str, *, headers: dict[str, str], json: dict[str, object]):
            _ = (headers, json)
            self.__class__.calls.append(target_url)
            raise httpx.ConnectError("[Errno -3] Temporary failure in name resolution")

    monkeypatch.setattr(module.httpx, "Client", _Client)
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)
    previous_planner = module.settings.RUNTIME_PLANNER_API_URL
    previous_core = module.settings.CORE_API_URL
    module.settings.RUNTIME_PLANNER_API_URL = "http://api-gateway:8000"
    module.settings.CORE_API_URL = "http://api-gateway:8000"
    gateway = module.HttpPlannerModelGateway()
    try:
        with pytest.raises(PlannerBridgeError) as excinfo:
            gateway.request_payload(
                tenant_id="default",
                project_id="proj",
                correlation_id="corr",
                workspace_root="/srv/rouvr/proposals/task-2573",
                body=_valid_gateway_body(),
                authorization="Bearer token",
                timeout_seconds=30,
                max_retries=0,
                retry_backoff_ms=0,
            )
    finally:
        module.settings.RUNTIME_PLANNER_API_URL = previous_planner
        module.settings.CORE_API_URL = previous_core

    assert _Client.calls == ["http://api-gateway:8000/api/integrations/llm/chat"]
    assert excinfo.value.diagnostics["local_route_retry_used"] is False
    assert excinfo.value.diagnostics["target_url"] == "http://api-gateway:8000/api/integrations/llm/chat"


def test_plan_steps_via_llm_recovers_managed_integration_service_before_retry(monkeypatch) -> None:
    from backend.services import devexecutor_planner_bridge_service as module

    monkeypatch.setattr(module.os, "name", "posix", raising=False)

    class _Response:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"data": {"output_text": '{"steps":[{"tool":"get_status","args":{}}]}'}}

    class _Client:
        calls: list[str] = []
        health_checks: list[str] = []

        def __init__(self, *, timeout: int, **_kwargs: object) -> None:
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def get(self, target_url: str):
            self.__class__.health_checks.append(target_url)
            if len(self.__class__.health_checks) == 1:
                raise httpx.ConnectError("[Errno -3] Temporary failure in name resolution")
            return _Response()

        def post(self, target_url: str, *, headers: dict[str, str], json: dict[str, object]):
            _ = (headers, json)
            self.__class__.calls.append(target_url)
            return _Response()

    monkeypatch.setattr(module.httpx, "Client", _Client)
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(module, "_should_skip_socket_preflight", lambda **_kwargs: True)
    recovery_commands: list[list[str]] = []

    def _record_recovery_command(command: list[str]) -> dict[str, Any]:
        recovery_commands.append(list(command))
        return {"command": list(command), "returncode": 0, "stdout": "ok", "stderr": None}

    monkeypatch.setattr(module, "_run_planner_service_recovery_command", _record_recovery_command)
    previous_planner = module.settings.RUNTIME_PLANNER_API_URL
    previous_core = module.settings.CORE_API_URL
    module.settings.RUNTIME_PLANNER_API_URL = "http://integration:8020"
    module.settings.CORE_API_URL = "http://integration:8020"
    gateway = module.HttpPlannerModelGateway()
    try:
        payload, diagnostics = gateway.request_payload(
            tenant_id="default",
            project_id="proj",
            correlation_id="corr",
            workspace_root="/srv/rouvr/proposals/task-2573",
            body=_valid_gateway_body(),
            authorization="Bearer token",
            timeout_seconds=30,
            max_retries=0,
            retry_backoff_ms=0,
        )
    finally:
        module.settings.RUNTIME_PLANNER_API_URL = previous_planner
        module.settings.CORE_API_URL = previous_core

    assert payload == {"output_text": '{"steps":[{"tool":"get_status","args":{}}]}'}
    assert _Client.health_checks == [
        "http://integration:8020/health",
        "http://integration:8020/health",
    ]
    assert _Client.calls == ["http://integration:8020/api/integrations/llm/chat"]
    assert diagnostics["planner_base_url_resolved"] == "http://integration:8020"
    assert diagnostics["planner_base_override_set"] is True
    assert diagnostics["planner_base_fallback_used"] is False
    assert diagnostics["guard_blocked"] is False
    assert recovery_commands
    assert recovery_commands[0] == ["docker", "start", "hyeve-integration-service"]
    assert diagnostics["planner_service_recovery_used"] is True
    assert diagnostics["planner_service_recovery"]["after_probe"]["healthy"] is True
    assert diagnostics["planner_service_recovery"]["probe_attempts"]


def test_plan_steps_via_llm_uses_compose_recovery_with_project_env_when_integration_starts_late(monkeypatch) -> None:
    from backend.services import devexecutor_planner_bridge_service as module

    monkeypatch.setattr(module.os, "name", "posix", raising=False)

    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"data": {"output_text": '{"steps":[{"tool":"get_status","args":{}}]}'}}

    class _Client:
        calls: list[str] = []

        def __init__(self, *, timeout: int, **_kwargs: object) -> None:
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def post(self, target_url: str, *, headers: dict[str, str], json: dict[str, object]):
            _ = (headers, json)
            self.__class__.calls.append(target_url)
            return _Response()

    monkeypatch.setattr(module.httpx, "Client", _Client)
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(module, "_should_skip_socket_preflight", lambda **_kwargs: True)
    recovery_commands: list[list[str]] = []

    def _fake_recovery(command: list[str]) -> dict[str, object]:
        recovery_commands.append(list(command))
        return {"command": list(command), "returncode": 0, "stdout": "ok", "stderr": None}

    monkeypatch.setattr(module, "_run_planner_service_recovery_command", _fake_recovery)
    probe_states = iter(
        [
            {"healthy": False, "attempts": [{"healthy": False, "attempt": 1}], "final_probe": {"healthy": False}},
            {"healthy": False, "attempts": [{"healthy": False, "attempt": 1}], "final_probe": {"healthy": False}},
            {
                "healthy": True,
                "attempts": [
                    {"healthy": False, "attempt": 1},
                    {"healthy": False, "attempt": 2},
                    {"healthy": True, "attempt": 3},
                ],
                "final_probe": {"healthy": True, "attempt": 3},
            },
        ]
    )
    monkeypatch.setattr(module, "_probe_planner_service_health", lambda **_kwargs: {"healthy": False})
    monkeypatch.setattr(module, "_probe_planner_service_until_ready", lambda **_kwargs: next(probe_states))
    previous_planner = module.settings.RUNTIME_PLANNER_API_URL
    previous_core = module.settings.CORE_API_URL
    module.settings.RUNTIME_PLANNER_API_URL = "http://integration:8020"
    module.settings.CORE_API_URL = "http://integration:8020"
    gateway = module.HttpPlannerModelGateway()
    try:
        payload, diagnostics = gateway.request_payload(
            tenant_id="default",
            project_id="proj",
            correlation_id="corr",
            workspace_root="/srv/rouvr/proposals/task-2573",
            body=_valid_gateway_body(),
            authorization="Bearer token",
            timeout_seconds=30,
            max_retries=0,
            retry_backoff_ms=0,
        )
    finally:
        module.settings.RUNTIME_PLANNER_API_URL = previous_planner
        module.settings.CORE_API_URL = previous_core

    assert payload == {"output_text": '{"steps":[{"tool":"get_status","args":{}}]}'}
    assert _Client.calls == ["http://integration:8020/api/integrations/llm/chat"]
    assert recovery_commands[-1] == [
        "docker",
        "compose",
        "--project-directory",
        module._MANAGED_PLANNER_SERVICE_COMPOSE_DIR,
        "-f",
        module._MANAGED_PLANNER_SERVICE_COMPOSE_FILE,
        "--env-file",
        module._MANAGED_PLANNER_SERVICE_ENV_FILE,
        "up",
        "-d",
        module._MANAGED_PLANNER_SERVICE_COMPOSE_NAME,
    ]
    assert diagnostics["planner_service_recovery"]["probe_attempts"] == [
        {"healthy": False, "attempt": 1},
        {"healthy": False, "attempt": 2},
        {"healthy": True, "attempt": 3},
    ]
    assert diagnostics["planner_service_recovery"]["after_probe"]["healthy"] is True


def test_plan_steps_via_llm_requires_canonical_planner_packet(monkeypatch) -> None:
    from backend.services import devexecutor_planner_bridge_service as module

    monkeypatch.setattr(module.httpx, "Client", object)

    with pytest.raises(PlannerBridgeError) as exc:
        plan_steps_via_llm(
            tenant_id="default",
            project_id="proj",
            correlation_id="corr",
            workspace_root="C:/dev/hyeve",
            prompt="do it",
            planner_mode="packet_bound_replace_function",
            provider="slavx",
            authorization="Bearer token",
        )

    assert "PLANNER_PACKET_REQUIRED" in str(exc.value)


def test_get_structured_plan_records_parsed_output_for_schema_invalid(monkeypatch) -> None:
    from backend.services import devexecutor_planner_bridge_service as module

    monkeypatch.setattr(
        module,
        "_planner_request_payload",
        lambda **_kwargs: (
            {"response_json": {"steps": [{"tool": "cmd.run", "args": {}}]}},
            {},
        ),
    )

    with pytest.raises(PlannerBridgeError) as exc:
        module._get_structured_plan(
            tenant_id="default",
            project_id="proj",
            correlation_id="corr",
            workspace_root="C:/dev/hyeve",
            provider="slavx",
            model=None,
            messages=[{"role": "system", "content": "system"}, {"role": "user", "content": "user"}],
            request_metadata={},
            planner_packet=_valid_planner_packet(),
            timeout_seconds=None,
            max_retries=None,
            retry_backoff_ms=None,
            authorization="Bearer token",
        )

    diagnostics = dict(getattr(exc.value, "diagnostics", None) or {})
    assert "PLANNER_SCHEMA_INVALID" in str(exc.value)
    assert diagnostics["planner_parsed_output"]["steps"][0]["tool"] == "cmd.run"
    assert diagnostics["planner_validation_errors"]


def test_get_structured_plan_repairs_packet_bound_read_range_line_window_to_symbol(monkeypatch) -> None:
    from backend.services import devexecutor_planner_bridge_service as module

    packet = _valid_planner_packet()
    packet["target_file"] = "backend/services/example.py"
    packet["target_symbol"] = "backend.services.example.run"
    payload = {
        "steps": [
            {"type": "read_range", "file": "backend/services/example.py", "start_line": 1, "end_line": 8},
            {
                "type": "replace_function",
                "file": "backend/services/example.py",
                "qualified_symbol": "backend.services.example.run",
                "edit_intent": "Return the normalized value.",
            },
            {"type": "run_validation_command", "command": "python -m pytest backend/tests/test_example.py -q"},
        ]
    }
    monkeypatch.setattr(
        module,
        "_planner_request_payload",
        lambda **_kwargs: ({"response_json": payload}, {}),
    )

    parsed, diagnostics, _raw, _attempt = module._get_structured_plan(
        tenant_id="default",
        project_id="proj",
        correlation_id="corr",
        workspace_root="C:/dev/hyeve",
        provider="slavx",
        model=None,
        messages=[{"role": "system", "content": "system"}, {"role": "user", "content": "user"}],
        request_metadata={},
        planner_packet=packet,
        timeout_seconds=None,
        max_retries=None,
        retry_backoff_ms=None,
        authorization="Bearer token",
        planner_mode="packet_bound_replace_function",
    )

    assert parsed["steps"][0]["symbol"] == "backend.services.example.run"
    assert "start_line" not in parsed["steps"][0]
    assert diagnostics["planner_validation_errors"] == []


def test_planner_schema_validation_allows_packet_bound_replace_function_without_explicit_target_fields() -> None:
    from backend.services.devexecutor_planner_bridge_service import _planner_schema_validation_errors

    parsed = {
        "steps": [
            {
                "type": "replace_function",
                "edit_intent": "Return the normalized runtime packet before closeout.",
                "preserve_constraints": ["preserve function signature"],
            }
        ]
    }
    planner_packet = _valid_planner_packet()
    planner_packet["target_symbol"] = "backend.routers.x.handle_invalid_input"
    planner_packet["admitted_target_files"] = ["backend/routers/x.py"]
    planner_packet["admitted_symbol_targets"] = ["backend.routers.x.handle_invalid_input"]

    assert _planner_schema_validation_errors(parsed, planner_packet=planner_packet) == []


def test_plan_steps_via_llm_rejects_packet_bound_old_shape_edit_file_before_grounding(
    monkeypatch, tmp_path: Path
) -> None:
    from backend.services import devexecutor_planner_bridge_service as planner_module

    planner_packet = _packet_bound_single_symbol_planner_packet()
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(parents=True, exist_ok=True)
    (workspace_root / "backend" / "services").mkdir(parents=True, exist_ok=True)
    (workspace_root / "backend" / "services" / "example.py").write_text(
        "def run() -> None:\n    value = 1\n    return None\n",
        encoding="utf-8",
    )
    violating_payload: dict[str, object] = {
        "data": {
            "output_text": json.dumps(
                {
                    "steps": [
                        {
                            "type": "edit_file",
                            "file": "backend/services/example.py",
                            "grounding_source": "anchor",
                            "search_range": {"start_line": 1, "end_line": -1},
                            "replacement_text": "def run() -> None:\n    value = 1\n    return None",
                        },
                        {"type": "run_validation_command", "command": planner_packet["validation_command"]},
                    ]
                }
            )
        }
    }
    _PlannerSeamHandler.requests = []
    _PlannerSeamHandler.response_sequence = [violating_payload]
    _PlannerSeamHandler.response_payload = violating_payload
    port = _find_free_tcp_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), _PlannerSeamHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    previous_planner = planner_module.settings.RUNTIME_PLANNER_API_URL
    previous_core = planner_module.settings.CORE_API_URL
    planner_module.settings.RUNTIME_PLANNER_API_URL = f"http://127.0.0.1:{port}"
    planner_module.settings.CORE_API_URL = f"http://127.0.0.1:{port}"
    thread.start()
    try:
        with pytest.raises(PlannerBridgeError, match="PLANNER_CONTRACT_VIOLATION") as exc_info:
            plan_steps_via_llm(
                tenant_id="default",
                project_id="proj",
                correlation_id="corr-contract-violation-2846",
                workspace_root=str(workspace_root),
                prompt="Use packet-bound replace_function only.",
                planner_packet=planner_packet,
                provider="slavx",
                authorization="Bearer token",
            )
    finally:
        planner_module.settings.RUNTIME_PLANNER_API_URL = previous_planner
        planner_module.settings.CORE_API_URL = previous_core
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        _PlannerSeamHandler.response_sequence = []

    diagnostics = dict(getattr(exc_info.value, "diagnostics", None) or {})
    assert len(_PlannerSeamHandler.requests) == 1
    assert diagnostics["packet_bound_lane"] is True
    assert diagnostics["real_output_tool_types"] == ["edit_file", "run_validation_command"]
    assert diagnostics["lane_contract_expected"] == "replace_function_only"
    assert diagnostics["lane_contract_accepted"] is False
    assert diagnostics["validation_stage_of_rejection"] == "planner_contract"
    assert (
        "packet_bound_single_symbol_replace_function_lane_forbids_non_replace_function_mutation_steps"
        in diagnostics["planner_contract_rejection_reason"]
    )
    assert diagnostics["planner_parsed_output"]["steps"][0]["type"] == "edit_file"
    assert "PLANNER_EDIT_INVALID_SEARCH_RANGE" not in str(exc_info.value)


def test_plan_steps_via_llm_shadow_writes_trace_to_ayrpack(monkeypatch) -> None:
    from backend.services import devexecutor_planner_bridge_service as module

    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"data": {"output_text": '{"steps":[{"tool":"get_status","args":{}}]}'}}

    class _Client:
        def __init__(self, *, timeout: int) -> None:
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def post(self, _target_url: str, *, headers: dict[str, str], json: dict[str, object]):
            return _Response()

    captured: dict[str, object] = {}

    async def _submit_packet(**kwargs):
        captured["submit"] = kwargs
        return {"success": True, "data": {"packet_id": "pkt-persisted-1"}}, None

    async def _append_audit(**kwargs):
        captured["audit"] = kwargs
        return {"success": True}, None

    monkeypatch.setattr(module.httpx, "Client", _Client)
    monkeypatch.setattr(module.ayrpack_client, "submit_packet", _submit_packet)
    monkeypatch.setattr(module.ayrpack_client, "append_audit", _append_audit)
    previous_shadow = module.settings.AYRPACK_SHADOW_WRITE_ENABLED
    previous_token = module.settings.AYRPACK_API_TOKEN
    module.settings.AYRPACK_SHADOW_WRITE_ENABLED = True
    module.settings.AYRPACK_API_TOKEN = "token"
    planner_packet = _valid_planner_packet()
    try:
        steps, diagnostics = plan_steps_via_llm(
            tenant_id="default",
            project_id="proj",
            correlation_id="corr",
            workspace_root="/workspace/hyeve",
            prompt="do it",
            planner_packet=planner_packet,
            provider="slavx",
            authorization="Bearer token",
        )
    finally:
        module.settings.AYRPACK_SHADOW_WRITE_ENABLED = previous_shadow
        module.settings.AYRPACK_API_TOKEN = previous_token

    assert steps == [{"tool": "get_status", "args": {}}]
    assert diagnostics["trace_packet_id"] == "pkt-persisted-1"
    assert diagnostics["trace_ref"] == "ayrpack://packet/pkt-persisted-1"
    assert diagnostics["trace_shadow_write_error"] is None
    assert diagnostics["trace_shadow_role"] == "debug_forensic_mirror"
    assert diagnostics["trace_shadow_authority"] == "non_canonical"
    assert diagnostics["trace_shadow_required_for_control_plane"] is False
    assert diagnostics["shadow_trace_contract"]["required_for_control_plane"] is False
    submit = captured["submit"]
    audit = captured["audit"]
    assert isinstance(submit, dict)
    assert isinstance(audit, dict)
    submit_payload = submit["packet_payload"]
    assert audit["packet_id"] == "pkt-persisted-1"
    assert audit["audit_payload"]["event_data_json"]["packet_id"] == "pkt-persisted-1"
    assert submit_payload["schema_name"] == "aix_trace_packet_current"
    assert submit_payload["payload_json"]["trace_kind"] == "planner_trace"
    assert submit_payload["payload_json"]["planner"]["normalized_steps"][0]["tool"] == "get_status"
    assert submit_payload["metadata_json"]["source"] == "aix.devexecutor_planner_bridge"


@pytest.mark.asyncio
async def test_plan_steps_via_llm_shadow_writes_trace_inside_running_event_loop(monkeypatch) -> None:
    from backend.services import devexecutor_planner_bridge_service as module

    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"data": {"output_text": '{"steps":[{"tool":"get_status","args":{}}]}'}}

    class _Client:
        def __init__(self, *, timeout: int) -> None:
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def post(self, _target_url: str, *, headers: dict[str, str], json: dict[str, object]):
            return _Response()

    captured: dict[str, object] = {}

    async def _submit_packet(**kwargs):
        captured["submit"] = kwargs
        return {"success": True, "data": {"packet_id": "pkt-persisted-2"}}, None

    async def _append_audit(**kwargs):
        captured["audit"] = kwargs
        return {"success": True}, None

    monkeypatch.setattr(module.httpx, "Client", _Client)
    monkeypatch.setattr(module.ayrpack_client, "submit_packet", _submit_packet)
    monkeypatch.setattr(module.ayrpack_client, "append_audit", _append_audit)
    previous_shadow = module.settings.AYRPACK_SHADOW_WRITE_ENABLED
    previous_token = module.settings.AYRPACK_API_TOKEN
    module.settings.AYRPACK_SHADOW_WRITE_ENABLED = True
    module.settings.AYRPACK_API_TOKEN = "token"
    planner_packet = _valid_planner_packet()
    try:
        steps, diagnostics = plan_steps_via_llm(
            tenant_id="default",
            project_id="proj",
            correlation_id="corr",
            workspace_root="/workspace/hyeve",
            prompt="do it",
            planner_packet=planner_packet,
            provider="slavx",
            authorization="Bearer token",
        )
    finally:
        module.settings.AYRPACK_SHADOW_WRITE_ENABLED = previous_shadow
        module.settings.AYRPACK_API_TOKEN = previous_token

    assert steps == [{"tool": "get_status", "args": {}}]
    assert diagnostics["trace_packet_id"] == "pkt-persisted-2"
    assert diagnostics["trace_shadow_write_error"] is None
    assert diagnostics["trace_shadow_role"] == "debug_forensic_mirror"
    assert captured["submit"]


def test_plan_steps_via_llm_reports_audit_failure_after_packet_persist(monkeypatch) -> None:
    from backend.services import devexecutor_planner_bridge_service as module

    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"data": {"output_text": '{"steps":[{"tool":"get_status","args":{}}]}'}}

    class _Client:
        def __init__(self, *, timeout: int) -> None:
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def post(self, _target_url: str, *, headers: dict[str, str], json: dict[str, object]):
            return _Response()

    captured: dict[str, object] = {}

    async def _submit_packet(**kwargs):
        captured["submit"] = kwargs
        return {"success": True, "data": {"packet_id": "pkt-persisted-3"}}, None

    async def _append_audit(**kwargs):
        captured["audit"] = kwargs
        return None, "HTTP_500"

    monkeypatch.setattr(module.httpx, "Client", _Client)
    monkeypatch.setattr(module.ayrpack_client, "submit_packet", _submit_packet)
    monkeypatch.setattr(module.ayrpack_client, "append_audit", _append_audit)
    previous_shadow = module.settings.AYRPACK_SHADOW_WRITE_ENABLED
    previous_token = module.settings.AYRPACK_API_TOKEN
    module.settings.AYRPACK_SHADOW_WRITE_ENABLED = True
    module.settings.AYRPACK_API_TOKEN = "token"
    planner_packet = _valid_planner_packet()
    try:
        steps, diagnostics = plan_steps_via_llm(
            tenant_id="default",
            project_id="proj",
            correlation_id="corr",
            workspace_root="/workspace/hyeve",
            prompt="do it",
            planner_packet=planner_packet,
            provider="slavx",
            authorization="Bearer token",
        )
    finally:
        module.settings.AYRPACK_SHADOW_WRITE_ENABLED = previous_shadow
        module.settings.AYRPACK_API_TOKEN = previous_token

    assert steps == [{"tool": "get_status", "args": {}}]
    assert diagnostics["trace_packet_id"] == "pkt-persisted-3"
    assert diagnostics["trace_ref"] == "ayrpack://packet/pkt-persisted-3"
    assert diagnostics["trace_shadow_write_error"] == "AYRPACK_AUDIT_HTTP_500"
    assert diagnostics["trace_shadow_required_for_control_plane"] is False
    audit = captured["audit"]
    assert isinstance(audit, dict)
    assert audit["packet_id"] == "pkt-persisted-3"


def test_plan_steps_via_llm_persists_local_trace(monkeypatch, tmp_path) -> None:
    from backend.services import devexecutor_planner_bridge_service as module

    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"data": {"output_text": '{"steps":[{"tool":"get_status","args":{}}]}'}}

    class _Client:
        def __init__(self, *, timeout: int) -> None:
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def post(self, _target_url: str, *, headers: dict[str, str], json: dict[str, object]):
            return _Response()

    monkeypatch.setattr(module.httpx, "Client", _Client)
    monkeypatch.setattr(module, "LOCAL_TRACE_ROOT", tmp_path / "planner-traces")
    previous_shadow = module.settings.AYRPACK_SHADOW_WRITE_ENABLED
    module.settings.AYRPACK_SHADOW_WRITE_ENABLED = False
    planner_packet = _valid_planner_packet()
    try:
        steps, diagnostics = plan_steps_via_llm(
            tenant_id="default",
            project_id="proj",
            correlation_id="corr",
            workspace_root="/workspace/hyeve",
            prompt="do it",
            planner_packet=planner_packet,
            provider="slavx",
            authorization="Bearer token",
        )
    finally:
        module.settings.AYRPACK_SHADOW_WRITE_ENABLED = previous_shadow

    assert steps == [{"tool": "get_status", "args": {}}]
    assert diagnostics["trace_packet_id"] is None
    assert diagnostics["local_trace_error"] is None
    assert diagnostics["canonical_operator_sources"] == ["workhub", "run_ledger", "execution_digest"]
    local_trace = diagnostics["local_trace_ref"]
    assert isinstance(local_trace, str) and local_trace.endswith(".json")
    payload = json.loads(Path(local_trace).read_text(encoding="utf-8"))
    assert payload["provider"] == "slavx"
    assert payload["planner_packet_snapshot"]["slice_id"] == "slice-143-001"
    assert payload["normalized_steps"][0]["tool"] == "get_status"


def test_plan_steps_via_llm_validates_inbound_planner_packet(monkeypatch) -> None:
    planner_packet = {
        "schema_name": "aix.planner.slice_packet.current",
        "schema_version": "current",
        "task_number": 143,
        "slice_id": "slice-143-001",
    }

    with pytest.raises(PlannerBridgeError) as exc:
        plan_steps_via_llm(
            tenant_id="default",
            project_id="proj",
            correlation_id="corr",
            workspace_root="C:/dev/hyeve",
            prompt="do it",
            planner_packet=planner_packet,
            planner_mode="packet_bound_replace_function",
            provider="slavx",
            authorization="Bearer token",
        )

    assert "BUILDER_CURRENT_PACKET_REQUIRED" in str(exc.value)


def test_planner_bridge_mode_does_not_infer_strict_from_grounded_packet_shape() -> None:
    from backend.services import devexecutor_planner_bridge_service as module

    planner_packet = {
        "schema_name": "planner_slice.current",
        "target_file": "backend/service.py",
        "target_symbol": "backend.service.target",
        "codeintel_anchor": {"symbol": "backend.service.target", "start_line": 1, "end_line": 3},
    }

    assert module._resolve_planner_mode(planner_packet=planner_packet) == "generic"
    assert (
        module._resolve_planner_mode(
            planner_mode="packet_bound_replace_function",
            planner_packet=planner_packet,
        )
        == "packet_bound_replace_function"
    )


def test_validation_and_quality_modes_do_not_infer_strict_from_grounded_packet_shape() -> None:
    from backend.services import aix_quality_enforcement_service as quality_module
    from backend.services import task_runner_planner_validation as validation_module

    planner_packet = {
        "schema_name": "planner_slice.current",
        "target_file": "backend/service.py",
        "target_symbol": "backend.service.target",
        "codeintel_anchor": {"symbol": "backend.service.target", "start_line": 1, "end_line": 3},
    }

    assert validation_module._resolve_planner_mode(planner_packet) == "generic"
    assert quality_module._planner_mode(planner_packet) == "generic"
    planner_packet["planner_mode"] = "packet_bound_replace_function"
    assert validation_module._resolve_planner_mode(planner_packet) == "packet_bound_replace_function"
    assert quality_module._planner_mode(planner_packet) == "packet_bound_replace_function"


def test_plan_steps_via_llm_persists_local_trace_on_parse_failure(monkeypatch, tmp_path) -> None:
    from backend.services import devexecutor_planner_bridge_service as module

    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"data": {"output_text": '{"steps" [{"tool":"get_status","args":{}}]}'}}

    class _Client:
        def __init__(self, *, timeout: int) -> None:
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def post(self, _target_url: str, *, headers: dict[str, str], json: dict[str, object]):
            return _Response()

    monkeypatch.setattr(module.httpx, "Client", _Client)
    monkeypatch.setattr(module, "LOCAL_TRACE_ROOT", tmp_path / "planner-traces")
    previous_shadow = module.settings.AYRPACK_SHADOW_WRITE_ENABLED
    module.settings.AYRPACK_SHADOW_WRITE_ENABLED = False
    planner_packet = _valid_planner_packet()
    try:
        with pytest.raises(PlannerBridgeError) as exc:
            plan_steps_via_llm(
                tenant_id="default",
                project_id="proj",
                correlation_id="corr",
                workspace_root="/workspace/hyeve",
                prompt="do it",
                planner_packet=planner_packet,
                provider="slavx",
                authorization="Bearer token",
            )
    finally:
        module.settings.AYRPACK_SHADOW_WRITE_ENABLED = previous_shadow

    text = str(exc.value)
    assert "PLANNER_JSON_INVALID" in text
    assert "local_trace_ref=" in text
    local_trace = text.split("local_trace_ref=", 1)[1].split(" ", 1)[0]
    payload = json.loads(Path(local_trace).read_text(encoding="utf-8"))
    assert payload["parse_error"].startswith("PLANNER_JSON_INVALID")
    assert payload["raw_output_text"] == '{"steps" [{"tool":"get_status","args":{}}]}'


def test_plan_steps_via_llm_persists_local_trace_on_provider_reasoning_exhaustion(monkeypatch, tmp_path) -> None:
    from backend.services import devexecutor_planner_bridge_service as module

    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "data": {
                    "provider": "openrouter",
                    "resolved_provider": "openrouter",
                    "model": "openrouter/z-ai/glm-5",
                    "usage": {"completion_tokens_details": {"reasoning_tokens": 2985}},
                    "response_json": {
                        "choices": [
                            {
                                "message": {
                                    "role": "assistant",
                                    "content": None,
                                    "tool_calls": None,
                                    "function_call": None,
                                }
                            }
                        ]
                    },
                }
            }

    class _Client:
        def __init__(self, *, timeout: int) -> None:
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def post(self, _target_url: str, *, headers: dict[str, str], json: dict[str, object]):
            return _Response()

    monkeypatch.setattr(module.httpx, "Client", _Client)
    monkeypatch.setattr(module, "LOCAL_TRACE_ROOT", tmp_path / "planner-traces")
    previous_shadow = module.settings.AYRPACK_SHADOW_WRITE_ENABLED
    module.settings.AYRPACK_SHADOW_WRITE_ENABLED = False
    planner_packet = _valid_planner_packet()
    try:
        with pytest.raises(PlannerBridgeError) as exc:
            plan_steps_via_llm(
                tenant_id="default",
                project_id="proj",
                correlation_id="corr",
                workspace_root="/workspace/hyeve",
                prompt="do it",
                planner_packet=planner_packet,
                provider="openrouter",
                model="openrouter/z-ai/glm-5",
                authorization="Bearer token",
            )
    finally:
        module.settings.AYRPACK_SHADOW_WRITE_ENABLED = previous_shadow

    text = str(exc.value)
    assert "PROVIDER_REASONING_EXHAUSTION" in text
    assert "local_trace_ref=" in text
    local_trace = text.split("local_trace_ref=", 1)[1].split(" ", 1)[0]
    payload = json.loads(Path(local_trace).read_text(encoding="utf-8"))
    assert payload["parse_error"].startswith("PROVIDER_REASONING_EXHAUSTION")
    assert payload["raw_output_text"] == ""
    assert payload["upstream_payload"]["response_json"]["choices"][0]["message"]["content"] is None


def test_plan_steps_via_llm_persists_planner_packet_snapshot_in_local_trace(monkeypatch, tmp_path) -> None:
    from backend.services import devexecutor_planner_bridge_service as module

    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"data": {"output_text": '{"steps":[{"tool":"get_status","args":{}}]}'}}

    class _Client:
        def __init__(self, *, timeout: int) -> None:
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def post(self, _target_url: str, *, headers: dict[str, str], json: dict[str, object]):
            return _Response()

    monkeypatch.setattr(module.httpx, "Client", _Client)
    monkeypatch.setattr(module, "LOCAL_TRACE_ROOT", tmp_path / "planner-traces")
    previous_shadow = module.settings.AYRPACK_SHADOW_WRITE_ENABLED
    module.settings.AYRPACK_SHADOW_WRITE_ENABLED = False
    planner_packet = _valid_planner_packet()
    try:
        _, diagnostics = plan_steps_via_llm(
            tenant_id="default",
            project_id="proj",
            correlation_id="corr",
            workspace_root="/workspace/hyeve",
            prompt="do it",
            planner_packet=planner_packet,
            provider="slavx",
            authorization="Bearer token",
        )
    finally:
        module.settings.AYRPACK_SHADOW_WRITE_ENABLED = previous_shadow

    payload = json.loads(Path(diagnostics["local_trace_ref"]).read_text(encoding="utf-8"))
    assert payload["planner_packet_snapshot"]["slice_id"] == "slice-143-001"
    assert payload["planner_packet_snapshot"]["target_file"] == "backend/routers/x.py"
    assert payload["planner_request_shape"] == "generic_prompt"
    assert payload["planner_packet_fingerprint"] == diagnostics["planner_packet_fingerprint"]
    assert payload["request_payload_bytes"] > 0
    assert payload["request_metadata"]["planner_request_shape"] == "generic_prompt"


def test_plan_steps_via_llm_uses_shared_runlog_root_authority(monkeypatch, tmp_path) -> None:
    from backend.services import devexecutor_planner_bridge_service as module

    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"data": {"output_text": '{"steps":[{"tool":"get_status","args":{}}]}'}}

    class _Client:
        def __init__(self, *, timeout: int) -> None:
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def post(self, _target_url: str, *, headers: dict[str, str], json: dict[str, object]):
            return _Response()

    repo_root = tmp_path / "repo"
    planner_trace_root = tmp_path / "_runlogs" / "planner-traces"
    monkeypatch.setattr(module.settings, "AIX_RUNLOG_ROOT", str(tmp_path / "_runlogs"))

    monkeypatch.setattr(module.httpx, "Client", _Client)
    previous_shadow = module.settings.AYRPACK_SHADOW_WRITE_ENABLED
    module.settings.AYRPACK_SHADOW_WRITE_ENABLED = False
    planner_packet = _valid_planner_packet()
    try:
        _, diagnostics = plan_steps_via_llm(
            tenant_id="default",
            project_id="proj",
            correlation_id="corr",
            workspace_root=str(repo_root),
            prompt="do it",
            planner_packet=planner_packet,
            provider="slavx",
            authorization="Bearer token",
        )
    finally:
        module.settings.AYRPACK_SHADOW_WRITE_ENABLED = previous_shadow

    local_trace_path = Path(str(diagnostics["local_trace_ref"]))
    assert local_trace_path.parent == planner_trace_root
    assert local_trace_path.exists()


def test_plan_steps_via_llm_persists_transport_failure_to_shared_runlog_root(monkeypatch, tmp_path) -> None:
    from backend.services import devexecutor_planner_bridge_service as module

    class _Client:
        def __init__(self, *, timeout: int) -> None:
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def post(self, _target_url: str, *, headers: dict[str, str], json: dict[str, object]):
            raise httpx.TimeoutException("timed out")

    repo_root = tmp_path / "repo"
    planner_trace_root = tmp_path / "_runlogs" / "planner-traces"
    monkeypatch.setattr(module.settings, "AIX_RUNLOG_ROOT", str(tmp_path / "_runlogs"))

    monkeypatch.setattr(module.httpx, "Client", _Client)
    previous_shadow = module.settings.AYRPACK_SHADOW_WRITE_ENABLED
    module.settings.AYRPACK_SHADOW_WRITE_ENABLED = False
    planner_packet = _valid_planner_packet()
    try:
        with pytest.raises(PlannerBridgeError) as exc:
            plan_steps_via_llm(
                tenant_id="default",
                project_id="proj",
                correlation_id="corr-transport",
                workspace_root=str(repo_root),
                prompt="do it",
                planner_packet=planner_packet,
                provider="openrouter",
                model="openrouter/z-ai/glm-5",
                authorization="Bearer token",
            )
    finally:
        module.settings.AYRPACK_SHADOW_WRITE_ENABLED = previous_shadow

    local_trace = str(exc.value).split("local_trace_ref=", 1)[1].split(" ", 1)[0]
    local_trace_path = Path(local_trace)
    assert local_trace_path.parent == planner_trace_root
    assert local_trace_path.exists()
    payload = json.loads(local_trace_path.read_text(encoding="utf-8"))
    assert payload["parse_error"].startswith("PLANNER_TRANSPORT_TIMEOUT")


def test_plan_steps_via_llm_seam_persists_shared_runlog_trace_via_real_http(monkeypatch, tmp_path) -> None:
    from backend.services import devexecutor_planner_bridge_service as module

    repo_root = tmp_path / "repo"
    planner_trace_root = tmp_path / "_runlogs" / "planner-traces"
    monkeypatch.setattr(module.settings, "AIX_RUNLOG_ROOT", str(tmp_path / "_runlogs"))
    port = _find_free_tcp_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), _PlannerSeamHandler)
    _PlannerSeamHandler.requests = []
    _PlannerSeamHandler.response_payload = {"data": {"output_text": '{"steps":[{"tool":"get_status","args":{}}]}'}}
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    previous_shadow = module.settings.AYRPACK_SHADOW_WRITE_ENABLED
    previous_url = module.settings.RUNTIME_PLANNER_API_URL
    planner_packet = _valid_planner_packet()
    try:
        module.settings.AYRPACK_SHADOW_WRITE_ENABLED = False
        module.settings.RUNTIME_PLANNER_API_URL = f"http://127.0.0.1:{port}"
        steps, diagnostics = plan_steps_via_llm(
            tenant_id="default",
            project_id="proj",
            correlation_id="corr-seam",
            workspace_root=str(repo_root),
            prompt="do it",
            planner_packet=planner_packet,
            provider="openrouter",
            model="openrouter/z-ai/glm-5",
            authorization="Bearer token",
            max_retries=0,
        )
    finally:
        module.settings.AYRPACK_SHADOW_WRITE_ENABLED = previous_shadow
        module.settings.RUNTIME_PLANNER_API_URL = previous_url
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert steps == [{"tool": "get_status", "args": {}}]
    assert len(_PlannerSeamHandler.requests) == 1
    local_trace_path = Path(str(diagnostics["local_trace_ref"]))
    assert local_trace_path.parent == planner_trace_root
    payload = json.loads(local_trace_path.read_text(encoding="utf-8"))
    assert payload["planner_prompt"] == "do it"
    assert payload["provider"] == "openrouter"
    assert payload["model"] == "openrouter/z-ai/glm-5"
    assert payload["upstream_payload"] is None
    assert payload["transport_diagnostics"]["request_build_ms"] >= 0.0
    assert payload["transport_diagnostics"]["failure_phase"] is None
    assert len(payload["transport_diagnostics"]["transport_attempts"]) == 1
    assert payload["transport_diagnostics"]["transport_attempts"][0]["preflight_phase"] == "skipped"
    assert payload["transport_diagnostics"]["transport_attempts"][0]["http_request_ms"] >= 0.0
    assert payload["transport_diagnostics"]["transport_attempts"][0]["response_parse_ms"] >= 0.0
    request_body = _PlannerSeamHandler.requests[0]["body"]
    assert isinstance(request_body, dict)
    assert request_body["provider"] == "openrouter"
    assert request_body["model"] == "openrouter/z-ai/glm-5"


def test_plan_steps_via_llm_seam_persists_shared_runlog_trace_on_real_transport_failure(
    monkeypatch,
    tmp_path,
) -> None:
    from backend.services import devexecutor_planner_bridge_service as module

    repo_root = tmp_path / "repo"
    planner_trace_root = tmp_path / "_runlogs" / "planner-traces"
    monkeypatch.setattr(module.settings, "AIX_RUNLOG_ROOT", str(tmp_path / "_runlogs"))
    port = _find_free_tcp_port()
    previous_shadow = module.settings.AYRPACK_SHADOW_WRITE_ENABLED
    previous_url = module.settings.RUNTIME_PLANNER_API_URL
    planner_packet = _valid_planner_packet()
    try:
        module.settings.AYRPACK_SHADOW_WRITE_ENABLED = False
        module.settings.RUNTIME_PLANNER_API_URL = f"http://127.0.0.1:{port}"
        with pytest.raises(PlannerBridgeError) as exc:
            plan_steps_via_llm(
                tenant_id="default",
                project_id="proj",
                correlation_id="corr-seam-fail",
                workspace_root=str(repo_root),
                prompt="do it",
                planner_packet=planner_packet,
                provider="openrouter",
                model="openrouter/z-ai/glm-5",
                authorization="Bearer token",
                timeout_seconds=1,
                max_retries=0,
            )
    finally:
        module.settings.AYRPACK_SHADOW_WRITE_ENABLED = previous_shadow
        module.settings.RUNTIME_PLANNER_API_URL = previous_url

    assert "local_trace_ref=" in str(exc.value)
    local_trace_path = Path(str(exc.value).split("local_trace_ref=", 1)[1].split(" ", 1)[0])
    assert local_trace_path.parent == planner_trace_root
    payload = json.loads(local_trace_path.read_text(encoding="utf-8"))
    assert payload["parse_error"].startswith("PLANNER_TRANSPORT_")
    assert payload["upstream_payload"]["request_body"]["provider"] == "openrouter"
    assert payload["transport_diagnostics"]["request_build_ms"] >= 0.0
    assert payload["transport_diagnostics"]["failure_phase"] in {"connect", "timeout", "transport", "unknown"}
    assert payload["transport_diagnostics"]["transport_attempts"]
    assert payload["transport_diagnostics"]["transport_attempts"][0]["failure_phase"] in {
        "connect",
        "timeout",
        "transport",
        "unknown",
    }


def test_plan_steps_via_llm_persists_local_trace_on_transport_failure(monkeypatch, tmp_path) -> None:
    from backend.services import devexecutor_planner_bridge_service as module

    class _Client:
        def __init__(self, *, timeout: int) -> None:
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def post(self, _target_url: str, *, headers: dict[str, str], json: dict[str, object]):
            raise httpx.TimeoutException("timed out")

    monkeypatch.setattr(module.httpx, "Client", _Client)
    monkeypatch.setattr(module, "LOCAL_TRACE_ROOT", tmp_path / "planner-traces")
    previous_shadow = module.settings.AYRPACK_SHADOW_WRITE_ENABLED
    module.settings.AYRPACK_SHADOW_WRITE_ENABLED = False
    planner_packet = _valid_planner_packet()
    try:
        with pytest.raises(PlannerBridgeError) as exc:
            plan_steps_via_llm(
                tenant_id="default",
                project_id="proj",
                correlation_id="corr-transport",
                workspace_root="/workspace/hyeve",
                prompt="do it",
                planner_packet=planner_packet,
                provider="openrouter",
                model="openrouter/google/gemini-3.1-pro-preview",
                authorization="Bearer token",
            )
    finally:
        module.settings.AYRPACK_SHADOW_WRITE_ENABLED = previous_shadow

    text = str(exc.value)
    assert "PLANNER_TRANSPORT_TIMEOUT" in text
    assert "local_trace_ref=" in text
    local_trace = text.split("local_trace_ref=", 1)[1].split(" ", 1)[0]
    payload = json.loads(Path(local_trace).read_text(encoding="utf-8"))
    assert payload["correlation_id"] == "corr-transport"
    assert payload["planner_prompt"] == "do it"
    assert payload["parse_error"].startswith("PLANNER_TRANSPORT_TIMEOUT")
    assert payload["raw_output_text"] == ""
    assert payload["upstream_payload"]["request_body"]["provider"] == "openrouter"


def test_build_planner_trace_packet_id_includes_attempts_used() -> None:
    first = _build_planner_trace_packet_id(
        tenant_id="default",
        project_id="proj",
        correlation_id="corr",
        provider="openrouter",
        workspace_root="/workspace/hyeve",
        prompt="do it",
        attempts_used=1,
    )
    second = _build_planner_trace_packet_id(
        tenant_id="default",
        project_id="proj",
        correlation_id="corr",
        provider="openrouter",
        workspace_root="/workspace/hyeve",
        prompt="do it",
        attempts_used=2,
    )

    assert first != second


def test_plan_steps_via_llm_uses_planner_attempt_number_for_local_trace_attempts(monkeypatch, tmp_path) -> None:
    from backend.services import devexecutor_planner_bridge_service as module

    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"data": {"output_text": '{"steps":[{"tool":"get_status","args":{}}]}'}}

    class _Client:
        def __init__(self, *, timeout: int) -> None:
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def post(self, _target_url: str, *, headers: dict[str, str], json: dict[str, object]):
            return _Response()

    monkeypatch.setattr(module.httpx, "Client", _Client)
    monkeypatch.setattr(module, "LOCAL_TRACE_ROOT", tmp_path / "planner-traces")
    previous_shadow = module.settings.AYRPACK_SHADOW_WRITE_ENABLED
    module.settings.AYRPACK_SHADOW_WRITE_ENABLED = False
    planner_packet = _valid_planner_packet()
    try:
        _, diagnostics = plan_steps_via_llm(
            tenant_id="default",
            project_id="proj",
            correlation_id="corr",
            workspace_root="/workspace/hyeve",
            prompt="do it",
            planner_packet=planner_packet,
            provider="slavx",
            authorization="Bearer token",
            planner_attempt_number=3,
        )
    finally:
        module.settings.AYRPACK_SHADOW_WRITE_ENABLED = previous_shadow

    payload = json.loads(Path(diagnostics["local_trace_ref"]).read_text(encoding="utf-8"))
    assert payload["attempts_used"] == 3
    assert diagnostics["planner_attempt_number"] == 3


def test_managed_planner_compose_file_default(monkeypatch) -> None:
    import backend.services.devexecutor_planner_bridge_service as module

    monkeypatch.delenv("AIX_MANAGED_PLANNER_COMPOSE_FILE", raising=False)
    reloaded = importlib.reload(module)
    assert reloaded._MANAGED_PLANNER_SERVICE_COMPOSE_FILE == (
        "/srv/rouvr/repos/hyeve/deploy/hostinger/docker-compose.aix-min.yml"
    )
    assert reloaded._MANAGED_PLANNER_SERVICE_COMPOSE_DIR.endswith("deploy/hostinger")
    assert reloaded._MANAGED_PLANNER_SERVICE_ENV_FILE.endswith("deploy/hostinger/.env.aix-min")


def test_managed_planner_compose_file_env_override(monkeypatch) -> None:
    import backend.services.devexecutor_planner_bridge_service as module

    monkeypatch.setenv("AIX_MANAGED_PLANNER_COMPOSE_FILE", "/tmp/override-stack/docker-compose.yml")
    try:
        reloaded = importlib.reload(module)
        assert reloaded._MANAGED_PLANNER_SERVICE_COMPOSE_FILE == "/tmp/override-stack/docker-compose.yml"
        assert reloaded._MANAGED_PLANNER_SERVICE_COMPOSE_DIR == "/tmp/override-stack"
        assert reloaded._MANAGED_PLANNER_SERVICE_ENV_FILE == "/tmp/override-stack/.env.aix-min"
    finally:
        monkeypatch.delenv("AIX_MANAGED_PLANNER_COMPOSE_FILE", raising=False)
        importlib.reload(module)
