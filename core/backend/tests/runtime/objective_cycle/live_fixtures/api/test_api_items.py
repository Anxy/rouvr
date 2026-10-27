"""Focused tests for the live-fixture items API."""

from __future__ import annotations

import pytest

from backend.tests.runtime.objective_cycle.live_fixtures.api import app, schemas, service


@pytest.fixture(autouse=True)
def _reset_store() -> None:
    service.reset_store()


@pytest.mark.asyncio
async def test_create_and_list_items() -> None:
    status, created = await app.call_app(
        "POST", "/items", body={"title": "alpha", "body": "one"}
    )
    assert status == 201
    assert created["title"] == "alpha"
    assert created["archived"] is False

    status, listed = await app.call_app("GET", "/items")
    assert status == 200
    assert len(listed) == 1
    assert listed[0]["id"] == created["id"]


@pytest.mark.asyncio
async def test_get_item_by_id() -> None:
    _, created = await app.call_app("POST", "/items", body={"title": "beta"})
    status, loaded = await app.call_app("GET", f"/items/{created['id']}")
    assert status == 200
    assert loaded["title"] == "beta"


@pytest.mark.asyncio
async def test_archive_item_hides_from_default_list() -> None:
    """S11 acceptance: POST/PUT archive endpoint excludes item from default list."""
    _, created = await app.call_app("POST", "/items", body={"title": "gamma"})
    item_id = int(created["id"])

    status, archived = await app.call_app("POST", f"/items/{item_id}/archive")
    assert status == 200
    assert archived["id"] == item_id
    assert archived["archived"] is True

    status, listed = await app.call_app("GET", "/items")
    assert status == 200
    assert listed == []

    status, listed_all = await app.call_app(
        "GET", "/items", query="include_archived=1"
    )
    assert status == 200
    assert len(listed_all) == 1
    assert listed_all[0]["archived"] is True

    status, loaded = await app.call_app("GET", f"/items/{item_id}")
    assert status == 200
    assert loaded["archived"] is True



def test_retired_mutation_routes_fail_closed() -> None:
    for method, path in (
        ("POST", "/items"),
        ("POST", "/items/1/archive"),
    ):
        with pytest.raises(PermissionError):
            schemas.assert_mutation_route_allowed(method, path)


def test_objective_cycle_remains_the_live_writer() -> None:
    route = schemas.assert_mutation_route_allowed(
        "POST", "/aix/objectives/objective-1/cycles"
    )
    assert route["authority"] == "CANONICAL_COMMAND"
    assert schemas.ENDPOINT_AUTHORITY_MATRIX["ready_follow_on_tasks"]
