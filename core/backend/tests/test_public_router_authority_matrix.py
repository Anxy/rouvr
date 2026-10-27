from __future__ import annotations

import ast
import asyncio
import copy
import importlib
import re
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.routing import APIRoute

import backend.main as production


MAIN_PATH = Path(__file__).parents[1] / "main.py"
_MUTATION_METHODS = {"DELETE", "PATCH", "POST", "PUT"}


def _included_router_names() -> set[str]:
    tree = ast.parse(MAIN_PATH.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute) or node.func.attr != "include_router":
            continue
        if not node.args or not isinstance(node.args[0], ast.Name):
            raise AssertionError("Every include_router call must use a named router")
        names.add(node.args[0].id)
    return names


def _mutation_routes(router) -> list[tuple[str, str, APIRoute]]:
    routes: list[tuple[str, str, APIRoute]] = []
    for route in router.routes:
        if not isinstance(route, APIRoute):
            continue
        methods = set(route.methods or ()) & _MUTATION_METHODS
        path = getattr(route, "path_format", route.path)
        param_convertors = getattr(route, "param_convertors", {})
        path_parameter_types = {
            parameter.name: getattr(parameter, "type_", None)
            for parameter in getattr(getattr(route, "dependant", None), "path_params", [])
        }

        def replacement(match: re.Match[str]) -> str:
            parameter_name = match.group(1).split(":", 1)[0]
            convertor = param_convertors.get(parameter_name)
            convertor_name = type(convertor).__name__.lower()
            parameter_type = path_parameter_types.get(parameter_name)
            parameter_type_name = getattr(parameter_type, "__name__", "").lower()
            if "uuid" in convertor_name or "uuid" in parameter_type_name:
                return "00000000-0000-0000-0000-000000000000"
            if "integer" in convertor_name or parameter_type in (int,):
                return "1"
            if "float" in convertor_name or parameter_type in (float,):
                return "1.0"
            return "retired-test-id"

        path = re.sub(r"\{([^}]+)\}", replacement, path)
        routes.extend((method, path, route) for method in sorted(methods))
    return routes


def _request_payload(route: APIRoute) -> dict:
    """Build the smallest valid JSON body for a retired route's validation layer."""
    body_field = getattr(route, "body_field", None)
    field_type = getattr(body_field, "type_", None)
    schema = None
    schema_builder = getattr(field_type, "model_json_schema", None)
    if not callable(schema_builder):
        schema_builder = getattr(field_type, "schema", None)
    if callable(schema_builder):
        schema = schema_builder()

    definitions = (schema or {}).get("$defs", (schema or {}).get("definitions", {}))

    def value_for_schema(node: dict) -> object:
        if "$ref" in node:
            return value_for_schema(definitions[node["$ref"].rsplit("/", 1)[-1]])
        if "anyOf" in node:
            non_null = [candidate for candidate in node["anyOf"] if candidate.get("type") != "null"]
            return value_for_schema(non_null[0]) if non_null else None
        if "enum" in node:
            return node["enum"][0]
        node_type = node.get("type")
        if node_type == "object" or "properties" in node:
            properties = node.get("properties", {})
            return {
                name: value_for_schema(properties[name])
                for name in node.get("required", [])
                if name in properties
            }
        if node_type == "integer":
            return 1
        if node_type == "number":
            return 1.0
        if node_type == "boolean":
            return False
        if node_type == "array":
            return []
        if node.get("format") == "uuid":
            return "00000000-0000-0000-0000-000000000000"
        return "retired-test-id"

    return value_for_schema(schema) if schema else {}


def _request_path(app: FastAPI, route: APIRoute, fallback: str) -> str:
    """Resolve the path after FastAPI applies the router mount prefix."""
    for mounted_route in app.routes:
        if not isinstance(mounted_route, APIRoute):
            continue
        if mounted_route.endpoint is route.endpoint and set(mounted_route.methods or ()) & set(route.methods or ()):
            return getattr(mounted_route, "path_format", mounted_route.path)
    return fallback


def _request_parameters(route: APIRoute) -> dict[str, object]:
    parameters: dict[str, object] = {}

    def visit(dependant) -> None:
        for parameter in getattr(dependant, "query_params", []):
            if not getattr(parameter, "required", False):
                continue
            parameter_type = getattr(parameter, "type_", None)
            type_name = getattr(parameter_type, "__name__", "").lower()
            if "uuid" in type_name:
                value: object = "00000000-0000-0000-0000-000000000000"
            elif parameter_type is int:
                value = "1"
            elif parameter_type is float:
                value = "1.0"
            elif parameter_type is bool:
                value = "false"
            else:
                value = "retired-test-id"
            parameters.setdefault(parameter.alias, value)
        for dependency in getattr(dependant, "dependencies", []):
            visit(dependency)

    visit(route.dependant)
    return parameters


