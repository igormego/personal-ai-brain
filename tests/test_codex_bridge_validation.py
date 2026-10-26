"""Exercise the installed validator policy without executing task-supplied code."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import types

import pytest
from personal_ai_brain import codex_bridge_transport_runtime as rt

SOURCE = 'src/personal_ai_brain/state_quality.py'
TEST = 'tests/test_state_quality.py'

@pytest.fixture
def validation(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = tmp_path / SOURCE
    path.parent.mkdir(parents=True)
    path.write_text('pure = True')
    state = dict(head='a'*40, untracked=b'', rows=f'1\t0\t{SOURCE}\n',
                 patch=b'original patch', calls=[], code=0, output=b'18 passed in 0.02s\n', after=None)
    monkeypatch.setattr(sys, 'argv', ['validator', str(tmp_path), 'a'*40])
    # Windows lacks resource; the subprocess boundary is mocked, no task code runs.
    monkeypatch.setitem(sys.modules, 'resource', types.SimpleNamespace(RLIMIT_FSIZE=1, setrlimit=lambda *a: None))
    def run(args, **kw):
        if args[0] == '/usr/bin/git':
            command = args[9:]
            if command[:1] == ['rev-parse']: data = state['head'].encode()
            elif command[:1] == ['ls-files']: data = state['untracked']
            elif command[:1] == ['ls-tree']: data = b'100644 blob hash\tpath'
            elif '--check' in command:
                if state.get('bad_diff'): return types.SimpleNamespace(returncode=1, stdout=b'')
                data = b''
            elif '--numstat' in command: data = state['rows'].encode()
            else: data = state['patch']
            return types.SimpleNamespace(returncode=0, stdout=data)
        state['calls'].append(args)
        assert args[:6] == ['/usr/bin/python3', '-m', 'pytest', '-q', '-p', 'no:cacheprovider']
        assert args[6] == '--basetemp' and Path(args[7]).parent.parent == tmp_path
        assert args[8:] == [TEST]  # No repository-wide collection or caller arguments.
        assert kw['timeout'] == 180
        kw['preexec_fn']()
        kw['stdout'].write(state['output']); kw['stdout'].flush()
        if state['after']: state['after']()
        return types.SimpleNamespace(returncode=state['code'])
    monkeypatch.setattr(subprocess, 'run', run)
    def invoke():
        out = io.StringIO()
        with contextlib.redirect_stdout(out): exec(rt.VALIDATE, {})
        return json.loads(out.getvalue())
    return state, invoke, path

@pytest.mark.parametrize('edited', [SOURCE, TEST])
def test_permitted_edit_runs_only_fixed_mapping(validation, edited):
    s, run, path = validation
    target=Path(edited); target.parent.mkdir(parents=True,exist_ok=True); target.write_text('fixture')
    s['rows']=f'1\t0\t{edited}\n'
    result=run()
    assert result == dict(files_changed=[edited], additions=1, deletions=0,
                         test_summary='18 tests passed in restricted Linux sandbox')
    assert len(s['calls'])==1
    assert not list(Path.cwd().glob('.pab-validation-*'))

@pytest.mark.parametrize('path', ['src/personal_ai_brain/inference_broker.py',
    'src/personal_ai_brain/codex_bridge_transport_runtime.py', 'tests/conftest.py',
    'pyproject.toml', 'README.md', 'tests/test_coding_broker.py'])
def test_non_allowlisted_change_rejected_before_tests(validation, path):
    s, run, _=validation; s['rows']=f'1\t0\t{path}\n'
    with pytest.raises(RuntimeError,match='change_scope_denied'): run()
    assert not s['calls']

@pytest.mark.parametrize('field,value,error', [
    ('head','b'*40,'unexpected_commit'), ('untracked',b'extra','untracked_files'),
    ('rows','', 'change_size_denied'), ('rows',f'3001\t0\t{SOURCE}\n','change_size_denied'),
    ('bad_diff',True,'git_validation_failed')])
def test_invariants_before_tests(validation, field, value, error):
    s,run,_=validation; s[field]=value
    with pytest.raises(RuntimeError,match=error): run()
    assert not s['calls']


def test_symlink_fails_closed(validation, monkeypatch):
    s,run,_=validation
    monkeypatch.setattr(Path,'is_symlink',lambda self: self.as_posix()==SOURCE)
    with pytest.raises(RuntimeError,match='symlink_denied'): run()
    assert not s['calls']

@pytest.mark.parametrize('field,value,error', [
    ('patch',b'same-size edits','tests_changed_tree'), ('head','b'*40,'unexpected_commit'),
    ('untracked',b'new file','untracked_files'), ('bad_diff',True,'git_validation_failed')])
def test_post_test_invariants(validation, field, value, error):
    s,run,_=validation; s['after']=lambda:s.update({field:value})
    with pytest.raises(RuntimeError,match=error): run()
    assert len(s['calls'])==1

@pytest.mark.parametrize('code,output,error', [(1,b'sensitive test text','sandboxed_tests_failed'),
    (0,b'x'*1048577,'test_output_limit'), (0,b'no trusted summary','missing_test_summary')], ids=['failed','oversized','missing-summary'])
def test_output_and_failure_are_bounded(validation, code, output, error):
    s,run,_=validation; s.update(code=code, output=output)
    with pytest.raises(RuntimeError,match=error) as caught: run()
    assert 'sensitive' not in str(caught.value)
    assert not list(Path.cwd().glob('.pab-validation-*'))


def test_raw_output_not_in_result(validation):
    s,run,_=validation; s['output']=b'private test output\n18 passed in 0.02s\n'
    assert 'private' not in json.dumps(run())


def test_outer_validator_sanitizes_failure(monkeypatch):
    monkeypatch.setattr(rt.subprocess,'run',lambda *a,**k:types.SimpleNamespace(returncode=1,stdout=b'private'))
    with pytest.raises(RuntimeError,match='^completion_validation_failed$'):
        rt.validate_completion(dict(cwd=rt.ROOT+'/tasks/12345678-1234-4234-8234-123456789abc',base_sha='a'*40))


def test_validator_failure_keeps_ledger_non_success_and_no_replay(tmp_path, monkeypatch):
    import uuid
    from personal_ai_brain.codex_bridge_core import Ledger
    from personal_ai_brain.codex_bridge_client import execute
    from personal_ai_brain import codex_bridge_workspace as workspace
    monkeypatch.setattr(workspace,'git',lambda *args:'a'*40)
    monkeypatch.setattr(workspace,'prepare',lambda *args:{'cwd':'trusted'})
    payload=dict(request_id=str(uuid.uuid4()),task_id=str(uuid.uuid4()),project_id='personal-ai-brain',
                 execution_profile='local-homelab-admin',goal='run arbitrary tests',
                 acceptance_criteria='model says passed',base_sha='a'*40)
    class Client:
        calls=[]
        def rpc(self,method,params):
            self.calls.append(method)
            return {'thread':{'id':'thread'}} if method=='thread/start' else {'turn':{'id':'turn'}}
        def event(self,timeout):
            return {'method':'turn/completed','params':{'turn':{'id':'turn','status':'completed'}}}
    def fail(ws): raise RuntimeError('completion_validation_failed')
    ledger=Ledger(tmp_path); client=Client()
    result=execute(ledger,payload,client,validate_completion=fail)
    assert result['state']=='uncertain' and result['attempts']==1
    assert execute(ledger,payload,client,validate_completion=fail)['state']=='uncertain'
    assert client.calls==['thread/start','turn/start']
