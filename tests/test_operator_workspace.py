import json
from pathlib import Path
from uuid import uuid4

import pytest

from personal_ai_brain import work_contract as contract, work_generic
from personal_ai_brain.operator_scope import ScopeDenied, ScopeGrant, validate_selector, load_scope
from personal_ai_brain import operator_scope
from personal_ai_brain.work_handoff import BASE, arguments, summary
from personal_ai_brain.work_runner import Runner
from personal_ai_brain.work_sandbox import command
from personal_ai_brain.work_store import Store


def task(**extra):
    return dict(schema_version='0.1', task_id=str(uuid4()),
                task_type=contract.OPERATOR_TYPE, repository=contract.REPOSITORY,
                base_ref=BASE, goal='Inspect a granted dataset.',
                acceptance_criteria=['Return checked counts'], requested_at=contract.timestamp(),
                deadline_seconds=30, script='print("fixture")', inputs=[],
                scope_id='media-analysis', access='read') | extra


def parsed(**extra):
    return contract.parse(json.dumps(task(**extra)).encode())


@pytest.mark.parametrize('extra', [
    {'scope_id':'../secret'}, {'scope_id':'/data'}, {'scope_id':'a/b'},
    {'access':'root'}, {'host':'localhost'}, {'ssh_key':'key'}, {'mount':'/etc'},
])
def test_caller_cannot_choose_host_path_or_privilege(extra):
    with pytest.raises(contract.Rejected):
        parsed(**extra)


def test_same_transport_and_disabled_default(tmp_path):
    request = parsed()
    assert arguments('work.submit', {'contract':request}, BASE)['contract'] == request
    store = Store(tmp_path)
    store.submit(request, BASE)
    runner = Runner(store, {'base_ref':BASE, 'generic_enabled':True, 'min_free_bytes':0})
    assert runner.tick()
    record = store.load(request['task_id'])
    assert record['status'] == 'blocked'
    assert record['history'][-1]['code'] == 'operator_not_enabled'
    assert not runner.tick()


def test_grant_modes_and_network_isolation():
    for access in ('read', 'write'):
        grant = ScopeGrant('media-analysis', access, Path('/approved/read'),
                           Path('/approved/write'), False)
        args = command(Path('/scratch'), Path('/private/script.py'), generic=True,
                       operator_scope=grant)
        assert '--unshare-all' in args and '--share-net' not in args
        assert args[args.index('/approved/read')-1] == '--ro-bind'
        assert args[args.index('/approved/write')-1] == ('--bind' if access == 'write' else '--ro-bind')
        assert '/workspace/resources' in args and '/workspace/changes' in args
        assert '/approved/read' not in command(Path('/scratch'), Path('/private/script.py'), generic=True)


def test_scope_denies_unknown_or_unapproved_write(monkeypatch):
    monkeypatch.setattr(operator_scope, '_read_policy', lambda _: {
        'schema_version':'0.1','scopes':{'media-analysis':{'write':False,'preview':False}}})
    for scope, access in [('unknown','read'), ('media-analysis','write')]:
        with pytest.raises(ScopeDenied):
            load_scope(scope, access)


def test_policy_rejects_unexpected_fields_and_untrusted_location(monkeypatch, tmp_path):
    monkeypatch.setattr(operator_scope, '_read_policy', lambda _: {
        'schema_version':'0.1','scopes':{'media-analysis':{'write':False,'preview':False,'path':'/etc'}}})
    with pytest.raises(ScopeDenied):
        load_scope('media-analysis','read')
    with pytest.raises(ScopeDenied):
        operator_scope._protected(tmp_path, directory=True)


def test_missing_grant_fails_before_execution(tmp_path):
    workspace = tmp_path/'work'
    workspace.mkdir()
    artifacts = tmp_path/'audit'
    artifacts.mkdir()
    assert work_generic.execute(parsed(),workspace,artifacts,lambda: None) == (
        'failed','generic_input_rejected',())


def test_owner_grant_reuses_runner_and_manifest(tmp_path, monkeypatch):
    request = parsed(access='write')
    grant = ScopeGrant('media-analysis','write',Path('/approved/read'),Path('/approved/write'),False)
    monkeypatch.setattr(operator_scope,'load_scope',lambda *args:grant)
    def execute(req, workspace, artifacts, guard, *, operator_scope):
        guard()
        assert operator_scope == grant and req == request
        return 'succeeded','generic_sandbox_completed',(
            {'path':'result.json','size':2,'sha256':'a'*64},)
    monkeypatch.setattr(work_generic,'execute',execute)
    store = Store(tmp_path)
    store.submit(request,BASE)
    runner = Runner(store,{'base_ref':BASE,'generic_enabled':True,'operator_enabled':True,'min_free_bytes':0})
    assert runner.tick() and not runner.tick()
    record = store.load(request['task_id'])
    assert record['status']=='succeeded' and record['attempts']==1
    assert summary(record)['result']['goal_semantics_verified'] is False
    assert not store.submit(request,BASE)[1]


def test_interrupted_write_status_does_not_claim_no_effect(tmp_path):
    request = parsed(access='write')
    store = Store(tmp_path)
    store.submit(request,BASE)
    store.transition(request['task_id'],'reserved','worker_reserved',attempts=1)
    store.transition(request['task_id'],'running','sandbox_starting')
    Runner(store,{'base_ref':BASE}).recovery()
    result = summary(store.load(request['task_id']))['result']
    assert result['status'] == 'interrupted'
    assert result['scope_id'] == 'media-analysis'
    assert result['workspace_write_may_have_occurred'] is True