def _isolated_router_app(router) -> FastAPI:
    """Remount a router and override only its request dependencies for retirement checks."""
    retired_app = FastAPI()

    async def _fake_user() -> dict[str, str]:
        return {"id": "retired-test-user"}

    def collect_dependency_calls(dependant, dependency_calls: set[object]) -> None:
        if dependant is None:
            return
        call = getattr(dependant, "call", None)
        if call is not None:
            dependency_calls.add(call)
        for dependency in getattr(dependant, "dependencies", []):
            collect_dependency_calls(dependency, dependency_calls)

    # Include first so the dependency graph is the same one FastAPI will use
    # for the remounted routes. This deliberately avoids exercising production's
    # authentication gate while retaining the retired endpoint handlers.
    retired_app.include_router(router)

    dependency_calls: set[object] = set()
    for candidate_route in (*router.routes, *retired_app.routes):
        if isinstance(candidate_route, APIRoute):
            # The root dependant is the endpoint itself, not an auth
            # dependency. Collect both the original and remounted dependency
            # graphs because FastAPI may clone or normalize dependencies while
            # including a router.
            for dependency in getattr(candidate_route.dependant, "dependencies", []):
                collect_dependency_calls(dependency, dependency_calls)

    for dependency in getattr(router, "dependencies", []):
        call = getattr(dependency, "dependency", None)
        if call is not None:
            dependency_calls.add(call)

    # Ensure every remounted route resolves overrides from this isolated app,
    # rather than a dependency-overrides provider retained by the production
    # router. FastAPI may preserve the provider on the cloned APIRoute.
    retired_app.router.dependency_overrides_provider = retired_app
    for candidate_route in retired_app.routes:
        if isinstance(candidate_route, APIRoute):
            candidate_route.dependency_overrides_provider = retired_app

    for call in dependency_calls:
        retired_app.dependency_overrides[call] = _fake_user

    # Match the explicit C004 remount proof for the shared gateway permission
    # dependency. This keeps retirement assertions from failing closed on the
    # missing production Bearer token.
    from backend.dependencies.aix_gateway_decisions_service_dependencies import require_permission

    retired_app.dependency_overrides[require_permission("gateway_decisions:create")] = _fake_user

    return retired_app


def test_every_production_include_router_has_authority_matrix_entry() -> None:
    included = _included_router_names()
    classified = set(production.PUBLIC_ROUTER_AUTHORITY_MATRIX["routers"])

    assert included == classified
    assert len(production.PUBLIC_ROUTER_AUTHORITY_MATRIX["ready"]) <= 7


def test_objective_cycle_router_is_the_only_canonical_command_writer() -> None:
    routers = production.PUBLIC_ROUTER_AUTHORITY_MATRIX["routers"]
    canonical_writers = [
        name for name, authority in routers.items() if authority == "CANONICAL_COMMAND"
    ]

    assert canonical_writers == ["objective_cycle_router"]
    assert getattr(production, canonical_writers[0]) is production.objective_cycle_router


def test_mounted_retired_mutation_routes_return_http_410() -> None:
    async def exercise() -> None:
        retired_router_names = [
            name
            for name, authority in production.PUBLIC_ROUTER_AUTHORITY_MATRIX["routers"].items()
            if authority == "RETIRE"
        ]

        for router_name in retired_router_names:
            retired_router = getattr(production, router_name)
            retired_routes = _mutation_routes(retired_router)
            assert retired_routes, (
                f"production RETIRE router {router_name} must expose mutation routes"
            )

            # Exercise the retired router without production's authentication
            # dependencies. This verifies the retirement response itself rather
            # than the fail-closed missing-Bearer-token response.
            retired_app = _isolated_router_app(retired_router)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=retired_app),
                base_url="http://testserver",
            ) as client:
                for method, path, route in retired_routes:
                    response = await client.request(
                        method,
                        _request_path(retired_app, route, path),
                        headers={
                            "Authorization": "Bearer test",
                            "X-Tenant-ID": "t1",
                            "X-Actor-ID": "a1",
                        },
                        json=_request_payload(route),
                        params=_request_parameters(route),
                    )
                    assert response.status_code == 410, (
                        f"{method} {path} for {router_name} returned {response.status_code}: "
                        f"{response.text}"
                    )

    asyncio.run(exercise())


