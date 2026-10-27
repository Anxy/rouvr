"""Tests for P11 (repository intelligence extensions) and P12 (project memory)."""

from __future__ import annotations

import pytest


# ── P11: Cross-language dependency graph ──────────────────────────────────────

class TestCrossLanguageDependencyGraph:
    def test_index_js_imports(self):
        from backend.aix_core.intelligence.repository.repository_intel.dependency_graph import (
            RepoGraph, index_js_module, impact_query,
        )
        g = RepoGraph()
        index_js_module(g, path="src/app.ts", content="""
import { Router } from 'express';
import { UserService } from './services/user';
const db = require('pg');
""")
        assert "mod:express" in g.nodes
        assert "mod:./services/user" in g.nodes
        assert "mod:pg" in g.nodes
        edges = [e for e in g.edges if e.relation == "imports"]
        assert len(edges) == 3

    def test_index_php_imports(self):
        from backend.aix_core.intelligence.repository.repository_intel.dependency_graph import (
            RepoGraph, index_php_module,
        )
        g = RepoGraph()
        index_php_module(g, path="app/Services/UserService.php", content="""<?php
use App\\Models\\User;
use Illuminate\\Support\\Facades\\DB;
""")
        assert "mod:App.Models.User" in g.nodes
        assert "mod:Illuminate.Support.Facades.DB" in g.nodes
        edges = [e for e in g.edges if e.relation == "imports"]
        assert len(edges) == 2

    def test_index_module_dispatches_by_extension(self):
        from backend.aix_core.intelligence.repository.repository_intel.dependency_graph import (
            RepoGraph, index_module,
        )
        g = RepoGraph()
        index_module(g, path="utils.py", content="import os\n")
        index_module(g, path="app.tsx", content="import React from 'react';\n")
        index_module(g, path="service.php", content="use App\\Core;\n")
        assert "mod:os" in g.nodes
        assert "mod:react" in g.nodes
        assert "mod:App.Core" in g.nodes

    def test_python_call_graph(self):
        from backend.aix_core.intelligence.repository.repository_intel.dependency_graph import (
            RepoGraph, index_python_calls,
        )
        g = RepoGraph()
        index_python_calls(g, path="main.py", content="""
result = process_data(items)
db.save(result)
validate_schema(result)
""")
        call_edges = [e for e in g.edges if e.relation == "calls"]
        callees = {e.dst for e in call_edges}
        assert "call:process_data" in callees
        assert "call:db.save" in callees
        assert "call:validate_schema" in callees

    def test_python_calls_skip_builtins(self):
        from backend.aix_core.intelligence.repository.repository_intel.dependency_graph import (
            RepoGraph, index_python_calls,
        )
        g = RepoGraph()
        index_python_calls(g, path="main.py", content="""
x = len(items)
y = str(value)
z = isinstance(x, int)
custom_func(x)
""")
        call_edges = [e for e in g.edges if e.relation == "calls"]
        callees = {e.dst for e in call_edges}
        assert "call:len" not in callees
        assert "call:str" not in callees
        assert "call:isinstance" not in callees
        assert "call:custom_func" in callees

    def test_impact_query_cross_language(self):
        from backend.aix_core.intelligence.repository.repository_intel.dependency_graph import (
            RepoGraph, index_module, impact_query,
        )
        g = RepoGraph()
        index_module(g, path="shared/utils.py", content="")
        index_module(g, path="app.ts", content="import { helper } from '../shared/utils';\n")
        result = impact_query(g, node_id="mod:../shared/utils")
        assert "mod:app.ts" in result["transitive_consumers"]


# ── P12: Project memory ──────────────────────────────────────────────────────

