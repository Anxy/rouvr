from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.services.aix_model_ingress_gate_service import (
    CLI_EXECUTOR_INGRESS_SCHEMA,
    CURRENT_INGRESS_SCHEMA_CATALOG,
    MODEL_REQUEST_SCHEMA,
    PLANNER_INGRESS_SCHEMA,
    REPAIR_INGRESS_SCHEMA,
    UTF_TASK_SCHEMA,
    VERIFIER_INGRESS_SCHEMA,
    ModelIngressGateError,
    _resolve_schema_dir,
    compile_model_ingress_request,
    enforce_model_ingress_gate,
    validate_current_ingress_payload,
)

ROOT = Path(__file__).resolve().parents[5]
FIXTURE_DIR = ROOT / "fixtures" / "aix" / "model_ingress"


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def test_model_ingress_gate_attaches_prompt_token_breakdown() -> None:
    body = {
        "messages": [
            {"role": "system", "content": "Return JSON."},
            {"role": "user", "content": '{"schema_name":"aix.planner_ingress.current","task_identity":{}}'},
        ],
        "metadata": {"planner_request_shape": PLANNER_INGRESS_SCHEMA},
    }

    guarded = enforce_model_ingress_gate(body, source="aix.planner_bridge")

    metadata = guarded["metadata"]
    assert metadata["model_ingress_gate"]["schema"] == "aix.model_ingress.current"
    assert metadata["model_ingress_gate"]["estimated_prompt_tokens"] < 2000
    assert metadata["prompt_token_breakdown"]["messages"][1]["role"] == "user"


def test_model_ingress_schema_dir_resolves_repo_and_container_layouts(tmp_path: Path) -> None:
    repo_schema_dir = _resolve_schema_dir()
    assert (repo_schema_dir / "aix.utf_task.current.schema.json").is_file()

    container_root = tmp_path / "app"
    container_schema_dir = container_root / "schemas" / "aix" / "model_ingress"
    container_schema_dir.mkdir(parents=True)
    marker = container_schema_dir / "aix.utf_task.current.schema.json"
    marker.write_text("{}", encoding="utf-8")

    resolved = _resolve_schema_dir(
        start_file=container_root / "service" / "backend" / "services" / "aix_model_ingress_gate_service.py",
        extra_roots=(container_root,),
    )

    assert resolved == container_schema_dir


def test_current_ingress_schema_catalog_exposes_only_current_schema_names() -> None:
    expected = {
        UTF_TASK_SCHEMA,
        PLANNER_INGRESS_SCHEMA,
        CLI_EXECUTOR_INGRESS_SCHEMA,
        REPAIR_INGRESS_SCHEMA,
        VERIFIER_INGRESS_SCHEMA,
        MODEL_REQUEST_SCHEMA,
    }

    assert set(CURRENT_INGRESS_SCHEMA_CATALOG) == expected
    assert all(schema.endswith(".current") for schema in CURRENT_INGRESS_SCHEMA_CATALOG)
    assert not any(".v1" in schema or ".v2" in schema for schema in CURRENT_INGRESS_SCHEMA_CATALOG)


def test_model_ingress_gate_rejects_oversized_prompt() -> None:
    body = {
        "messages": [
            {"role": "user", "content": '{"schema_name":"aix.planner_ingress.current","x":"' + ("x" * 9000) + '"}'}
        ],
        "metadata": {"planner_request_shape": PLANNER_INGRESS_SCHEMA},
    }

    with pytest.raises(ModelIngressGateError, match="PROMPT_TOKEN_CAP_EXCEEDED") as exc_info:
        enforce_model_ingress_gate(body, source="aix.planner_bridge")

    assert exc_info.value.diagnostics["reroute_provider"] == "slavx"


def test_model_ingress_gate_rejects_unapproved_tool_surface() -> None:
    body = {
        "messages": [{"role": "user", "content": '{"schema_name":"aix.planner_ingress.current"}'}],
        "tools": [{"type": "function", "function": {"name": "search", "parameters": {}}}],
        "metadata": {"planner_request_shape": PLANNER_INGRESS_SCHEMA},
    }

    with pytest.raises(ModelIngressGateError, match="TOOL_CALLS_NOT_ALLOWED_BY_COMPACT_SCHEMA"):
        enforce_model_ingress_gate(body, source="aix.planner_bridge")


@pytest.mark.parametrize(
    ("fixture_name", "schema_name"),
    [
        ("utf_task.current.json", UTF_TASK_SCHEMA),
        ("planner_ingress.current.json", PLANNER_INGRESS_SCHEMA),
        ("cli_executor_ingress.current.json", CLI_EXECUTOR_INGRESS_SCHEMA),
        ("repair_ingress.current.json", REPAIR_INGRESS_SCHEMA),
        ("verifier_ingress.current.json", VERIFIER_INGRESS_SCHEMA),
        ("model_request.current.json", MODEL_REQUEST_SCHEMA),
    ],
)
def test_model_ingress_schema_catalog_accepts_current_fixtures(fixture_name: str, schema_name: str) -> None:
    payload = _load_fixture(fixture_name)

    validated = validate_current_ingress_payload(payload, schema_name=schema_name)

    assert validated["schema_name"] == schema_name
    assert ".v1" not in json.dumps(validated, sort_keys=True)
    assert ".v2" not in json.dumps(validated, sort_keys=True)


