from __future__ import annotations

import argparse
import json
from importlib import metadata
from importlib.util import find_spec
from typing import Any


def _package_version(name: str) -> str | None:
    try:
        return metadata.version(name)
    except Exception:
        return None


def _check_temporal() -> dict[str, Any]:
    installed = find_spec("temporalio") is not None
    return {"installed": installed, "version": _package_version("temporalio")}


def _check_langgraph() -> dict[str, Any]:
    installed = find_spec("langgraph") is not None
    if not installed:
        return {"installed": False, "version": None, "graph_compile_ok": False}
    try:
        from langgraph.graph import END, StateGraph

        graph = StateGraph(dict)
        graph.add_node("start", lambda state: state)
        graph.set_entry_point("start")
        graph.add_edge("start", END)
        graph.compile()
        return {"installed": True, "version": _package_version("langgraph"), "graph_compile_ok": True}
    except Exception as exc:
        return {
            "installed": True,
            "version": _package_version("langgraph"),
            "graph_compile_ok": False,
            "error": str(exc),
        }


def _check_haystack() -> dict[str, Any]:
    installed = find_spec("haystack") is not None
    if not installed:
        return {"installed": False, "version": None, "pipeline_init_ok": False}
    try:
        from haystack import Pipeline

        pipeline = Pipeline()
        return {
            "installed": True,
            "version": _package_version("haystack-ai"),
            "pipeline_init_ok": pipeline is not None,
        }
    except Exception as exc:
        return {
            "installed": True,
            "version": _package_version("haystack-ai"),
            "pipeline_init_ok": False,
            "error": str(exc),
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify Temporal/LangGraph/Haystack runtime availability for AIX.")
    parser.add_argument("--output", default="", help="Optional path to write JSON report.")
    args = parser.parse_args()

    report = {
        "temporal": _check_temporal(),
        "langgraph": _check_langgraph(),
        "haystack": _check_haystack(),
    }
    report["ok"] = all(
        [
            bool(report["temporal"].get("installed")),
            bool(report["langgraph"].get("installed")) and bool(report["langgraph"].get("graph_compile_ok")),
            bool(report["haystack"].get("installed")) and bool(report["haystack"].get("pipeline_init_ok")),
        ]
    )
    payload = json.dumps(report, indent=2)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(payload + "\n")
    print(payload)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