class TestProjectMemory:
    def test_record_and_query(self):
        from backend.aix_core.runtime.executor.project_memory import (
            ProjectMemoryStore, ProjectMemoryEntry,
        )
        store = ProjectMemoryStore()
        entry = ProjectMemoryEntry(
            entry_id="arch-1", tenant_id="t1", project_id="p1",
            kind="architecture_decision",
            title="Use event sourcing for state",
            content="All state changes go through the event ledger.",
            provenance="user_declared",
        )
        store.record(entry)
        results = store.query("t1", "p1")
        assert len(results) == 1
        assert results[0].title == "Use event sourcing for state"

    def test_query_by_kind(self):
        from backend.aix_core.runtime.executor.project_memory import (
            ProjectMemoryStore, ProjectMemoryEntry,
        )
        store = ProjectMemoryStore()
        store.record(ProjectMemoryEntry(
            entry_id="arch-1", tenant_id="t1", project_id="p1",
            kind="architecture_decision", title="AD1", content="...", provenance="user",
        ))
        store.record(ProjectMemoryEntry(
            entry_id="conv-1", tenant_id="t1", project_id="p1",
            kind="coding_convention", title="CC1", content="...", provenance="auto_detected",
        ))
        arch = store.query("t1", "p1", kind="architecture_decision")
        assert len(arch) == 1
        assert arch[0].entry_id == "arch-1"

    def test_supersede_entry(self):
        from backend.aix_core.runtime.executor.project_memory import (
            ProjectMemoryStore, ProjectMemoryEntry,
        )
        store = ProjectMemoryStore()
        store.record(ProjectMemoryEntry(
            entry_id="dec-1", tenant_id="t1", project_id="p1",
            kind="architecture_decision", title="V1", content="old approach", provenance="user",
        ))
        store.record(ProjectMemoryEntry(
            entry_id="dec-2", tenant_id="t1", project_id="p1",
            kind="architecture_decision", title="V2", content="new approach", provenance="user",
        ))
        store.supersede("t1", "p1", "dec-1", "dec-2")
        active = store.query("t1", "p1", active_only=True)
        assert len(active) == 1
        assert active[0].entry_id == "dec-2"
        old = store.get("t1", "p1", "dec-1")
        assert old is not None
        assert old.status == "SUPERSEDED"
        assert old.superseded_by == "dec-2"

    def test_project_isolation(self):
        from backend.aix_core.runtime.executor.project_memory import (
            ProjectMemoryStore, ProjectMemoryEntry,
        )
        store = ProjectMemoryStore()
        store.record(ProjectMemoryEntry(
            entry_id="e1", tenant_id="t1", project_id="proj-A",
            kind="project_note", title="A note", content="...", provenance="user",
        ))
        store.record(ProjectMemoryEntry(
            entry_id="e2", tenant_id="t1", project_id="proj-B",
            kind="project_note", title="B note", content="...", provenance="user",
        ))
        assert len(store.query("t1", "proj-A")) == 1
        assert len(store.query("t1", "proj-B")) == 1
        assert store.query("t1", "proj-A")[0].title == "A note"

    def test_context_capsule_format(self):
        from backend.aix_core.runtime.executor.project_memory import (
            ProjectMemoryStore, ProjectMemoryEntry,
        )
        store = ProjectMemoryStore()
        store.record(ProjectMemoryEntry(
            entry_id="e1", tenant_id="t1", project_id="p1",
            kind="coding_convention", title="4-space indent",
            content="Use 4-space indentation", provenance="auto_detected",
            confidence=0.9, tags=("style",),
        ))
        capsule = store.for_context_capsule("t1", "p1")
        assert len(capsule) == 1
        assert capsule[0]["title"] == "4-space indent"
        assert capsule[0]["confidence"] == 0.9
        assert "style" in capsule[0]["tags"]

    def test_invalid_kind_rejected(self):
        from backend.aix_core.runtime.executor.project_memory import (
            ProjectMemoryStore, ProjectMemoryEntry,
        )
        store = ProjectMemoryStore()
        with pytest.raises(ValueError, match="invalid memory kind"):
            store.record(ProjectMemoryEntry(
                entry_id="e1", tenant_id="t1", project_id="p1",
                kind="invalid_kind",  # type: ignore[arg-type]
                title="Bad", content="...", provenance="user",
            ))


class TestConventionDetection:
    def test_detects_4space_indentation(self):
        from backend.aix_core.runtime.executor.project_memory import detect_conventions

        files = {
            f"mod{i}.py": "def foo():\n    return 1\n    pass\n" * 5
            for i in range(5)
        }
        conventions = detect_conventions(files)
        indent_conv = [c for c in conventions if "indent" in c.entry_id.lower()]
        assert len(indent_conv) == 1
        assert "4spaces" in indent_conv[0].entry_id

    def test_detects_type_hints(self):
        from backend.aix_core.runtime.executor.project_memory import detect_conventions

        files = {
            f"mod{i}.py": "def foo(x: int) -> str:\n    return str(x)\n"
            for i in range(6)
        }
        conventions = detect_conventions(files)
        hint_conv = [c for c in conventions if "type_hint" in c.entry_id.lower()]
        assert len(hint_conv) == 1

    def test_detects_pydantic_preference(self):
        from backend.aix_core.runtime.executor.project_memory import detect_conventions

        files = {}
        for i in range(4):
            files[f"models{i}.py"] = "from pydantic import BaseModel\nclass Foo(BaseModel): pass\n"
        files["legacy.py"] = "from dataclasses import dataclass\n@dataclass\nclass Bar: pass\n"
        conventions = detect_conventions(files)
        model_conv = [c for c in conventions if "data_models" in c.entry_id.lower()]
        assert len(model_conv) == 1
        assert "pydantic" in model_conv[0].entry_id
