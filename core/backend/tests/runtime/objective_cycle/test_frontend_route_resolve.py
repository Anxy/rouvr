from pathlib import Path

from backend.aix_core.runtime.executor.frontend_route_resolve import (
    extract_web_routes,
    resolve_app_router_page,
    resolve_page_from_objective,
)


def test_extract_web_routes_strips_query() -> None:
    text = "update /objectives/new?new=1 in aix please"
    assert extract_web_routes(text) == ["/objectives/new"]


def test_resolve_objectives_new_page_under_aix_frontend() -> None:
    root = Path(r"D:\v2.1\AIX\hyeve-aix-service\AIX-Frontend")
    if not root.is_dir():
        return
    page = resolve_app_router_page(root, "/objectives/new")
    assert page is not None
    assert page.replace("\\", "/").endswith("objectives/new/page.tsx")
    assert "(admin)" in page or page.startswith("src/app/")


def test_resolve_page_from_objective_uses_route() -> None:
    root = Path(r"D:\v2.1\AIX\hyeve-aix-service\AIX-Frontend")
    if not root.is_dir():
        return
    objective = (
        'update /objectives/new?new=1 under '
        r'"D:\v2.1\AIX\hyeve-aix-service\AIX-Frontend\src"'
    )
    page = resolve_page_from_objective(root, objective)
    assert page is not None
    assert page.endswith("objectives/new/page.tsx")
