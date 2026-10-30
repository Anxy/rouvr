"""TokenSource mint shape (stage-proven) + host credential selection. No network."""
import json
import os
import stat

import httpx
import pytest

from rouvr.config import Settings
from rouvr.host import build_host
from rouvr.workhub.client import TokenSource


async def test_token_source_mints_proven_shape_and_caches():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"access_token": "tok-1", "expires_in": 3600})

    ts = TokenSource(httpx.AsyncClient(transport=httpx.MockTransport(handler)),
                     "cred-1", "secret-1", auth_base="https://auth.example/",
                     tenant="hyeve-lane-ops", audience="hyeve-workhub-service")
    assert await ts.get() == "tok-1"
    assert await ts.get() == "tok-1"  # cached, no second mint
    assert len(seen) == 1
    req = seen[0]
    assert req.url.path == "/v1/auth/service-tokens"
    assert req.headers["X-Hyeve-Tenant-Id"] == "hyeve-lane-ops"
    assert json.loads(req.content) == {"credential_id": "cred-1", "client_secret": "secret-1",
                                       "audience": "hyeve-workhub-service"}


async def test_token_source_refuses_non_200():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"code": "attempt_not_permitted"})

    ts = TokenSource(httpx.AsyncClient(transport=httpx.MockTransport(handler)),
                     "cred-1", "secret-1", auth_base="https://auth.example",
                     tenant="t", audience="a")
    with pytest.raises(Exception):
        await ts.get()


def _settings(tmp_path, registry):
    return Settings(tenant_id="hyeve-lane-ops", project_id="rouvr",
                    temporal_address="localhost:7233", temporal_namespace="default",
                    workhub_base_url="https://workhub.example", database_url="postgresql://x",
                    persona_registry_path=registry, workspace_root=tmp_path,
                    evidence_root=tmp_path, muse_bin="muse", workhub_backend="stage",
                    persona_source="yaml", spec_root=tmp_path)


def _persona(tmp_path, creds: dict, name="builder-1"):
    cred_file = tmp_path / f"{name}.json"
    cred_file.write_text(json.dumps(creds))
    os.chmod(cred_file, 0o600)
    registry = tmp_path / "tenants.yaml"
    registry.write_text(
        "tenants:\n  hyeve-lane-ops:\n    personas:\n"
        f"      {name}: {{role: builder, principal: svc-b, muse_model: '', credential_file: {cred_file}}}\n")
    return registry, name


def test_host_uses_token_source_for_service_credentials(tmp_path):
    registry, name = _persona(tmp_path, {"service_credential_id": "cred-1", "client_secret": "s",
                                         "auth_base": "https://auth.example", "audience": "wh",
                                         "tenant": "hyeve-lane-ops"})
    host = build_host(_settings(tmp_path, registry))
    wh = host.workhub_for(host.personas.get("hyeve-lane-ops", name, "builder"))
    assert isinstance(wh._token, TokenSource)


def test_host_keeps_static_workhub_token(tmp_path):
    registry, name = _persona(tmp_path, {"workhub_token": "static-tok", "meta_api_key": "k"})
    host = build_host(_settings(tmp_path, registry))
    wh = host.workhub_for(host.personas.get("hyeve-lane-ops", name, "builder"))
    assert wh._token == "static-tok"
