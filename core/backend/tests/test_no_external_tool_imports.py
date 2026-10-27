"""F2-P4 — external_tool_registry must be deleted; no live imports."""

from __future__ import annotations

from pathlib import Path


def test_no_external_tool_registry_module() -> None:
    backend = Path(__file__).resolve().parents[1]
    path = backend / "aix_core" / "runtime" / "tools" / "external_tool_registry.py"
    assert not path.exists(), f"retired module still present: {path}"


def test_no_external_tool_registry_imports() -> None:
    backend = Path(__file__).resolve().parents[1]
    allow_name_mentions = {
        "tests/test_no_external_tool_imports.py",
        "tests\\test_no_external_tool_imports.py",
        "aix_core/runtime/tools/__init__.py",
        "aix_core\\runtime\\tools\\__init__.py",
        "tests/runtime/objective_cycle/test_p15_p16_p17_p18.py",
        "tests\\runtime\\objective_cycle\\test_p15_p16_p17_p18.py",
    }
    offenders: list[str] = []
    for path in backend.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        rel = str(path.relative_to(backend))
        if rel in allow_name_mentions:
            # Retirement docs / retirement assertion tests may name the module.
            text = path.read_text(encoding="utf-8", errors="replace")
            if "import" in text and "external_tool_registry" in text and "importlib" not in text:
                if "from backend.aix_core.runtime.tools.external_tool_registry" in text:
                    offenders.append(rel)
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if "external_tool_registry" in text:
            offenders.append(rel)
    assert offenders == [], f"external_tool_registry references remain: {offenders}"
