"""Synthetic credentials only; verify minting never modifies credential files."""
import io
import json
import urllib.error

import pytest

from backend.workhub_broker_transport import WorkHubTransport
from backend.workhub_command_broker import Refused


@pytest.fixture
def transport(tmp_path):
    path = tmp_path / 'credential.json'
    path.write_text(json.dumps({'tenant': 't', 'service_credential_id': 'synthetic-id',
                                'client_secret': 'synthetic-secret', 'audience': 'workhub',
                                'auth_base': 'https://auth.test'}))
    path.chmod(0o600)
    return WorkHubTransport(base_url='https://workhub.test', credential_file=path,
                            tenant_id='t', project_id='p')


def test_service_credential_mints_in_memory(transport, monkeypatch):
    before = transport.credential_file.read_bytes()
    calls = []
    def open_request(request, **kwargs):
        calls.append(request)
        if request.full_url == 'https://auth.test/v1/auth/service-tokens':
            assert json.loads(request.data) == {'credential_id': 'synthetic-id',
                'client_secret': 'synthetic-secret', 'audience': 'workhub'}
            assert request.get_header('X-hyeve-tenant-id') == 't'
            return io.BytesIO(b'{"access_token":"synthetic-token"}')
        assert request.get_header('Authorization') == 'Bearer synthetic-token'
        return io.BytesIO(b'{"success":true,"data":{"id":"task"}}')
    monkeypatch.setattr('urllib.request.urlopen', open_request)
    assert transport.read_projection('p', 215, 'task') == {'id': 'task'}
    assert len(calls) == 2
    assert transport.credential_file.read_bytes() == before
    assert list(transport.credential_file.parent.iterdir()) == [transport.credential_file]


@pytest.mark.parametrize('change', [{'tenant': 'other'}, {'tenant_id': 'other'},
                                   {'audience': ''}, {'client_secret': ''}, {'auth_base': ''}])
def test_invalid_service_scope_never_sends(transport, monkeypatch, change):
    creds = json.loads(transport.credential_file.read_text())
    creds.update(change)
    transport.credential_file.write_text(json.dumps(creds))
    def unexpected(*args, **kwargs):
        pytest.fail('invalid credentials sent a request')
    monkeypatch.setattr('urllib.request.urlopen', unexpected)
    with pytest.raises(Refused):
        transport.read_projection('p', 215, 'task')


@pytest.mark.parametrize('response', [b'{}', b'{"access_token":""}', b'{"access_token":123}', b'[]', b'invalid'])
def test_invalid_token_response_is_redacted(transport, monkeypatch, response):
    monkeypatch.setattr('urllib.request.urlopen', lambda *a, **k: io.BytesIO(response))
    with pytest.raises(Refused, match='AUTH_ACK_INVALID'):
        transport.read_projection('p', 215, 'task')


def test_auth_failure_is_redacted(transport, monkeypatch):
    def denied(request, **kwargs):
        raise urllib.error.HTTPError(request.full_url, 401, 'synthetic-secret', {}, None)
    monkeypatch.setattr('urllib.request.urlopen', denied)
    with pytest.raises(Refused, match='^AUTH_REFUSED$'):
        transport.read_projection('p', 215, 'task')


@pytest.mark.parametrize('body,expected', [
    ({'detail': 'Not Found'}, 'ASSIGNMENT_BINDING_ROUTE_UNAVAILABLE'),
    ({'detail': {'code': 'assignment_binding_not_found'}}, 'ASSIGNMENT_REQUIRED'),
    ({'detail': {'code': 'task_not_found'}}, 'WORKHUB_REFUSED'),
])
def test_assignment_404_diagnosis(transport, monkeypatch, body, expected):
    transport.credential_file.write_text(json.dumps({'tenant_id': 't', 'access_token': 'synthetic-token'}))
    def missing(request, **kwargs):
        raise urllib.error.HTTPError(request.full_url, 404, 'Not Found', {},
                                     io.BytesIO(json.dumps(body).encode()))
    monkeypatch.setattr('urllib.request.urlopen', missing)
    with pytest.raises(Refused, match='^' + expected + '$'):
        transport.read_projection('p', 215, 'assignment')
