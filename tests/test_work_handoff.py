import json
import os
from pathlib import Path
import threading
import time
from uuid import uuid4

import pytest

from personal_ai_brain import work_contract as contract
from personal_ai_brain.work_handoff import BASE, Client, Directory, Server, arguments
from personal_ai_brain.work_store import Store
from personal_ai_brain.work_runner import Runner
from personal_ai_brain.control import Redactor, Rejected, build_registry
from personal_ai_brain.control_worker import Worker
from test_control import FakeTransport, request as control_request
from test_work_runner import request as task_request

@pytest.fixture
def setup(tmp_path):
    surface = tmp_path / 'surface'
    for name in ('inbox', 'outbox'):
        (surface / name).mkdir(parents=True)
    store = Store(tmp_path / 'builder')
    server = Server(store, BASE, surface)
    yield store, server, Client(surface, wait_seconds=0), surface
    server.close()

def invoke(setup, operation, args, request_id=None):
    store, server, client, surface = setup
    rid = request_id or str(uuid4())
    pending = client.invoke(operation, args, rid)
    assert pending['outcome'] == 'handoff_pending'
    server.tick()
    return client.invoke(operation, args, rid)

def test_submit_status_duplicate_conflict_cancel(setup):
    store, server, client, surface = setup
    task = task_request(base_ref=BASE)
    first = invoke(setup, 'work.submit', {'contract': task})
    assert first['data']['created'] and first['data']['status'] == 'submitted'
    duplicate = invoke(setup, 'work.submit', {'contract': task})
    assert not duplicate['data']['created']
    conflict = invoke(setup, 'work.submit', {'contract': {**task, 'deadline_seconds': 19}})
    assert conflict['error'] == 'task_id_payload_conflict'
    status = invoke(setup, 'work.status', {'task_id': task['task_id']})
    assert status['data']['attempts'] == 0
    cancelled = invoke(setup, 'work.cancel', {'task_id': task['task_id']})
    assert cancelled['data']['status'] == 'cancelled'
    assert cancelled['data']['terminal']
    assert len(store.records()) == 1

@pytest.mark.parametrize('operation,args', [
    ('shell', {}), ('work.status', {'task_id': '../escape'}),
    ('work.cancel', {'task_id': str(uuid4()), 'force': True}),
    ('work.submit', {'contract': task_request(base_ref='a'*40)}),
    ('work.submit', {'contract': task_request(base_ref=BASE, command='id')}),
    ('work.submit', {'contract': task_request(base_ref=BASE, repository='Other/repo')}),
])
def test_invalid_never_written_or_executed(setup, operation, args):
    store, server, client, surface = setup
    with pytest.raises(contract.Rejected):
        client.invoke(operation, args, str(uuid4()))
    server.tick()
    assert not store.records() and not list((surface / 'inbox').iterdir())

def test_runner_independently_validates_bypassed_control(setup):
    store, server, client, surface = setup
    rid = str(uuid4())
    server.inbox.publish(rid, {'schema_version': '0.1', 'request_id': rid,
        'capability': 'work.submit', 'arguments': {'contract': task_request(base_ref=BASE, task_type='shell')}})
    server.tick()
    assert server.outbox.read(rid)['outcome'] == 'rejected'
    assert not store.records()

def test_crash_after_admission_before_response_does_not_repeat(setup, monkeypatch):
    store, server, client, surface = setup
    task = task_request(base_ref=BASE)
    rid = str(uuid4())
    client.invoke('work.submit', {'contract': task}, rid)
    original = server.outbox.publish
    monkeypatch.setattr(server.outbox, 'publish', lambda *_: (_ for _ in ()).throw(OSError('disk')))
    server.tick()
    assert len(store.records()) == 1
    monkeypatch.setattr(server.outbox, 'publish', original)
    server.tick()
    assert not server.outbox.read(rid)['data']['created']
    assert len(store.records()) == 1

def test_control_restart_after_handoff_reservation_is_not_reinvoked(setup, tmp_path):
    store, server, client, surface = setup
    task = task_request(base_ref=BASE)
    req = control_request(capability='work.submit', arguments={'contract': task})
    class CrashingClient:
        def invoke(self, *args):
            client.invoke(*args)
            raise KeyboardInterrupt()
    transport = FakeTransport([json.dumps(req)])
    registry = build_registry(tmp_path, None, Redactor(), CrashingClient())
    worker = Worker(tmp_path / 'control', transport, registry, Redactor())
    with pytest.raises(KeyboardInterrupt):
        worker.tick()
    server.tick()
    worker = Worker(tmp_path / 'control', transport,
                    build_registry(tmp_path, None, Redactor(), client), Redactor())
    worker.tick()
    assert transport.comments[0][1]['status'] == 'duplicate'
    assert len(store.records()) == 1
    assert len(list((surface / 'inbox').glob('*.json'))) == 1

