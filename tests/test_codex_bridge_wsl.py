"""Policy tests run without a WSL installation or authentication."""
import json
from pathlib import Path
from types import SimpleNamespace
import uuid

import pytest

from personal_ai_brain import codex_bridge_wsl as wsl
from personal_ai_brain.codex_bridge_core import Ledger, validate


def test_launch_is_fixed_direct_execution_with_clean_environment():
    args = wsl.select_backend(wsl.BACKEND).launch_args()
    assert args[:len(wsl.PREFIX)] == wsl.PREFIX
    assert args[args.index('--distribution')+1] == 'Debian'
    assert '--exec' in args and '-i' in args
    assert 'CODEX_HOME=/home/pabbridge/.codex' in args
    assert '/usr/local/bin/codex' in args
    assert not any('windows.sandbox' in arg for arg in args)
    assert 'permissions.bridge-workspace-only.network.enabled=false' in args
    assert any('":root"="deny"' in arg for arg in args)


@pytest.mark.parametrize('name', ['', 'Debian', 'native', 'wsl-codex-v02'])
def test_unknown_backend_rejected(name):
    with pytest.raises(ValueError, match='unknown_backend'):
        wsl.select_backend(name)


@pytest.mark.parametrize('field', ['execution_backend', 'cwd', 'distribution', 'CODEX_HOME'])
def test_payload_cannot_select_backend_configuration(field):
    payload = dict(request_id=str(uuid.uuid4()), task_id=str(uuid.uuid4()),
                   project_id='personal-ai-brain', execution_profile='local-homelab-admin',
                   goal='test', acceptance_criteria='test', base_sha='a'*40)
    with pytest.raises(ValueError, match='invalid_fields'):
        validate(payload | {field: 'hostile'})


def test_materialization_verifies_exact_mapping_and_cleans_transfer(monkeypatch, tmp_path):
    from personal_ai_brain import codex_bridge_workspace as workspace
    monkeypatch.setattr(workspace, 'git', lambda *args: 'a'*40)
    monkeypatch.setattr(wsl.tempfile, 'tempdir', str(tmp_path))
    task = str(uuid.uuid4())
    expected = dict(cwd=wsl.ROOT+'/tasks/'+task,
                    branch='codex/bridge-task-'+task, base_sha='a'*40)
    seen = []
    def run(args, **kwargs):
        seen.append(args)
        if args[0] == 'git':
            Path(args[-2]).write_bytes(b'CONTROLLED-BUNDLE')
            return SimpleNamespace()
        assert kwargs['stdin'].read() == b'CONTROLLED-BUNDLE'
        assert args[-2:] == [task, 'a'*40]
        assert kwargs['timeout'] == 120
        return SimpleNamespace(stdout=json.dumps(expected).encode())
    monkeypatch.setattr(wsl.subprocess, 'run', run)
    assert wsl.WslBackend().prepare(task, 'a'*40) == expected
    assert not Path(seen[0][-2]).exists()


def test_unapproved_base_no_transfer(monkeypatch):
    from personal_ai_brain import codex_bridge_workspace as workspace
    monkeypatch.setattr(workspace, 'git', lambda *args: 'b'*40)
    monkeypatch.setattr(wsl.subprocess, 'run', lambda *a, **k: pytest.fail('no transfer'))
    with pytest.raises(ValueError, match='base_not_approved'):
        wsl.WslBackend().prepare(str(uuid.uuid4()), 'a'*40)


def test_wsl_result_uses_existing_ledger_no_duplicate_or_conflict_launch(monkeypatch, tmp_path):
    from personal_ai_brain import codex_bridge_workspace as workspace
    from personal_ai_brain.codex_bridge_client import execute
    monkeypatch.setattr(workspace, 'git', lambda *args: 'a'*40)
    payload = dict(request_id=str(uuid.uuid4()), task_id=str(uuid.uuid4()),
                   project_id='personal-ai-brain', execution_profile='local-homelab-admin',
                   goal='test', acceptance_criteria='test', base_sha='a'*40)
    calls = []
    class Backend:
        def prepare(self, task, base):
            calls.append('prepare')
            return {'cwd': wsl.ROOT+'/tasks/'+task}
    class Client:
        workspace_backend = Backend()
        def rpc(self, method, params):
            calls.append(method)
            return {'thread': {'id': 'thread'}} if method == 'thread/start' else {'turn': {'id': 'turn'}}
        def event(self, timeout):
            return {'method': 'turn/completed', 'params': {'turn': {'id': 'turn', 'status': 'completed'}}}
    ledger = Ledger(tmp_path)
    result = execute(ledger, payload, Client(), validate_completion=lambda ws: {'test_summary': 'passed'})
    assert result['state'] == 'succeeded'
    assert execute(ledger, payload, Client()) == result
    with pytest.raises(ValueError, match='conflict'):
        execute(ledger, payload | {'goal': 'changed'}, Client())
    assert calls == ['prepare', 'thread/start', 'turn/start']


def test_local_startup_failure_is_durable_and_never_retried(monkeypatch, tmp_path):
    from personal_ai_brain import codex_bridge_client as client
    payload = dict(request_id=str(uuid.uuid4()), task_id=str(uuid.uuid4()),
                   project_id='personal-ai-brain', execution_profile='local-homelab-admin',
                   goal='test', acceptance_criteria='test', base_sha='a'*40)
    calls = []
    def unavailable(**kwargs):
        calls.append(kwargs)
        raise OSError('unavailable backend')
    monkeypatch.setattr(client, 'AppServer', unavailable)
    ledger = Ledger(tmp_path)
    result = wsl.run_local(ledger, payload)
    assert result['status'] == 'uncertain'
    assert result['failure_class'] == 'backend_startup_failed'
    assert result['attempts'] == 1
    assert wsl.run_local(ledger, payload) == result
    with pytest.raises(ValueError, match='conflict'):
        wsl.run_local(ledger, payload | {'goal': 'changed'})
    assert calls == [{'execution_backend': wsl.BACKEND}]
    assert ledger.load()['tasks'][payload['task_id']]['turn_id'] is None
