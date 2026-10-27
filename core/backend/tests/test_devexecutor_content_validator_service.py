from pathlib import Path

from backend.services.devexecutor_content_validator_service import validate_step_content


def test_validate_step_content_rejects_body_fragment_for_replace_in_function() -> None:
    result = validate_step_content(
        tool="replace_in_function",
        args={
            "relative_path": "backend/services/foo.py",
            "replacement": "    x = 1\n    return x\n",
        },
    )

    assert result == "MUTATION_INVALID_FUNCTION_REPLACEMENT: missing_function_signature"


def test_validate_step_content_accepts_full_function_for_replace_in_function() -> None:
    assert (
        validate_step_content(
            tool="replace_in_function",
            args={
                "relative_path": "backend/services/foo.py",
                "replacement": "def run_task():\n    return {}\n",
            },
        )
        is None
    )


def test_validate_step_content_accepts_full_function_for_replace_function() -> None:
    assert (
        validate_step_content(
            tool="replace_function",
            args={
                "relative_path": "backend/services/foo.py",
                "replacement": "def run_task():\n    return {}\n",
            },
        )
        is None
    )


def test_validate_step_content_rejects_body_fragment_for_replace_function() -> None:
    result = validate_step_content(
        tool="replace_function",
        args={
            "relative_path": "backend/services/foo.py",
            "replacement": "    x = 1\n    return x\n",
        },
    )

    assert result == "MUTATION_INVALID_FUNCTION_REPLACEMENT: missing_function_signature"


def test_validate_step_content_rejects_placeholder_replace_function_body() -> None:
    result = validate_step_content(
        tool="replace_function",
        args={
            "relative_path": "backend/services/foo.py",
            "replacement": "def run_task():\n    # ...\n",
        },
    )

    assert result is not None
    assert result.startswith("PLANNER_INVALID_REPLACEMENT_CONTENT:")
    assert "# ..." in result


def test_validate_step_content_rejects_ellipsis_replace_function_body() -> None:
    result = validate_step_content(
        tool="replace_function",
        args={
            "relative_path": "backend/services/foo.py",
            "replacement": "def run_task():\n    ...\n",
        },
    )

    assert result is not None
    assert result.startswith("PLANNER_INVALID_REPLACEMENT_CONTENT:")
    assert "..." in result


def test_validate_step_content_accepts_body_fragment_for_insert_after_symbol() -> None:
    assert (
        validate_step_content(
            tool="insert_after_symbol",
            args={
                "relative_path": "backend/services/foo.py",
                "new_content": "    x = 1\n    return x\n",
            },
        )
        is None
    )


def test_validate_step_content_rejects_broken_body_fragment_syntax_for_insert_after_symbol() -> None:
    result = validate_step_content(
        tool="insert_after_symbol",
        args={
            "relative_path": "backend/services/foo.py",
            "new_content": "    x = (\n    return x\n",
        },
    )

    assert result is not None
    assert "CONTENT_SYNTAX_ERROR" in result


def test_validate_step_content_accepts_body_fragment_for_insert_at_anchor() -> None:
    assert (
        validate_step_content(
            tool="insert_at_anchor",
            args={
                "relative_path": "backend/services/foo.py",
                "new_content": "    x = 1\n    return x\n",
            },
        )
        is None
    )


def test_validate_step_content_rejects_broken_full_function_syntax_for_replace_in_function() -> None:
    result = validate_step_content(
        tool="replace_in_function",
        args={
            "relative_path": "backend/services/foo.py",
            "replacement": "def run_task(\n    return {}\n",
        },
    )

    assert result is not None
    assert "CONTENT_SYNTAX_ERROR" in result


def test_validate_step_content_accepts_top_level_write_file_content() -> None:
    assert (
        validate_step_content(
            tool="write_file",
            args={
                "relative_path": "backend/services/foo.py",
                "content": "def foo():\n    return 1\n",
            },
        )
        is None
    )


def test_validate_step_content_rejects_invalid_search_replace_blocks_for_edit_file() -> None:
    result = validate_step_content(
        tool="edit_file",
        args={
            "relative_path": "backend/services/foo.py",
            "search_replace_blocks": "notes only",
        },
    )

    assert result is not None
    assert result.startswith("CONTENT_SEARCH_REPLACE_INVALID:")


def test_validate_step_content_rejects_refactor_symbol_prose_payload() -> None:
    result = validate_step_content(
        tool="edit_symbol",
        args={
            "relative_path": "backend/services/foo.py",
            "mode": "refactor_symbol",
            "new_code": "Refactor this symbol to use one shared predicate.",
        },
    )

    assert result is not None
    assert result.startswith("CONTENT_REFACTOR_SYMBOL_PAYLOAD_INVALID:")


def test_validate_step_content_rejects_missing_module_scope_depends_import(tmp_path: Path) -> None:
    target = tmp_path / "backend" / "routers" / "codeintel_workspace_router.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "from fastapi import APIRouter, HTTPException, Query\n"
        "router = APIRouter()\n\n"
        "@router.get('/file')\n"
        "def read_workspace_file(path: str = Query(...)) -> dict:\n"
        "    return {'path': path}\n",
        encoding="utf-8",
    )

    result = validate_step_content(
        tool="replace_in_function",
        args={
            "relative_path": "backend/routers/codeintel_workspace_router.py",
            "replacement": (
                "def read_workspace_file(\n"
                "    path: str = Query(...),\n"
                "    current_user: dict = Depends(require_permission('read:workspace')),\n"
                ") -> dict:\n"
                "    return {'path': path}\n"
            ),
        },
        workspace_root=tmp_path,
    )

    assert result == "CONTENT_MISSING_MODULE_IMPORT: name 'Depends' requires module-scope import from fastapi"


def test_validate_step_content_accepts_depends_when_required_imports_exist(tmp_path: Path) -> None:
    target = tmp_path / "backend" / "routers" / "codeintel_workspace_router.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "from fastapi import APIRouter, Depends, HTTPException, Query, Request\n"
        "from backend.dependencies.codeintel_indexing_service_dependencies import (\n"
        "    enforce_no_tenant_header,\n"
        "    project_id_header,\n"
        "    require_permission,\n"
        "    resolve_project_context,\n"
        "    validate_tenant_access,\n"
        ")\n"
        "router = APIRouter()\n\n"
        "@router.get('/file')\n"
        "async def read_workspace_file(request: Request, path: str = Query(...)) -> dict:\n"
        "    return {'path': path}\n",
        encoding="utf-8",
    )

    result = validate_step_content(
        tool="replace_in_function",
        args={
            "relative_path": "backend/routers/codeintel_workspace_router.py",
            "replacement": (
                "async def read_workspace_file(\n"
                "    request: Request,\n"
                "    path: str = Query(...),\n"
                "    current_user: dict = Depends(require_permission('read:workspace')),\n"
                "    x_project_id: str | None = Depends(project_id_header),\n"
                ") -> dict:\n"
                "    enforce_no_tenant_header(request)\n"
                "    tenant_id = await validate_tenant_access(current_user)\n"
                "    project_id = resolve_project_context(header_project_id=x_project_id, required=True)\n"
                "    return {'path': path, 'tenant_id': tenant_id, 'project_id': project_id}\n"
            ),
        },
        workspace_root=tmp_path,
    )

    assert result is None