def test_archived_retired_mutation_routes_return_http_410() -> None:
    retired = production.PUBLIC_ROUTER_AUTHORITY_MATRIX["retired_mutation_routes"]
    assert retired

    for router_name, metadata in retired.items():
        assert metadata["status"] == "RETIRE"
        assert metadata["mounted"] is False
        assert metadata["expected_http_status"] == 410

        module = importlib.import_module(f"backend.routers.{router_name}")
        retired_router = module.router
        mutation_routes = _mutation_routes(retired_router)
        assert mutation_routes, f"No mutation routes found for retired router {router_name}"

        # Remount the archived router in an isolated app without its auth
        # dependency. The production app is intentionally not exercised here:
        # a missing Bearer token would fail closed before the retirement guard.
        retired_app = _isolated_router_app(retired_router)

        async def exercise() -> None:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=retired_app),
                base_url="http://testserver",
            ) as client:
                for method, path, route in mutation_routes:
                    response = await client.request(
                        method,
                        _request_path(retired_app, route, path),
                        headers={
                            "Authorization": "Bearer test",
                            "X-Tenant-ID": "t1",
                            "X-Actor-ID": "a1",
                        },
                        json=_request_payload(route),
                        params=_request_parameters(route),
                    )
                    assert response.status_code == metadata["expected_http_status"], (
                        f"{method} {path} for {router_name} returned "
                        f"{response.status_code}, expected HTTP 410"
                    )

        asyncio.run(exercise())


# ── I-T007: RUNTIME_CLASSIFICATION_TABLE contract tests ──────────────────────


_VALID_RUNTIME_CLASSES = frozenset(
    {"KEEP", "MERGE", "REWIRE", "REPLACE", "RETIRE", "EXPERIMENTAL_ONLY"}
)


def test_runtime_classification_table_is_well_formed() -> None:
    """Every runtime in RUNTIME_CLASSIFICATION_TABLE has a valid class and required fields."""
    from backend.main import RUNTIME_CLASSIFICATION_TABLE  # noqa: PLC0415

    table = RUNTIME_CLASSIFICATION_TABLE
    assert table.get("version") == 1, "RUNTIME_CLASSIFICATION_TABLE must have version==1"
    runtimes = table.get("runtimes", {})
    assert runtimes, "RUNTIME_CLASSIFICATION_TABLE.runtimes must not be empty"

    required_fields = {"class", "module", "entry", "rationale"}
    for name, entry in runtimes.items():
        klass = entry.get("class")
        assert klass in _VALID_RUNTIME_CLASSES, (
            f"Runtime '{name}' has invalid class '{klass}'. "
            f"Must be one of {_VALID_RUNTIME_CLASSES}."
        )
        missing = required_fields - set(entry.keys())
        assert not missing, (
            f"Runtime '{name}' is missing required fields: {missing}"
        )


def test_runtime_classification_table_objective_cycle_is_keep_and_canonical() -> None:
    """objective_cycle must be KEEP and its public_authority must be CANONICAL_COMMAND.
    This ensures the classification table cannot silently demote the canonical writer."""
    from backend.main import RUNTIME_CLASSIFICATION_TABLE  # noqa: PLC0415

    runtimes = RUNTIME_CLASSIFICATION_TABLE["runtimes"]
    assert "objective_cycle" in runtimes, (
        "RUNTIME_CLASSIFICATION_TABLE must contain 'objective_cycle'"
    )
    oc = runtimes["objective_cycle"]
    assert oc["class"] == "KEEP", (
        f"objective_cycle class must be KEEP, got '{oc['class']}'"
    )
    assert oc.get("public_authority") == "CANONICAL_COMMAND", (
        "objective_cycle public_authority must be CANONICAL_COMMAND"
    )


def test_runtime_classification_table_retire_entries_align_with_router_matrix() -> None:
    """Every runtime with class RETIRE that also has a public_authority must match
    the PUBLIC_ROUTER_AUTHORITY_MATRIX entry for the corresponding router."""
    from backend.main import PUBLIC_ROUTER_AUTHORITY_MATRIX, RUNTIME_CLASSIFICATION_TABLE  # noqa: PLC0415

    router_authority = PUBLIC_ROUTER_AUTHORITY_MATRIX["routers"]
    runtimes = RUNTIME_CLASSIFICATION_TABLE["runtimes"]

    # Build a lookup: module/entry string → router name in matrix
    retire_runtime_to_router = {
        "aix_mvp_product": "aix_mvp_product_router",
        "aix_orchestrator_v2": "aix_orchestrator_v2_router",
        "langgraph_orchestration": "langgraph_orchestration_router",
    }

    for runtime_name, expected_router in retire_runtime_to_router.items():
        entry = runtimes.get(runtime_name)
        if entry is None:
            continue
        if entry.get("class") != "RETIRE":
            continue
        # The retired_mutation_routes section or routers section must agree
        matrix_authority = (
            router_authority.get(expected_router)
            or PUBLIC_ROUTER_AUTHORITY_MATRIX
            .get("retired_mutation_routes", {})
            .get(expected_router, {})
            .get("status")
        )
        assert matrix_authority == "RETIRE", (
            f"Runtime '{runtime_name}' is classified RETIRE but corresponding router "
            f"'{expected_router}' has authority '{matrix_authority}' in "
            f"PUBLIC_ROUTER_AUTHORITY_MATRIX (expected RETIRE)."
        )
