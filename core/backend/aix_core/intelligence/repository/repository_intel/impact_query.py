"""C034: bounded impact query + context builder API (evidence manifest, not full files)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from backend.aix_core.intelligence.repository.repository_intel.dependency_graph import RepoGraph, impact_query
from backend.aix_core.intelligence.repository.repository_intel.symbol_index import LanguageAwareSymbolIndex
from backend.aix_core.intelligence.repository.repository_intel.test_linkage import require_test_coverage_or_gap


class ImpactAPIError(RuntimeError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


@dataclass
class ImpactQueryRequest:
    symbol: str | None = None
    module_node_id: str | None = None
    effect_path: str | None = None
    max_files: int = 20
    include_tests: bool = True


@dataclass
class EvidenceManifest:
    items: list[dict[str, Any]] = field(default_factory=list)
    policy: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"items": list(self.items), "policy": dict(self.policy)}


def build_impact_context(
    *,
    request: ImpactQueryRequest,
    symbol_index: LanguageAwareSymbolIndex | None = None,
    graph: RepoGraph | None = None,
    test_paths: list[str] | None = None,
) -> EvidenceManifest:
    if request.max_files <= 0:
        raise ImpactAPIError("INVALID_MAX_FILES")
    manifest = EvidenceManifest(
        policy={
            "max_files": request.max_files,
            "full_file_bodies": False,
            "returns": "evidence_manifest",
        }
    )
    if request.symbol and symbol_index is not None:
        q = symbol_index.query(request.symbol)
        for d in q["definitions"][: request.max_files]:
            manifest.items.append(
                {
                    "kind": "symbol_definition",
                    "path": d["path"],
                    "line": d["line"],
                    "content_sha256": d["content_sha256"],
                    "preview": None,
                }
            )
        for r in q["references"][: max(0, request.max_files - len(manifest.items))]:
            manifest.items.append(
                {
                    "kind": "symbol_reference",
                    "path": r["path"],
                    "line": r["line"],
                    "content_sha256": r["content_sha256"],
                    "preview": None,
                }
            )
    if request.module_node_id and graph is not None:
        impact = impact_query(graph, node_id=request.module_node_id, transitive=True)
        manifest.items.append({"kind": "impact", **impact})
    if request.include_tests and request.effect_path:
        coverage = require_test_coverage_or_gap(
            effect_path=request.effect_path,
            symbols=[request.symbol] if request.symbol else [],
            test_paths=test_paths or [],
        )
        manifest.items.append({"kind": "test_linkage", **coverage})
    # Never attach arbitrary full file contents.
    for item in manifest.items:
        if "content" in item or "full_text" in item:
            raise ImpactAPIError("FULL_FILE_FORBIDDEN")
    return manifest


__all__ = [
    "ImpactAPIError",
    "ImpactQueryRequest",
    "EvidenceManifest",
    "build_impact_context",
]
