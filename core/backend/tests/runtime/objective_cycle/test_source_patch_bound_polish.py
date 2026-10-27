from backend.aix_core.runtime.objective.discovery import DiscoveryReport
from backend.aix_core.runtime.executor.source_patch_codegen import (
    _apply_snippet_replacements,
    _edits_from_model_payload,
    _resolve_edit_path,
)


def test_resolve_edit_path_accepts_basename_and_nested_forms() -> None:
    known = {"bivr-generator.html", "styles/app.css"}
    assert _resolve_edit_path("bivr-generator.html", known) == "bivr-generator.html"
    assert _resolve_edit_path("./bivr-generator.html", known) == "bivr-generator.html"
    assert _resolve_edit_path(r"D:\v2.1\Visions\bivr-generator.html", known) == "bivr-generator.html"
    assert _resolve_edit_path("styles/app.css", known) == "styles/app.css"
    assert _resolve_edit_path("missing.html", known) is None


def test_snippet_replacements_apply_once_and_fail_closed() -> None:
    src = "<h1 class=\"text-4xl\">BIVR</h1>\n<p>ok</p>\n"
    out = _apply_snippet_replacements(
        src,
        [{"old": 'class="text-4xl"', "new": 'class="text-5xl tracking-tight"'}],
    )
    assert out is not None
    assert 'class="text-5xl tracking-tight"' in out
    assert _apply_snippet_replacements(src, [{"old": "MISSING", "new": "X"}]) is None
    assert _apply_snippet_replacements(src, []) is None


def test_edits_from_model_payload_accepts_replacements_for_bound_html() -> None:
    discovery = DiscoveryReport(
        config_files={
            "bivr-generator.html": (
                '<p class="text-4xl font-black">BIVR Code Generator</p>\n'
                '<button class="h-10 px-4">Use Sample DDL</button>\n'
            )
        }
    )
    parsed = {
        "edits": [
            {
                "path": "bivr-generator.html",
                "replacements": [
                    {
                        "old": 'class="text-4xl font-black"',
                        "new": 'class="text-5xl font-semibold tracking-tight"',
                    }
                ],
                "reason": "stronger hierarchy",
            }
        ],
        "no_files_identified": False,
        "reason": "",
    }
    edits, reason = _edits_from_model_payload(parsed, discovery=discovery)
    assert reason == ""
    assert len(edits) == 1
    assert edits[0].path == "bivr-generator.html"
    assert "text-5xl font-semibold" in edits[0].new_content
    assert "Use Sample DDL" in edits[0].new_content


def test_edits_from_model_payload_can_create_new_test_file() -> None:
    discovery = DiscoveryReport(config_files={"backend/main.py": "app = FastAPI()\n"})
    parsed = {
        "edits": [
            {
                "path": "backend/tests/test_public_router_authority_matrix.py",
                "new_content": "def test_one_canonical_writer():\n    assert True\n",
                "reason": "add focused tests",
            }
        ],
        "no_files_identified": False,
        "reason": "",
    }
    edits, reason = _edits_from_model_payload(parsed, discovery=discovery)
    assert reason == ""
    assert len(edits) == 1
    assert edits[0].path == "backend/tests/test_public_router_authority_matrix.py"
    assert "test_one_canonical_writer" in edits[0].new_content


def test_edits_from_model_payload_last_write_wins_for_same_path() -> None:
    discovery = DiscoveryReport(config_files={"backend/main.py": "app = FastAPI()\n"})
    parsed = {
        "edits": [
            {
                "path": "backend/tests/test_public_router_authority_matrix.py",
                "new_content": "from __future__ import annotations\nFIRST\n",
                "reason": "first",
            },
            {
                "path": "backend/tests/test_public_router_authority_matrix.py",
                "new_content": "from __future__ import annotations\nSECOND\n",
                "reason": "second",
            },
        ],
        "no_files_identified": False,
        "reason": "",
    }
    edits, _reason = _edits_from_model_payload(parsed, discovery=discovery)
    assert len(edits) == 1
    assert edits[0].new_content == "from __future__ import annotations\nSECOND\n"


def test_edits_from_model_payload_rejects_invented_non_test_path() -> None:
    discovery = DiscoveryReport(config_files={"backend/main.py": "app = FastAPI()\n"})
    parsed = {
        "edits": [
            {
                "path": "backend/secret_new_module.py",
                "new_content": "x = 1\n",
                "reason": "invent",
            }
        ],
        "no_files_identified": False,
        "reason": "",
    }
    edits, _reason = _edits_from_model_payload(parsed, discovery=discovery)
    assert edits == []
