"""Server-only WorkHub transport. No endpoint or credential crosses IPC.

Mutations require the #128 server fencing contract; no legacy downgrade.
Read-only is the default and is used for staging acceptance.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from backend.workhub_command_broker import Refused, private_json


class WorkHubTransport:
    def __init__(self, *, base_url, credential_file, tenant_id, project_id, mutation_contract=None):
        self.base = base_url.rstrip('/')
        self.credential_file = credential_file
        self.tenant, self.project = tenant_id, project_id
        self.mutation_contract = mutation_contract

    def _access_token(self):
        creds = private_json(self.credential_file)
        if not isinstance(creds, dict):
            raise Refused('CREDENTIAL_SCOPE_MISMATCH')
        tenants = [creds[k] for k in ('tenant_id', 'tenant') if k in creds]
        if not tenants or any(value != self.tenant for value in tenants):
            raise Refused('CREDENTIAL_SCOPE_MISMATCH')
        if 'service_credential_id' not in creds:
            token = creds.get('access_token')
            if not isinstance(token, str) or not token.strip():
                raise Refused('CREDENTIAL_SCOPE_MISMATCH')
            return token
        fields = ('service_credential_id', 'client_secret', 'audience', 'auth_base')
        if any(not isinstance(creds.get(k), str) or not creds[k].strip() for k in fields):
            raise Refused('CREDENTIAL_SCOPE_MISMATCH')
        auth_base = creds['auth_base'].rstrip('/')
        parsed = urllib.parse.urlsplit(auth_base)
        if parsed.scheme not in {'http', 'https'} or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise Refused('CREDENTIAL_SCOPE_MISMATCH')
        request = urllib.request.Request(
            auth_base + '/v1/auth/service-tokens',
            data=json.dumps({'credential_id': creds['service_credential_id'],
                             'client_secret': creds['client_secret'],
                             'audience': creds['audience']}).encode(),
            headers={'Content-Type': 'application/json', 'X-Hyeve-Tenant-Id': self.tenant},
            method='POST')
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                body = json.load(response)
        except urllib.error.HTTPError:
            raise Refused('AUTH_REFUSED') from None
        except OSError:
            raise Refused('AUTH_UNAVAILABLE') from None
        except ValueError:
            raise Refused('AUTH_ACK_INVALID') from None
        token = body.get('access_token') if isinstance(body, dict) else None
        if not isinstance(token, str) or not token.strip():
            raise Refused('AUTH_ACK_INVALID')
        # No disk cache, journal entry, IPC field or log receives this token.
        return token

    def _call(self, method, path, payload=None, key=None):
        token = self._access_token()
        headers = {'Authorization': 'Bearer ' + token, 'X-Hyeve-Tenant-Id': self.tenant, 'X-Project-ID': self.project, 'Content-Type': 'application/json'}
        if key:
            headers['Idempotency-Key'] = key
        request = urllib.request.Request(self.base + path, data=json.dumps(payload).encode() if payload is not None else None, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                body = json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code == 404 and path.endswith('/assignment-binding'):
                try:
                    error = json.load(exc)
                except (OSError, ValueError):
                    error = {}
                detail = error.get('detail') if isinstance(error, dict) else None
                if detail == 'Not Found':
                    raise Refused('ASSIGNMENT_BINDING_ROUTE_UNAVAILABLE') from None
                if isinstance(detail, dict) and detail.get('code') == 'assignment_binding_not_found':
                    raise Refused('ASSIGNMENT_REQUIRED') from None
            raise Refused('WORKHUB_REFUSED') from None
        except (OSError, ValueError):
            raise Refused('WORKHUB_UNAVAILABLE') from None
        if not isinstance(body, dict) or body.get('success') is not True or not isinstance(body.get('data'), dict):
            raise Refused('WORKHUB_ACK_INVALID')
        return body['data']

    def read_projection(self, project_id, task_number, kind):
        if project_id != self.project:
            raise Refused('TENANT_MISMATCH')
        base = f'/api/v1/projects/{urllib.parse.quote(self.project, safe="")}/tasks/by-number/{int(task_number)}'
        if kind == 'task':
            return self._call('GET', base)
        if kind == 'assignment':
            data = self._call('GET', base + '/assignment-binding')
            # #121 expects record_version: this is the raw assignment version,
            # renamed, never derived from a task version or local lease epoch.
            return {**data, 'record_version': data.get('assignment_record_version')}
        if kind == 'evidence':
            # Evidence collection has no aggregate revision in the supported
            # route. Refuse rather than fabricate one for #121.
            raise Refused('EVIDENCE_COLLECTION_REVISION_UNAVAILABLE')
        raise Refused('PROJECTION_KIND_INVALID')

    def validate_mutation(self, intent):
        if self.mutation_contract != 'workhub.assignment-fencing.v1':
            raise Refused('MUTATIONS_DISABLED')
        if intent['action'] != 'transition':
            # Current evidence POST does not expose #128 binding preconditions.
            raise Refused('EVIDENCE_ATOMIC_FENCING_UNAVAILABLE')
    def mutate(self, intent, task_id, key):
        self.validate_mutation(intent)
        item = {'task_id': task_id, 'status': intent['payload']['status'], 'reason': intent['payload']['reason_summary'], 'metadata_json': {'reason_code': intent['payload']['reason_code'], 'reason_summary': intent['payload']['reason_summary']}, 'idempotency_key': key}
        for field in ('expected_record_version', 'assignment_id', 'expected_assignment_revision', 'expected_lease_epoch'):
            item[field] = intent[field]
        data = self._call('POST', f'/api/v1/projects/{urllib.parse.quote(self.project, safe="")}/tasks/transitions/batch', {'items': [item]}, key)
        results = data.get('results')
        if data.get('failed') != 0 or data.get('updated') != 1 or data.get('idempotent_replays') != 0 or not isinstance(results, list) or len(results) != 1 or results[0].get('success') is not True or results[0].get('idempotent_replay') is not False:
            raise Refused('WORKHUB_NACK')
        task = results[0].get('data', {})
        if task.get('id') != task_id or task.get('status') != intent['payload']['status']:
            raise Refused('WORKHUB_ACK_INVALID')
        return {'acknowledged': True, 'task_id': task_id, 'workhub_id': task['id']}
