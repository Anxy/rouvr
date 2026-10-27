"""Compatibility shim — real implementation moved to backend.legacy.langgraph.router.

Reorg batch 1 (2026-08-16): LangGraph is confirmed unmounted/dead (see
BACKEND_REORGANIZATION_MANIFEST.md §9b). This file is kept ONLY because
aix_core.control_plane_reachability.generator.list_router_modules() discovers
routers by globbing routers/*_router.py on disk, not by import graph — removing
this file entirely would silently drop the retired component from the
reachability manifest and break tests/aix_core/control_plane_reachability/
test_c005_control_plane_harness.py. Not a production import path; no code
outside the reachability generator's directory scan should reference this
module. See BACKEND_COMPATIBILITY_SHIMS.md.
"""

from backend.legacy.langgraph.router import *  # noqa: F401,F403
from backend.legacy.langgraph.router import (  # noqa: F401
    LANGGRAPH_RETIREMENT_CODE,
    LANGGRAPH_WRITES_RETIRED,
    LangGraphRunRequest,
    router,
    run_langgraph_chain,
    settings,
)
