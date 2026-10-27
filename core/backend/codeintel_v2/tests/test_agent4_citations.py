"""Wave 4 Agent 4 acceptance tests: citation schemas + strict client.

Run from ``hyeve-aix-service/``::

    pytest backend/codeintel_v2/tests/ -q
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from backend.clients.codeintel_v2_query_client import (
    ERROR_CONNECTION,
    ERROR_DISABLED,
    ERROR_PAYLOAD_SHAPE,
    ERROR_POSTURE_REJECTED,
    ERROR_TIMEOUT,
    ERROR_TOKEN_MISSING,
    CitationSet,
    CodeIntelV2QueryClient,
    http_status_error,
)
from backend.codeintel_v2.citations import (
    Citation,
    CodeIntelSnapshot,
    Freshness,
    FreshnessRecord,
    citation_to_dict,
    coerce_freshness,
)
from backend.codeintel_v2.file_meta import FileMeta, SymbolMeta
from backend.hyeve_aix_service.config import settings

_THIS_DIR = Path(__file__).resolve().parent
_PACKAGE_DIR = _THIS_DIR.parent
MODULE_FILES = [
    _PACKAGE_DIR / "__init__.py",
    _PACKAGE_DIR / "file_meta.py",
    _PACKAGE_DIR / "citations.py",
    Path(__file__).resolve().parents[2]
    / "clients"
    / "codeintel_v2_query_client.py",
]

_STALE_FRESHNESS = {
    "repo_id": "repo-a",
    "commit_sha": "abc123",
    "snapshot_id": "snap-1",
    "workspace_head": "abc123",
    "freshness": "stale",
    "reason": "workspace moved ahead",
}
_CURRENT_FRESHNESS = dict(_STALE_FRESHNESS, freshness="current")


def _good_item(**overrides: Any) -> dict[str, Any]:
    item: dict[str, Any] = {
        "path": "svc/orders.py",
        "line_range": [10, 24],
        "snapshot_id": "snap-1",
        "commit_sha": "abc123",
        "confidence": 0.82,
        "source": "codeintel_v2",
        "freshness": dict(_CURRENT_FRESHNESS),
    }
    item.update(overrides)
    return item


# ---------------------------------------------------------------- file_meta


def test_file_meta_round_trip() -> None:
    meta = FileMeta(
        path="svc/orders.py",
        line_range=(10, 24),
        snapshot_id="snap-1",
        commit_sha="abc123",
    )
    assert meta.to_dict() == {
        "path": "svc/orders.py",
        "line_range": [10, 24],
        "snapshot_id": "snap-1",
        "commit_sha": "abc123",
    }


def test_symbol_meta_round_trip() -> None:
    symbol = SymbolMeta(name="place_order", kind="function")
    assert symbol.to_dict() == {"name": "place_order", "kind": "function"}


@pytest.mark.parametrize(
    "line_range", [(0, 1), (2, 1), ("a", "b"), (1,), (1, 2, 3), (True, 2), None]
)
def test_file_meta_rejects_bad_line_range(line_range: Any) -> None:
    with pytest.raises(ValueError):
        FileMeta(
            path="svc/orders.py",
            line_range=line_range,
            snapshot_id="snap-1",
            commit_sha="abc123",
        )


def test_file_meta_has_no_filesystem_surface() -> None:
    source = (_PACKAGE_DIR / "file_meta.py").read_text(encoding="utf-8")
    for probe in (
        "os.",
        "glob",
        "scandir",
        "listdir",
        "rglob",
        "pathlib",
        "open(",
        "walk",
    ):
        assert probe not in source


# ---------------------------------------------------------------- citations


def test_citation_valid_and_serializes() -> None:
    citation = Citation(
        path="svc/orders.py",
        line_range=(10, 24),
        snapshot_id="snap-1",
        commit_sha="abc123",
        confidence=0.82,
        source="codeintel_v2",
        freshness=dict(_CURRENT_FRESHNESS),
    )
    assert citation.confidence == pytest.approx(0.82)
    assert isinstance(citation.freshness, FreshnessRecord)
    assert not citation.is_stale
    wire = citation_to_dict(citation)
    assert wire["path"] == "svc/orders.py"
    assert wire["line_range"] == [10, 24]
    assert wire["freshness"]["freshness"] == "current"


@pytest.mark.parametrize("field", ["path", "snapshot_id", "commit_sha"])
@pytest.mark.parametrize("bad", [None, "", "   ", "unknown", "UNKNOWN", " Unknown "])
def test_citation_rejects_schemaless_input(field: str, bad: Any) -> None:
    kwargs: dict[str, Any] = {
        "path": "svc/orders.py",
        "line_range": (10, 24),
        "snapshot_id": "snap-1",
        "commit_sha": "abc123",
        "confidence": 0.5,
    }
    kwargs[field] = bad
    with pytest.raises(ValueError):
        Citation(**kwargs)


@pytest.mark.parametrize(
    "bad", [-0.1, 1.5, 100, float("nan"), float("inf"), "0.9", None, True]
)
def test_citation_rejects_out_of_range_confidence(bad: Any) -> None:
    with pytest.raises(ValueError):
        Citation(
            path="svc/orders.py",
            line_range=(10, 24),
            snapshot_id="snap-1",
            commit_sha="abc123",
            confidence=bad,
        )


@pytest.mark.parametrize("good", [0.0, 1.0, 1, 0])
def test_citation_accepts_confidence_bounds(good: Any) -> None:
    citation = Citation(
        path="svc/orders.py",
        line_range=(10, 24),
        snapshot_id="snap-1",
        commit_sha="abc123",
        confidence=good,
    )
    assert 0.0 <= citation.confidence <= 1.0


def test_local_protocols_match_frozen_shapes() -> None:
    assert set(CodeIntelSnapshot.__annotations__) == {
        "repo_id",
        "commit_sha",
        "snapshot_id",
    }
    assert set(Freshness.__annotations__) == {
        "repo_id",
        "commit_sha",
        "snapshot_id",
        "workspace_head",
        "freshness",
        "reason",
    }
    record = FreshnessRecord(
        repo_id="repo-a",
        commit_sha="abc123",
        snapshot_id="snap-1",
        workspace_head="abc123",
        freshness="stale",
        reason="workspace moved ahead",
    )
    assert isinstance(record, Freshness)
    with pytest.raises(ValueError):
        FreshnessRecord(
            repo_id="repo-a",
            commit_sha="abc123",
            snapshot_id="snap-1",
            freshness="rotten",
        )
    assert coerce_freshness(None).freshness == "unknown"


def test_modules_expose_no_banned_stems() -> None:
    for path in MODULE_FILES:
        source = path.read_text(encoding="utf-8").lower()
        for stem in ("infer", "target", "adjacent"):
            assert stem not in source, f"{path.name} contains {stem!r}"


def test_citations_module_has_no_file_or_symbol_selector() -> None:
    import backend.codeintel_v2.citations as module

    public_functions = [
        value
        for name, value in vars(module).items()
        if not name.startswith("_") and callable(value) and getattr(value, "__module__", None) == module.__name__
    ]
    assert public_functions, "expected public constructors/serializers"
    for function in public_functions:
        for stem in ("infer", "target", "adjacent"):
            assert stem not in function.__name__.lower()
    assert isinstance(
        coerce_freshness({"freshness": "current"}), FreshnessRecord
    )
    with pytest.raises(ValueError):
        citation_to_dict({"path": "svc/orders.py"})  # type: ignore[arg-type]


# ---------------------------------------------------------------- client


def _counting_transport(
    handler: Any,
) -> tuple[list[httpx.Request], httpx.MockTransport]:
    calls: list[httpx.Request] = []

    def _handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return handler(request)

    return calls, httpx.MockTransport(_handle)


def _client(**overrides: Any) -> CodeIntelV2QueryClient:
    params: dict[str, Any] = {
        "base_url": "http://codeintel.test",
        "token": "test-token",
        "timeout_seconds": 5.0,
        "posture": "advisory",
    }
    params.update(overrides)
    return CodeIntelV2QueryClient(**params)


def _ok(payload: dict[str, Any]) -> httpx.Response:
    return httpx.Response(200, json=payload)


@pytest.mark.asyncio
async def test_off_posture_makes_zero_http_calls_explicit() -> None:
    calls, transport = _counting_transport(lambda request: _ok({"citations": []}))
    client = _client(posture="off", transport=transport)
    citations, error = await client.query("place_order")
    assert error == ERROR_DISABLED
    assert list(citations) == []
    assert calls == []


@pytest.mark.asyncio
async def test_off_posture_from_settings_makes_zero_http_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "AIX_CODEINTEL", "off")
    calls, transport = _counting_transport(lambda request: _ok({"citations": []}))
    client = _client(posture=None, transport=transport)
    citations, error = await client.query("place_order")
    assert error == ERROR_DISABLED
    assert list(citations) == []
    assert calls == []


@pytest.mark.asyncio
async def test_illegal_posture_rejected_before_io() -> None:
    calls, transport = _counting_transport(lambda request: _ok({"citations": []}))
    client = _client(posture="on", transport=transport)
    citations, error = await client.query("place_order")
    assert error == ERROR_POSTURE_REJECTED
    assert list(citations) == []
    assert calls == []


@pytest.mark.asyncio
async def test_missing_token_before_io(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "CODEINTEL_API_TOKEN", None)
    calls, transport = _counting_transport(lambda request: _ok({"citations": []}))
    client = _client(posture="advisory", token=None, transport=transport)
    citations, error = await client.query("place_order")
    assert error == ERROR_TOKEN_MISSING
    assert list(citations) == []
    assert calls == []


@pytest.mark.asyncio
async def test_advisory_stale_results_marked_degraded() -> None:
    payload = {
        "citations": [
            _good_item(),
            _good_item(
                path="svc/refunds.py",
                line_range=[3, 9],
                freshness=dict(_STALE_FRESHNESS),
            ),
        ]
    }

    def _handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/query"
        assert request.headers["authorization"].startswith("Bearer ")
        return _ok(payload)

    calls, transport = _counting_transport(_handle)
    citations, error = await _client(transport=transport).query("place_order")
    assert error is None
    assert isinstance(citations, list)
    assert isinstance(citations, CitationSet)
    assert len(citations) == 2
    assert citations.advisory_degraded is True
    evidence = citations.to_evidence()
    assert evidence["advisory_degraded"] is True
    assert evidence["candidate_only"] is True
    assert evidence["citation_count"] == 2
    assert calls != []


@pytest.mark.asyncio
async def test_advisory_current_results_not_degraded() -> None:
    calls, transport = _counting_transport(
        lambda request: _ok({"citations": [_good_item()]})
    )
    citations, error = await _client(transport=transport).query("place_order")
    assert error is None
    assert len(citations) == 1
    assert citations.advisory_degraded is False
    assert citations.to_evidence()["advisory_degraded"] is False
    assert calls != []


@pytest.mark.asyncio
async def test_top_level_stale_without_citations_still_degraded() -> None:
    payload = {"citations": [], "freshness": dict(_STALE_FRESHNESS)}
    _, transport = _counting_transport(lambda request: _ok(payload))
    citations, error = await _client(transport=transport).query("place_order")
    assert error is None
    assert list(citations) == []
    assert citations.advisory_degraded is True
    assert citations.to_evidence()["advisory_degraded"] is True


@pytest.mark.asyncio
async def test_empty_results_have_no_fallback() -> None:
    _, transport = _counting_transport(lambda request: _ok({"citations": []}))
    citations, error = await _client(transport=transport).query("place_order")
    assert error is None
    assert list(citations) == []
    assert citations.advisory_degraded is False


@pytest.mark.asyncio
async def test_malformed_items_skipped_never_repaired() -> None:
    payload = {
        "citations": [
            _good_item(path="svc/kept.py"),
            _good_item(path=""),  # schemaless: skipped
            _good_item(commit_sha="unknown"),  # schemaless: skipped
            _good_item(confidence=9.9),  # out of range: skipped
            {"not": "a citation"},  # wrong shape: skipped
            "svc/nope.py",  # wrong shape: skipped
        ]
    }
    _, transport = _counting_transport(lambda request: _ok(payload))
    citations, error = await _client(transport=transport).query("place_order")
    assert error is None
    assert [item.path for item in citations] == ["svc/kept.py"]


@pytest.mark.asyncio
async def test_timeout_is_classified() -> None:
    def _handle(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out")

    _, transport = _counting_transport(_handle)
    citations, error = await _client(transport=transport).query("place_order")
    assert error == ERROR_TIMEOUT
    assert list(citations) == []


@pytest.mark.asyncio
async def test_connection_failure_is_classified() -> None:
    def _handle(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    _, transport = _counting_transport(_handle)
    citations, error = await _client(transport=transport).query("place_order")
    assert error == ERROR_CONNECTION
    assert list(citations) == []


@pytest.mark.asyncio
async def test_http_status_is_classified() -> None:
    _, transport = _counting_transport(
        lambda request: httpx.Response(500, json={"error": "boom"})
    )
    citations, error = await _client(transport=transport).query("place_order")
    assert error == http_status_error(500) == "CODEINTEL_V2_HTTP_500"
    assert list(citations) == []


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, json={"nope": True}),
        httpx.Response(200, json={"citations": {}}),
        httpx.Response(200, json=["svc/orders.py"]),
        httpx.Response(200, content=b"not json"),
    ],
    ids=["missing-key", "citations-not-list", "list-payload", "invalid-json"],
)
@pytest.mark.asyncio
async def test_payload_shape_failures_classified(response: httpx.Response) -> None:
    _, transport = _counting_transport(lambda request: response)
    citations, error = await _client(transport=transport).query("place_order")
    assert error == ERROR_PAYLOAD_SHAPE
    assert list(citations) == []


@pytest.mark.asyncio
async def test_errors_never_leak_tracebacks() -> None:
    cases: list[httpx.MockTransport] = []

    def _timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out")

    def _conn(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    calls_t, transport_t = _counting_transport(_timeout)
    calls_c, transport_c = _counting_transport(_conn)
    _, transport_500 = _counting_transport(
        lambda request: httpx.Response(500, json={"error": "x"})
    )
    _, transport_bad = _counting_transport(
        lambda request: httpx.Response(200, content=b"nope")
    )
    cases.extend([transport_t, transport_c, transport_500, transport_bad])
    for transport in cases:
        _, error = await _client(transport=transport).query("place_order")
        assert error is not None
        assert "Traceback" not in error
        assert "File \"" not in error
    assert calls_t != [] and calls_c != []
