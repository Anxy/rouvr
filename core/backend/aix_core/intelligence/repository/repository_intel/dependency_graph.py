"""C031: dependency, API, route, and event graph + impact query."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal


class GraphError(RuntimeError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


NodeKind = Literal["module", "route", "api", "event_producer", "event_consumer"]


@dataclass
class GraphNode:
    node_id: str
    kind: NodeKind
    label: str
    path: str | None = None


@dataclass
class GraphEdge:
    src: str
    dst: str
    relation: str  # imports | calls | publishes | consumes | mounts


@dataclass
class RepoGraph:
    nodes: dict[str, GraphNode] = field(default_factory=dict)
    edges: list[GraphEdge] = field(default_factory=list)

    def add_node(self, node: GraphNode) -> None:
        self.nodes[node.node_id] = node

    def add_edge(self, edge: GraphEdge) -> None:
        if edge.src not in self.nodes or edge.dst not in self.nodes:
            raise GraphError("UNKNOWN_GRAPH_NODE", f"{edge.src}->{edge.dst}")
        self.edges.append(edge)


_PY_IMPORT = re.compile(r"^(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))")
_JS_IMPORT = re.compile(
    r"""(?:import\s+.*?\s+from\s+['\"]([^'\"]+)['\"]"""
    r"""|require\(\s*['\"]([^'\"]+)['\"]\s*\))""",
)
_PHP_USE = re.compile(r"^use\s+([\w\\]+)")
_ROUTE = re.compile(r"""@(?:app|router)\.(get|post|put|patch|delete)\(\s*['\"]([^'\"]+)['\"]""")
_EVENT_PUB = re.compile(r"""(?:emit|publish|produce)\(\s*['\"]([^'\"]+)['\"]""")
_EVENT_SUB = re.compile(r"""(?:on|subscribe|consume)\(\s*['\"]([^'\"]+)['\"]""")
_PY_CALL = re.compile(r"(?<!\w)([\w.]+)\s*\(")


def _ensure_module(graph: RepoGraph, mod_id: str, label: str, path: str | None = None) -> None:
    if mod_id not in graph.nodes:
        graph.add_node(GraphNode(node_id=mod_id, kind="module", label=label, path=path))


def _index_common(graph: RepoGraph, mod_id: str, line: str, path: str) -> None:
    """Index routes and event pub/sub from any language."""
    for rm in _ROUTE.finditer(line):
        route_id = f"route:{rm.group(2)}:{rm.group(1).upper()}"
        graph.add_node(GraphNode(node_id=route_id, kind="route", label=rm.group(2), path=path))
        graph.add_edge(GraphEdge(src=mod_id, dst=route_id, relation="mounts"))
    for pm in _EVENT_PUB.finditer(line):
        eid = f"event:{pm.group(1)}:producer"
        graph.add_node(GraphNode(node_id=eid, kind="event_producer", label=pm.group(1), path=path))
        graph.add_edge(GraphEdge(src=mod_id, dst=eid, relation="publishes"))
    for sm in _EVENT_SUB.finditer(line):
        eid = f"event:{sm.group(1)}:consumer"
        graph.add_node(GraphNode(node_id=eid, kind="event_consumer", label=sm.group(1), path=path))
        graph.add_edge(GraphEdge(src=mod_id, dst=eid, relation="consumes"))


def index_python_module(graph: RepoGraph, *, path: str, content: str) -> None:
    mod_id = f"mod:{path}"
    _ensure_module(graph, mod_id, path, path)
    for line in content.splitlines():
        m = _PY_IMPORT.match(line.strip())
        if m:
            target = (m.group(1) or m.group(2) or "").split(".")[0]
            if not target:
                continue
            dep_id = f"mod:{target}"
            _ensure_module(graph, dep_id, target)
            graph.add_edge(GraphEdge(src=mod_id, dst=dep_id, relation="imports"))
        _index_common(graph, mod_id, line, path)


def index_python_calls(graph: RepoGraph, *, path: str, content: str) -> None:
    """Populate CALL edges from Python function/method calls."""
    mod_id = f"mod:{path}"
    _ensure_module(graph, mod_id, path, path)
    _skip = frozenset({"if", "for", "while", "return", "print", "len", "str", "int",
                        "float", "bool", "list", "dict", "set", "tuple", "type",
                        "range", "enumerate", "zip", "map", "filter", "sorted",
                        "isinstance", "issubclass", "hasattr", "getattr", "setattr",
                        "super", "property", "staticmethod", "classmethod"})
    seen: set[str] = set()
    for line in content.splitlines():
        for m in _PY_CALL.finditer(line.strip()):
            callee = m.group(1)
            base = callee.split(".")[0]
            if base in _skip or callee in seen:
                continue
            seen.add(callee)
            call_id = f"call:{callee}"
            if call_id not in graph.nodes:
                graph.add_node(GraphNode(node_id=call_id, kind="module", label=callee))
            graph.add_edge(GraphEdge(src=mod_id, dst=call_id, relation="calls"))


def index_js_module(graph: RepoGraph, *, path: str, content: str) -> None:
    """Index JS/TS import/require statements."""
    mod_id = f"mod:{path}"
    _ensure_module(graph, mod_id, path, path)
    for line in content.splitlines():
        for m in _JS_IMPORT.finditer(line):
            target = m.group(1) or m.group(2) or ""
            if not target:
                continue
            dep_id = f"mod:{target}"
            _ensure_module(graph, dep_id, target)
            graph.add_edge(GraphEdge(src=mod_id, dst=dep_id, relation="imports"))
        _index_common(graph, mod_id, line, path)


def index_php_module(graph: RepoGraph, *, path: str, content: str) -> None:
    """Index PHP use statements as import edges."""
    mod_id = f"mod:{path}"
    _ensure_module(graph, mod_id, path, path)
    for line in content.splitlines():
        m = _PHP_USE.match(line.strip())
        if m:
            fqcn = m.group(1).replace("\\", ".").strip(".")
            dep_id = f"mod:{fqcn}"
            _ensure_module(graph, dep_id, fqcn)
            graph.add_edge(GraphEdge(src=mod_id, dst=dep_id, relation="imports"))
        _index_common(graph, mod_id, line, path)


def index_module(graph: RepoGraph, *, path: str, content: str) -> None:
    """Language-dispatched module indexer."""
    lower = path.lower()
    if lower.endswith(".py"):
        index_python_module(graph, path=path, content=content)
        index_python_calls(graph, path=path, content=content)
    elif lower.endswith((".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")):
        index_js_module(graph, path=path, content=content)
    elif lower.endswith(".php"):
        index_php_module(graph, path=path, content=content)
    else:
        mod_id = f"mod:{path}"
        _ensure_module(graph, mod_id, path, path)


def impact_query(graph: RepoGraph, *, node_id: str, transitive: bool = True) -> dict[str, Any]:
    if node_id not in graph.nodes:
        raise GraphError("UNKNOWN_GRAPH_NODE", node_id)
    direct = [e.dst for e in graph.edges if e.src == node_id]
    # Also consumers that depend on this node.
    inbound = [e.src for e in graph.edges if e.dst == node_id]
    consumers = set(inbound)
    if transitive:
        frontier = list(inbound)
        seen = set(inbound)
        adj_rev: dict[str, list[str]] = {}
        for e in graph.edges:
            adj_rev.setdefault(e.dst, []).append(e.src)
        while frontier:
            cur = frontier.pop()
            for pred in adj_rev.get(cur, []):
                if pred not in seen:
                    seen.add(pred)
                    consumers.add(pred)
                    frontier.append(pred)
    return {
        "node_id": node_id,
        "direct_dependencies": sorted(set(direct)),
        "direct_consumers": sorted(set(inbound)),
        "transitive_consumers": sorted(consumers),
    }


__all__ = [
    "GraphError",
    "NodeKind",
    "GraphNode",
    "GraphEdge",
    "RepoGraph",
    "index_python_module",
    "index_python_calls",
    "index_js_module",
    "index_php_module",
    "index_module",
    "impact_query",
]
