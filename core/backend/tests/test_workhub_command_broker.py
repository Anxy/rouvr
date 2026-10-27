"""Broker acceptance against the unchanged #121 projection dependency.

AIX_PROJECTION_MODULE points at the reviewed companion file when unmerged.
No staging mutation, model worker or live Temporal effect is simulated as proof.
"""
import asyncio
import copy
import importlib.util
import json
import os
import threading
import time

import pytest

from backend.workhub_command_broker import Broker, BrokerServer, Refused, process_identity, private_json
from backend.workhub_broker_client import BrokerClient
from backend.workhub_broker_connections import ConsumerProjection, agent_wake_metric
from backend.workhub_broker_transport import WorkHubTransport


@pytest.fixture
def projection_type():
    path = os.environ.get('AIX_PROJECTION_MODULE')
    if path:
        spec = importlib.util.spec_from_file_location('companion_projection', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.WorkHubProjection
    from backend.workhub_projection import WorkHubProjection
    return WorkHubProjection


class WorkHub:
    def __init__(self):
        self.task = {'id': 'task-122', 'tenant_id': 't', 'project_id': 'p', 'task_number': 122, 'record_version': 4, 'status': 'running'}
        self.binding = {'task_id': 'task-122', 'task_number': 122, 'assignment_id': 'assignment-1', 'record_version': 3, 'assignment_record_version': 3, 'lease_epoch': 7, 'assignment_status': 'active', 'holding_principal': 'worker', 'executor_sub': 'worker'}
        self.calls = []
        self.outage = False
        self.before_ack = lambda: None

    def read_projection(self, project, number, kind):
        if self.outage:
            raise RuntimeError('outage')
        return dict(self.task if kind == 'task' else self.binding)

    def mutate(self, intent, task_id, key):
        self.calls.append(key)
        self.before_ack()
        if self.outage:
            raise RuntimeError('outage')
        return {'acknowledged': True, 'task_id': task_id, 'workhub_id': task_id}


@pytest.fixture
def setup(tmp_path, projection_type):
    wh = WorkHub()
    principal = {'subject': 'worker', 'tenant_id': 't', 'project_id': 'p', 'task_numbers': [122], 'permissions': ['transition', 'attach_evidence', 'read']}
    identity = process_identity(os.getpid(), os.getuid())
    config = dict(state_dir=tmp_path / 'state', tenant_id='t', project_id='p', principals={identity: principal}, transport=wh,
                  projection=projection_type(root=tmp_path/'projection', reader=wh, project_id='p'))
    intent = {'schema_version': 'aix.workhub-intent.v1', 'intent_id': 'one', 'tenant_id': 't', 'project_id': 'p', 'task_number': 122, 'action': 'transition', 'expected_record_version': 4, 'assignment_id': 'assignment-1', 'expected_assignment_revision': 3, 'expected_lease_epoch': 7, 'payload': {'status': 'needs_review', 'reason_code': 'BUILT', 'reason_summary': 'review candidate'}}
    return config, intent, identity, wh


def test_receipt_only_after_ack_and_restart(setup):
    config, intent, identity, wh = setup
    b = Broker(**config)
    def before():
        state, receipt = b.db.execute('SELECT state,receipt FROM intents').fetchone()
        assert state == 'inflight' and receipt is None
    wh.before_ack = before
    receipt = b.submit(intent, identity)
    assert receipt['workhub_id'] == 'task-122'
    assert all(m['fresh'] for m in receipt['projection_freshness'])
    b.close()
    b = Broker(**config)
    assert b.submit(intent, identity) == receipt
    assert len(wh.calls) == 1
    b.close()


@pytest.mark.parametrize('change,code', [
    ({'tenant_id': 'wrong'}, 'TENANT_MISMATCH'),
    ({'project_id': 'wrong'}, 'TENANT_MISMATCH'),
    ({'action': 'delete'}, 'PERMISSION_DENIED'),
    ({'task_number': 999}, 'TASK_NOT_AUTHORIZED'),
    ({'expected_record_version': True}, 'REVISION_REQUIRED'),
    ({'expected_record_version': 3}, 'TASK_REVISION_MISMATCH'),
    ({'expected_assignment_revision': 2}, 'ASSIGNMENT_REVISION_MISMATCH'),
    ({'expected_lease_epoch': 6}, 'ASSIGNMENT_REVISION_MISMATCH'),
    ({'assignment_id': 'old'}, 'ASSIGNMENT_REVISION_MISMATCH'),
    ({'payload': {'status': 'done', 'reason_code': 'x', 'reason_summary': 'x'}}, 'TRANSITION_FORBIDDEN'),
])
def test_checks_preserve_pending_no_receipt(setup, change, code):
    config, intent, identity, wh = setup
    b = Broker(**config)
    intent.update(change)
    with pytest.raises(Refused, match=code):
        b.submit(intent, identity)
    assert wh.calls == []
    assert b.db.execute('SELECT state,receipt FROM intents').fetchone() == ('pending', None)
    b.close()


@pytest.mark.parametrize('field,value,code', [('holding_principal','other','HOLDER_MISMATCH'), ('executor_sub','other','HOLDER_MISMATCH'), ('assignment_status','expired','ASSIGNMENT_MISMATCH')])
def test_binding_checks(setup, field, value, code):
    config, intent, identity, wh = setup
    wh.binding[field] = value
    b = Broker(**config)
    with pytest.raises(Refused, match=code): b.submit(intent, identity)
    assert not wh.calls
    b.close()


def test_outage_stale_and_recovery(setup):
    config, intent, identity, wh = setup
    b = Broker(**config)
    b._snapshot(122)
    wh.outage = True
    with pytest.raises(Refused, match='PROJECTION_STALE'): b.submit(intent, identity)
    assert not wh.calls
    b.close()
    wh.outage = False
    b = Broker(**config)
    assert b.submit(intent, identity)['workhub_id'] == 'task-122'
    b.close()


def test_ambiguous_ack_never_replayed(setup):
    config, intent, identity, wh = setup
    b = Broker(**config)
    def lose_ack(): raise TimeoutError()
    wh.before_ack = lose_ack
    with pytest.raises(Refused): b.submit(intent, identity)
    b.close()
    b = Broker(**config)
    with pytest.raises(Refused, match='ACK_UNKNOWN_RECONCILE_REQUIRED'): b.submit(intent, identity)
    assert len(wh.calls) == 1
    assert b.db.execute('SELECT state,receipt FROM intents').fetchone() == ('uncertain', None)
    b.close()


def test_identity_permission_conflict_singleton(setup):
    config, intent, identity, wh = setup
    b = Broker(**config)
    with pytest.raises(Refused, match='BROKER_ALREADY_RUNNING'): Broker(**config)
    with pytest.raises(Refused, match='IDENTITY_UNKNOWN'): b.submit(intent, 'self-asserted-pid')
    receipt = b.submit(intent, identity)
    different = copy.deepcopy(intent); different['payload']['reason_summary'] = 'changed'
    with pytest.raises(Refused, match='INTENT_CONFLICT'): b.submit(different, identity)
    config['principals'][identity]['permissions'] = []
    with pytest.raises(Refused, match='PERMISSION_DENIED'): b.submit(intent, identity)
    assert receipt and len(wh.calls) == 1
    b.close()


def test_real_socket_file_and_kernel_identity(setup, tmp_path):
    config, intent, identity, wh = setup
    path = tmp_path/'broker.sock'; stop = threading.Event(); errors = []
    def serve():
        try:
            b = Broker(**config)
            try: BrokerServer(b, path).serve(stop)
            finally: b.close()
        except Exception as exc: errors.append(exc)
    thread = threading.Thread(target=serve); thread.start()
    try:
        for _ in range(200):
            if path.exists(): break
            time.sleep(.005)
        file = tmp_path/'intent.json'; file.write_text(json.dumps(intent))
        client = BrokerClient(path)
        receipt = client.submit_file(file)
        assert receipt['workhub_id'] == 'task-122'
        assert client.read('task', 122)['source_revision'] == 4
        # Another process cannot inherit authority merely by using the socket.
        import subprocess, sys
        code = 'from backend.workhub_broker_client import BrokerClient; BrokerClient('+repr(str(path))+').read("task",122)'
        result = subprocess.run([sys.executable, '-c', code], capture_output=True)
        assert result.returncode != 0 and b'IDENTITY_UNKNOWN' in result.stderr
    finally:
        stop.set(); thread.join(6)
    assert not errors and not thread.is_alive()


def test_private_credentials_and_transport_fails_closed(tmp_path):
    path = tmp_path/'creds'; path.write_text('{}'); path.chmod(0o644)
    with pytest.raises(Refused, match='PRIVATE_FILE_REQUIRED'): private_json(path)
    path.chmod(0o600); assert private_json(path) == {}
    link = tmp_path/'link'; link.symlink_to(path)
    with pytest.raises(OSError): private_json(link)
    transport = WorkHubTransport(base_url='http://unused', credential_file=path, tenant_id='t', project_id='p')
    with pytest.raises(Refused, match='MUTATIONS_DISABLED'): transport.mutate({}, 'task', 'key')
    transport.mutation_contract = 'workhub.assignment-fencing.v1'
    with pytest.raises(Refused, match='EVIDENCE_ATOMIC_FENCING_UNAVAILABLE'): transport.mutate({'action':'attach_evidence'}, 'task', 'key')


def test_nested_nack_not_receipt(setup):
    config, intent, _, _ = setup
    t = WorkHubTransport(base_url='http://unused', credential_file='unused', tenant_id='t', project_id='p', mutation_contract='workhub.assignment-fencing.v1')
    t._call = lambda *args: {'failed':1, 'updated':0, 'idempotent_replays':0, 'results':[{'success':False}]}
    with pytest.raises(Refused, match='WORKHUB_NACK'): t.mutate(intent, 'task-122', 'unique')


def test_consumer_never_falls_back_and_metric():
    class Down:
        def read(self, *args): raise OSError()
    p = ConsumerProjection(Down())
    assert not p.is_fresh(p.read_task_snapshot(122))
    metric = agent_wake_metric(event_id='event', task_id='task', event_at_epoch=10, woke_at_epoch=10.125)
    assert metric['duration_ms'] == 125



def test_fresh_read_scope_and_mutation_race(setup):
    config, intent, identity, wh = setup
    b = Broker(**config)
    wh.task['tenant_id'] = 'other'
    with pytest.raises(Refused, match='SOURCE_SCOPE_MISMATCH'):
        b.read_projection({'kind':'task','task_number':122}, identity)
    wh.task['tenant_id'] = 't'
    def reassign_at_write():
        wh.binding['assignment_record_version'] += 1
        raise Refused('STALE_BINDING')  # server mutation-time CAS refusal
    wh.before_ack = reassign_at_write
    with pytest.raises(Refused, match='STALE_BINDING'): b.submit(intent, identity)
    assert b.db.execute('SELECT receipt FROM intents').fetchone() == (None,)
    b.close()


def test_scope_cut_activity_preserves_authority_without_broker():
    from backend.workflows.temporal import objective_cycle_activities as a
    raw = {'activity_name':'persist_transition', 'activity_id':'broker-scope-cut', 'tenant_id':'t', 'objective_id':'obj', 'cycle_id':'cycle', 'aggregate_version':1, 'idempotency_key':'scope-cut-key', 'correlation_id':'correlation', 'causation_id':'cause', 'payload_refs':{'workhub_task_number':'122'}}
    try:
        result = asyncio.run(a.persist_transition(raw))
        assert 'workhub_source_revision' not in result
        assert not hasattr(a, 'bind_workhub_broker')
    finally:
        a._IDEMPOTENCY_CACHE.pop('broker-scope-cut', None)


@pytest.mark.parametrize('outage', [False, True])
def test_scope_cut_evidence_refused_before_projection_or_transport(setup, outage):
    config, intent, identity, wh = setup
    intent.update(action='attach_evidence', payload={'evidence_type': 'test', 'evidence_json': {}})
    wh.outage = outage
    def unexpected_refresh(*args):
        pytest.fail('unsupported evidence must not refresh projections')
    config['projection'].refresh = unexpected_refresh
    b = Broker(**config)
    try:
        with pytest.raises(Refused, match='PERMISSION_DENIED'):
            b.submit(intent, identity)
        assert not wh.calls
        assert b.db.execute('SELECT state,receipt FROM intents').fetchone() == ('pending', None)
    finally:
        b.close()


def test_transport_forwards_raw_versions_and_rejects_replayed_ack(setup):
    _, intent, _, _ = setup
    t = WorkHubTransport(base_url='http://unused', credential_file='unused', tenant_id='t', project_id='p', mutation_contract='workhub.assignment-fencing.v1')
    calls = []
    def call(method, path, payload, key):
        calls.append((method, path, payload, key))
        return {'failed':0,'updated':1,'idempotent_replays':0,'results':[{'success':True,'idempotent_replay':False,'data':{'id':'task-122','status':'needs_review'}}]}
    t._call = call
    assert t.mutate(intent, 'task-122', 'unique')['acknowledged']
    item = calls[0][2]['items'][0]
    for field in ('expected_record_version','assignment_id','expected_assignment_revision','expected_lease_epoch'):
        assert item[field] == intent[field]
    assert item['metadata_json']['reason_code'] == 'BUILT'
    t._call = lambda *args: {'failed':0,'updated':1,'idempotent_replays':1,'results':[{'success':True,'idempotent_replay':True,'data':{'id':'task-122','status':'needs_review'}}]}
    with pytest.raises(Refused, match='WORKHUB_NACK'): t.mutate(intent,'task-122','another-unique')


def test_projection_assignment_revision_is_not_task_revision():
    t = WorkHubTransport(base_url='http://unused',credential_file='unused',tenant_id='t',project_id='p')
    t._call = lambda *args: {'assignment_record_version': 19, 'lease_epoch': 27}
    assert t.read_projection('p',122,'assignment')['record_version'] == 19
    with pytest.raises(Refused, match='EVIDENCE_COLLECTION_REVISION_UNAVAILABLE'): t.read_projection('p',122,'evidence')


def test_process_crash_recovers_inflight_without_resending(setup, tmp_path):
    import subprocess
    import sys
    config, intent, identity, wh = setup
    b = Broker(**config)
    b.db.execute("INSERT INTO intents VALUES (?,?,?,?,?,?,?)", ('one','digest','worker',json.dumps(intent),'inflight',None,None))
    b.db.commit(); b.close()
    # Real process exits without close; SQLite's durable state remains readable.
    script = "import sqlite3,os; c=sqlite3.connect("+repr(str(config['state_dir']/'journal.sqlite3'))+"); c.execute(\"UPDATE intents SET error='crash'\"); c.commit(); os._exit(9)"
    assert subprocess.run([sys.executable,'-c',script]).returncode == 9
    b = Broker(**config)
    assert b.db.execute('SELECT state,error,receipt FROM intents').fetchone() == ('uncertain','ACK_UNKNOWN',None)
    assert not wh.calls
    b.close()


def test_consumer_to_broker_projection_to_canonical_temporal(setup, tmp_path, monkeypatch):
    from backend.workhub_broker_connections import connect_consumer
    from backend.aix_event_consumer.consumer import DictEventSource
    from backend.aix_event_consumer.cursor import DurableCursor
    from backend.aix_event_consumer.temporal import InMemoryTemporalDouble
    config, _, identity, wh = setup
    b = Broker(**config)
    class Client:
        def __init__(self, path): pass
        def read(self, kind, n): return b.read_projection({'kind':kind,'task_number':n},identity)
    monkeypatch.setattr('backend.workhub_broker_connections.BrokerClient',Client)
    event = {'event_id':'event-122','event_type':'task.updated','tenant_id':'t','task_number':122,'objective_id':'obj','cycle_id':'cycle','candidate_sha':'a'*40,'plan_sha256':'b'*64}
    temporal = InMemoryTemporalDouble()
    consumer = connect_consumer(socket_path='unused',event_source=DictEventSource([event]),cursor=DurableCursor(tmp_path/'cursor.json'),temporal=temporal,tenant_id='t')
    assert consumer.run_once().started == 1
    consumer.event_source = DictEventSource([{**event,'event_id':'second'}]);wh.outage = True
    assert consumer.run_once().deferred == 1
    assert len(temporal.starts) == 1
    b.close()


def test_real_daemon_entrypoint_external_projection_restart(setup, tmp_path):
    """Real HTTP/socket/process integration; remote server is a labelled double."""
    import hashlib
    import subprocess
    import sys
    from http.server import BaseHTTPRequestHandler, HTTPServer
    config, intent, identity, wh = setup
    mutations = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def reply(self, data):
            self.send_response(200); self.send_header('Content-Type','application/json'); self.end_headers()
            self.wfile.write(json.dumps({'success':True,'data':data}).encode())
        def do_GET(self):
            assert self.headers['Authorization'] == 'Bearer test-only-token'
            self.reply(wh.binding if self.path.endswith('assignment-binding') else wh.task)
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            mutations.append(body)
            self.reply({'failed':0,'updated':1,'idempotent_replays':0,'results':[{'success':True,'idempotent_replay':False,'data':{'id':'task-122','status':'needs_review'}}]})
    http = HTTPServer(('127.0.0.1',0),Handler)
    thread = threading.Thread(target=http.serve_forever); thread.start()
    from pathlib import Path
    projection_path = Path(os.environ['AIX_PROJECTION_MODULE'])
    creds = tmp_path/'credentials';creds.write_text(json.dumps({'tenant_id':'t','access_token':'test-only-token'}));creds.chmod(0o600)
    socket_path = tmp_path/'daemon.sock'
    settings = {'state_dir':str(config['state_dir']), 'socket_path':str(socket_path), 'tenant_id':'t','project_id':'p','principals':config['principals'], 'workhub_base_url':f'http://127.0.0.1:{http.server_port}', 'credential_file':str(creds),'projection_dir':str(tmp_path/'daemon-projection'), 'projection_module':str(projection_path), 'projection_sha256':hashlib.sha256(projection_path.read_bytes()).hexdigest(), 'mutation_contract':'workhub.assignment-fencing.v1'}
    cfg = tmp_path/'config';cfg.write_text(json.dumps(settings));cfg.chmod(0o600)
    proc = None
    def start():
        p = subprocess.Popen([sys.executable,'-m','backend.scripts.run_workhub_broker','--config',str(cfg)],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        for _ in range(200):
            if socket_path.exists(): return p
            if p.poll() is not None: raise AssertionError(p.communicate()[1].decode())
            time.sleep(.005)
        p.kill();p.communicate();raise AssertionError('broker startup timeout')
    try:
        proc = start()
        receipt = BrokerClient(socket_path).submit(intent)
        assert receipt['workhub_id'] == 'task-122'
        proc.kill();proc.communicate(timeout=5);proc=None
        # Kill leaves the socket. Singleton lock is released by the kernel;
        # remove only this test-owned path so readiness cannot see the old inode.
        socket_path.unlink(missing_ok=True)
        proc = start()
        assert BrokerClient(socket_path).submit(intent) == receipt
        assert len(mutations) == 1
        assert BrokerClient(socket_path).read('task',122)['source_revision'] == 4
    finally:
        if proc is not None:
            proc.terminate();proc.communicate(timeout=5)
        http.shutdown();http.server_close();thread.join(5)


def test_acceptance_restore_uses_needs_review_with_fresh_binding(setup):
    """Queued scratch ends needs_review; supervisor archives after readback."""
    config, intent, identity, wh = setup
    b = Broker(**config)
    try:
        wh.task['status'] = 'queued'
        intent['payload']['status'] = 'running'
        b.submit(intent, identity)
        wh.task.update(status='running', record_version=5)
        restore = copy.deepcopy(intent)
        restore.update(intent_id='restore', expected_record_version=5)
        restore['payload'] = {'status': 'needs_review', 'reason_code': 'ACCEPTANCE_COMPLETE',
                              'reason_summary': 'Supervisor archive required after readback'}
        assert b.submit(restore, identity)['task_id'] == wh.task['id']
        queued = copy.deepcopy(restore)
        queued.update(intent_id='queued-restore')
        queued['payload']['status'] = 'queued'
        with pytest.raises(Refused, match='TRANSITION_FORBIDDEN'):
            b.submit(queued, identity)
        assert len(wh.calls) == 2
    finally:
        b.close()
