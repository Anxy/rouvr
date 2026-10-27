"""Persistent, Linux process-bound WorkHub command boundary.

Only BrokerServer supplies peer identity (SO_PEERCRED + process start ticks).
A private SQLite journal preserves pending/uncertain operations across restart.
An uncertain request is NEVER resent: unique upstream keys are per call; an
operator must reconcile authoritative readback before a new intent is issued.
#121 WorkHubProjection is injected unchanged. WorkHub owns all lifecycle state.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
import socket
import sqlite3
import stat
import struct
import time
import uuid


class Refused(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def process_identity(pid, uid):
    try:
        ticks = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[19]
        if Path(f'/proc/{pid}').stat().st_uid != uid:
            raise ValueError()
    except (OSError, ValueError, IndexError):
        raise Refused('IDENTITY_UNAVAILABLE') from None
    return f'{uid}:{pid}:{ticks}'


def private_json(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.geteuid():
            raise Refused('PRIVATE_FILE_REQUIRED')
        with os.fdopen(fd, closefd=False) as handle:
            return json.load(handle)
    finally:
        os.close(fd)


def integer(value):
    return type(value) is int and value >= 0


class Broker:
    def __init__(self, *, state_dir, tenant_id, project_id, principals, projection, transport, clock=time.time):
        self.root = Path(state_dir)
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.root.is_symlink() or stat.S_IMODE(self.root.stat().st_mode) != 0o700 or self.root.stat().st_uid != os.geteuid():
            raise Refused('PRIVATE_STATE_REQUIRED')
        self.lock = open(self.root / 'owner.lock', 'a')
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.lock.close()
            raise Refused('BROKER_ALREADY_RUNNING') from None
        self.db = sqlite3.connect(self.root / 'journal.sqlite3')
        os.chmod(self.root / 'journal.sqlite3', 0o600)
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('CREATE TABLE IF NOT EXISTS intents (id TEXT PRIMARY KEY, digest TEXT, principal TEXT, request TEXT, state TEXT, receipt TEXT, error TEXT)')
        self.db.execute("UPDATE intents SET state='uncertain',error='ACK_UNKNOWN' WHERE state='inflight'")
        self.db.commit()
        self.tenant, self.project = tenant_id, project_id
        self.principals, self.projection, self.transport, self.clock = principals, projection, transport, clock

    def close(self):
        self.db.close()
        self.lock.close()

    def _principal(self, identity):
        p = self.principals.get(identity)
        if not isinstance(p, dict):
            raise Refused('IDENTITY_UNKNOWN')
        if p.get('tenant_id') != self.tenant or p.get('project_id') != self.project:
            raise Refused('TENANT_MISMATCH')
        return p

    def _check(self, intent, p):
        if intent.get('schema_version') != 'aix.workhub-intent.v1':
            raise Refused('INTENT_INVALID')
        if intent.get('tenant_id') != self.tenant or intent.get('project_id') != self.project:
            raise Refused('TENANT_MISMATCH')
        # #126 scope cut: evidence writes lack atomic assignment fencing.
        # Registry permissions cannot expand the supported operation surface.
        action = intent.get('action')
        if action != 'transition' or action not in p.get('permissions', []):
            raise Refused('PERMISSION_DENIED')
        n = intent.get('task_number')
        if not integer(n) or n < 1 or n not in p.get('task_numbers', []):
            raise Refused('TASK_NOT_AUTHORIZED')
        for k in ('expected_record_version', 'expected_assignment_revision', 'expected_lease_epoch'):
            if not integer(intent.get(k)):
                raise Refused('REVISION_REQUIRED')
        if not isinstance(intent.get('assignment_id'), str) or not intent['assignment_id']:
            raise Refused('ASSIGNMENT_REQUIRED')
        payload = intent.get('payload')
        if not isinstance(payload, dict):
            raise Refused('PAYLOAD_INVALID')
        if action == 'transition':
            if set(payload) != {'status', 'reason_code', 'reason_summary'} or payload['status'] not in {'running', 'needs_review'}:
                raise Refused('TRANSITION_FORBIDDEN')
            if not all(isinstance(payload[k], str) and payload[k].strip() for k in payload):
                raise Refused('REASON_REQUIRED')

    def _snapshot(self, n):
        # Explicit refresh: a recent file cannot hide a current WorkHub outage.
        for kind in ('task', 'assignment'):
            self.projection.refresh(kind, n)
        task = self.projection.read_fresh('task', n)
        assignment = self.projection.read_fresh('assignment', n)
        metrics = [self.projection.freshness_metric_at_dispatch(k, n) for k in ('task', 'assignment')]
        if not all(m['fresh'] for m in metrics):
            raise Refused('PROJECTION_STALE')
        return task['data'], assignment['data'], metrics

    def submit(self, intent, identity):
        p = self._principal(identity)
        if not isinstance(intent, dict):
            raise Refused('INTENT_INVALID')
        iid = intent.get('intent_id')
        if not isinstance(iid, str) or not iid or len(iid) > 200:
            raise Refused('INTENT_ID_REQUIRED')
        raw = json.dumps(intent, sort_keys=True, separators=(',', ':'), allow_nan=False)
        digest = hashlib.sha256(raw.encode()).hexdigest()
        prior = self.db.execute('SELECT digest,principal,state,receipt FROM intents WHERE id=?', (iid,)).fetchone()
        if prior and (prior[0] != digest or prior[1] != p['subject']):
            raise Refused('INTENT_CONFLICT')
        self.db.execute("INSERT OR IGNORE INTO intents VALUES (?,?,?,?,'pending',NULL,NULL)", (iid, digest, p['subject'], raw))
        self.db.commit()
        try:
            self._check(intent, p)
            if prior and prior[2] == 'acked':
                return json.loads(prior[3])
            if prior and prior[2] in {'inflight', 'uncertain'}:
                raise Refused('ACK_UNKNOWN_RECONCILE_REQUIRED')
            task, binding, metrics = self._snapshot(intent['task_number'])
            if task.get('tenant_id') != self.tenant or task.get('project_id') != self.project or task.get('task_number') != intent['task_number']:
                raise Refused('SOURCE_SCOPE_MISMATCH')
            if not integer(task.get('record_version')) or task['record_version'] != intent['expected_record_version']:
                raise Refused('TASK_REVISION_MISMATCH')
            if binding.get('task_id') != task.get('id') or binding.get('assignment_status') != 'active':
                raise Refused('ASSIGNMENT_MISMATCH')
            if binding.get('holding_principal') != p['subject'] or binding.get('executor_sub') != p['subject']:
                raise Refused('HOLDER_MISMATCH')
            for source, expected in (('assignment_id', 'assignment_id'), ('assignment_record_version', 'expected_assignment_revision'), ('lease_epoch', 'expected_lease_epoch')):
                if binding.get(source) != intent[expected] or (source != 'assignment_id' and not integer(binding.get(source))):
                    raise Refused('ASSIGNMENT_REVISION_MISMATCH')
            # WorkHub must enforce these same values atomically at mutation time.
            if hasattr(self.transport, 'validate_mutation'):
                self.transport.validate_mutation(intent)
            key = 'aix-broker-' + str(uuid.uuid4())
            self.db.execute("UPDATE intents SET state='inflight',error=NULL WHERE id=?", (iid,))
            self.db.commit()
            ack = self.transport.mutate(intent, task['id'], key)
            if not isinstance(ack, dict) or ack.get('acknowledged') is not True or ack.get('task_id') != task['id'] or not ack.get('workhub_id'):
                raise Refused('WORKHUB_ACK_INVALID')
            receipt = {'schema_version': 'aix.workhub-receipt.v1', 'intent_id': iid, 'intent_sha256': digest, 'task_id': task['id'], 'task_number': intent['task_number'], 'workhub_id': ack['workhub_id'], 'idempotency_key': key, 'acknowledged_at': self.clock(), 'projection_freshness': metrics}
            self.db.execute("UPDATE intents SET state='acked',receipt=?,error=NULL WHERE id=?", (json.dumps(receipt), iid))
            self.db.commit()
            return receipt
        except Exception as exc:
            code = getattr(exc, 'code', 'WORKHUB_UNAVAILABLE')
            self.db.execute("UPDATE intents SET state=CASE WHEN state='inflight' THEN 'uncertain' ELSE state END,error=? WHERE id=?", (code, iid))
            self.db.commit()
            raise Refused(code) from None

    def read_projection(self, request, identity):
        p = self._principal(identity)
        n, kind = request.get('task_number'), request.get('kind')
        if 'read' not in p.get('permissions', []) or not integer(n) or n not in p.get('task_numbers', []) or kind not in {'task', 'assignment', 'evidence'}:
            raise Refused('PERMISSION_DENIED')
        self.projection.refresh(kind, n)
        record = self.projection.read_fresh(kind, n)
        if kind == 'task' and (record['data'].get('tenant_id') != self.tenant or record['data'].get('project_id') != self.project or record['data'].get('task_number') != n):
            raise Refused('SOURCE_SCOPE_MISMATCH')
        return record

    def handle(self, frame, identity):
        if not isinstance(frame, dict):
            raise Refused('FRAME_INVALID')
        if frame.get('operation') == 'submit':
            return self.submit(frame.get('intent'), identity)
        if frame.get('operation') == 'read':
            return self.read_projection(frame, identity)
        raise Refused('OPERATION_INVALID')


MAX_FRAME = 1024 * 1024


def receive(sock):
    data = bytearray()
    while len(data) <= MAX_FRAME:
        chunk = sock.recv(min(65536, MAX_FRAME + 1 - len(data)))
        if not chunk:
            raise Refused('FRAME_INCOMPLETE')
        data.extend(chunk)
        if b'\n' in chunk:
            return json.loads(bytes(data).split(b'\n', 1)[0])
    raise Refused('FRAME_TOO_LARGE')


class BrokerServer:
    """One serial service; socket lives in an operator-owned shared directory.

    Socket group can be provisioned by the operator. Server credentials and
    state MUST be under a distinct OS identity from untrusted worker accounts.
    """
    def __init__(self, broker, socket_path):
        self.broker, self.path = broker, Path(socket_path)

    def serve(self, stop):
        parent = self.path.parent.stat()
        if parent.st_uid != os.geteuid() or stat.S_IMODE(parent.st_mode) & 0o022:
            raise Refused('SOCKET_DIRECTORY_UNSAFE')
        with open(str(self.path) + '.lock', 'a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise Refused('SOCKET_ALREADY_OWNED') from None
            self._serve_locked(stop)

    def _serve_locked(self, stop):
        if self.path.exists():
            if not stat.S_ISSOCK(self.path.lstat().st_mode):
                raise Refused('SOCKET_PATH_OCCUPIED')
            self.path.unlink()  # private state flock already fences another server
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
            server.bind(str(self.path))
            os.chmod(self.path, 0o660)
            server.listen(16)
            server.settimeout(0.2)
            try:
                while not stop.is_set():
                    try:
                        conn, _ = server.accept()
                    except TimeoutError:
                        continue
                    with conn:
                        conn.settimeout(5)
                        try:
                            pid, uid, _ = struct.unpack('3i', conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                            identity = process_identity(pid, uid)
                            result = self.broker.handle(receive(conn), identity)
                            reply = {'ok': True, 'result': result}
                        except Exception as exc:
                            reply = {'ok': False, 'code': getattr(exc, 'code', 'BROKER_FAILURE')}
                        try:
                            conn.sendall(json.dumps(reply, allow_nan=False).encode() + b'\n')
                        except OSError:
                            pass  # receipt is already durable, client may reconnect
            finally:
                self.path.unlink(missing_ok=True)