@pytest.mark.parametrize('kind', ['symlink', 'hardlink', 'fifo', 'oversize', 'malformed'])
def test_poisoned_entry_fail_closed(setup, tmp_path, kind):
    store, server, client, surface = setup
    rid = str(uuid4())
    path = surface / 'inbox' / (rid + '.json')
    target = tmp_path / 'private'
    target.write_text('{"private":"do not read"}')
    if kind == 'symlink': path.symlink_to(target)
    elif kind == 'hardlink': os.link(target, path)
    elif kind == 'fifo': os.mkfifo(path)
    elif kind == 'oversize': path.write_bytes(b'x' * 8193)
    else: path.write_text('{"x":1,"x":2}')
    server.tick()
    assert not store.records() and not list((surface / 'outbox').iterdir())

def test_directory_parent_symlink_rejected(tmp_path):
    (tmp_path / 'real').mkdir()
    (tmp_path / 'link').symlink_to(tmp_path / 'real')
    with pytest.raises(OSError): Directory(tmp_path / 'link')

def test_response_hash_binding_and_no_overwrite(setup):
    store, server, client, surface = setup
    rid = str(uuid4())
    task = task_request(base_ref=BASE)
    invoke(setup, 'work.submit', {'contract': task}, rid)
    with pytest.raises(contract.Rejected):
        client.invoke('work.submit', {'contract': {**task, 'deadline_seconds': 19}}, rid)
    assert len(store.records()) == 1

def test_status_does_not_export_internal_fields(setup):
    store, server, client, surface = setup
    task = task_request(base_ref=BASE)
    store.submit(task, BASE)
    store.transition(task['task_id'], 'reserved', 'worker_reserved', attempts=1)
    store.transition(task['task_id'], 'running', 'sandbox_starting')
    store.transition(task['task_id'], 'succeeded', '/secret/internal/path')
    result = invoke(setup, 'work.status', {'task_id': task['task_id']})
    encoded = json.dumps(result)
    assert 'secret' not in encoded and 'history' not in encoded and 'request"' not in encoded
    assert result['data']['result'] == {'status': 'succeeded', 'artifacts_retained': True}

def test_builder_recovery_keeps_remote_task_interrupted(setup):
    store, server, client, surface = setup
    task = task_request(base_ref=BASE)
    invoke(setup, 'work.submit', {'contract': task})
    store.transition(task['task_id'], 'reserved', 'worker_reserved', attempts=1)
    store.transition(task['task_id'], 'running', 'sandbox_starting')
    Runner(store, {'base_ref': BASE}).recovery()
    result = invoke(setup, 'work.submit', {'contract': task})
    assert result['data']['status'] == 'interrupted' and result['data']['attempts'] == 1

def test_cancel_during_execution_is_received_concurrently(setup):
    store, server, client, surface = setup
    task = task_request(base_ref=BASE)
    store.submit(task, BASE)
    store.transition(task['task_id'], 'reserved', 'worker_reserved', attempts=1)
    store.transition(task['task_id'], 'running', 'sandbox_starting')
    result = invoke(setup, 'work.cancel', {'task_id': task['task_id']})
    assert result['data']['cancel_requested']
    store.transition(task['task_id'], 'succeeded', 'fixture_completed')
    assert store.load(task['task_id'])['status'] == 'cancelled'

def test_original_registry_unchanged_and_new_operations_explicit(tmp_path):
    old = build_registry(tmp_path, None, Redactor())
    new = build_registry(tmp_path, None, Redactor(), Client())
    assert set(old.capabilities) == {
        'runtime.health', 'core.health', 'core.snapshot',
        'core.report', 'core.rollup_proposals', 'runtime.logs_tail',
        'project_os.status',
    }
    assert set(new.capabilities) == set(old.capabilities) | {
        'work.submit', 'work.status', 'work.cancel',
    }
    for name, cap in old.capabilities.items():
        other = new.capabilities[name]
        assert cap.argument_schema == other.argument_schema
        assert cap.risk_level == other.risk_level == 'read_only'
        assert cap.side_effect_class == other.side_effect_class == 'none'