@pytest.mark.parametrize("fixture_name", ["invalid_legacy_schema.json", "invalid_null_bloat.json"])
def test_model_ingress_schema_catalog_rejects_legacy_and_bloated_fixtures(fixture_name: str) -> None:
    with pytest.raises(ModelIngressGateError):
        validate_current_ingress_payload(_load_fixture(fixture_name))


def test_model_ingress_compiler_emits_schema_backed_fingerprint_and_breakdown() -> None:
    compiled = compile_model_ingress_request(
        kind="planner",
        task_id="task-3004",
        task_type="planner_slice",
        params={"objective": "add model ingress compiler proof"},
        actions=["return_executable_plan"],
        context={"validation_commands": ["python -m pytest backend/tests/test_aix_model_ingress_gate_service.py -q"]},
        model="openai/gpt-5.1-codex",
    )

    assert compiled.schema_name == MODEL_REQUEST_SCHEMA
    assert compiled.schema_version == "current"
    assert compiled.fingerprint.startswith("sha256:")
    assert compiled.artifact["ingress"]["schema_name"] == PLANNER_INGRESS_SCHEMA
    assert compiled.body["metadata"]["prompt_token_breakdown"]["estimated_prompt_tokens"] < 1200


def test_repair_model_ingress_compiler_emits_current_repair_schema() -> None:
    compiled = compile_model_ingress_request(
        kind="repair",
        task_id="task-3009",
        task_type="devexecutor_repair_request",
        params={
            "failure_code": "PLANNER_JSON_INVALID",
            "failed_step": "json_repair",
            "prior_output_summary": "unterminated object",
        },
        actions=["repair_and_return_planner_steps_json"],
        context={"evidence_refs": ["workhub://runs/example/request_body.json"]},
    )

    assert compiled.artifact["ingress"]["schema_name"] == "aix.repair_ingress.current"
    assert compiled.body["metadata"]["ingress_schema"] == "aix.repair_ingress.current"
    assert compiled.body["metadata"]["prompt_token_breakdown"]["estimated_prompt_tokens"] < 1200


def test_model_ingress_compiler_rejects_missing_schema_and_legacy_schema_names() -> None:
    with pytest.raises(ModelIngressGateError, match="MODEL_INGRESS_SCHEMA_NAME_REQUIRED"):
        validate_current_ingress_payload({"schema_version": "current"})

    with pytest.raises(ModelIngressGateError, match="MODEL_INGRESS_LEGACY_SCHEMA_REJECTED"):
        validate_current_ingress_payload({"schema_name": "aix.planner_ingress.v2", "schema_version": "v2"})


def test_model_ingress_legacy_marker_allows_v2_in_file_paths_and_symbols() -> None:
    compiled = compile_model_ingress_request(
        kind="cli_executor",
        task_id="task-3040",
        task_type="cli_executor_mutation",
        params={
            "instruction": "Mutate only the declared target files to satisfy the task objective.",
            "working_dir": "/tmp/workspace",
            "task_title": "Live AIX CLI lane direct compact proof",
        },
        actions=["mutate_declared_scope", "run_declared_validation"],
        context={
            "allowed_target_files": ["backend/services/aix_v2_live_file_grounding_service.py"],
            "validation_commands": ["python -m py_compile backend/services/aix_v2_live_file_grounding_service.py"],
            "target_symbol": "backend.services.aix_v2_live_file_grounding_service.validate_v2_edit_against_live_file",
        },
    )

    assert compiled.artifact["ingress"]["schema_name"] == CLI_EXECUTOR_INGRESS_SCHEMA


@pytest.mark.parametrize(
    "legacy_value",
    [
        "aix.run.summary.v2",
        "ticket_contract_v1",
        "PlannerSlicePacketV1",
    ],
)
def test_model_ingress_legacy_marker_still_rejects_schema_identifiers(legacy_value: str) -> None:
    with pytest.raises(ModelIngressGateError, match="LEGACY_SCHEMA_MARKER"):
        validate_current_ingress_payload(
            {
                "schema_name": UTF_TASK_SCHEMA,
                "schema_version": "current",
                "task_id": "task-legacy",
                "task_type": "probe",
                "params": {"legacy": legacy_value},
                "actions": ["inspect"],
            },
            schema_name=UTF_TASK_SCHEMA,
        )


def test_model_ingress_compiler_rejects_raw_messages_without_compact_schema() -> None:
    with pytest.raises(ModelIngressGateError, match="COMPACT_UTF_SCHEMA_MISSING"):
        enforce_model_ingress_gate(
            {"messages": [{"role": "user", "content": "freeform prompt"}], "metadata": {}},
            source="aix.planner_bridge",
        )
