import base64
import hashlib
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import subprocess
import uuid

import pytest
from personal_ai_brain import codex_bridge_review as r
from personal_ai_brain.codex_bridge_v02_policy import project_for


def test_task_identity_and_terminal_state():
    key=str(uuid.uuid4());task=dict(state='succeeded',attempts=1,project_id='personal-ai-brain',repository='igormego/personal-ai-brain',base_sha='a'*40)
    assert r.task_record({'tasks':{key:task}},key)[0]==task
    for change in [dict(state='uncertain'),dict(attempts=2),dict(repository='attacker/repo'),dict(base_sha='bad')]:
        with pytest.raises(ValueError):r.task_record({'tasks':{key:task|change}},key)
    with pytest.raises(ValueError):r.task_record({'tasks':{}},'../escape')

@pytest.mark.parametrize('patch',[b'',b'x'*(r.MAX_PATCH+1),b'binary\0',b'github_pat_'+b'x'*40],ids=['empty','oversized','nul','credential'])
def test_export_bounds_and_secret_guard(patch):
    with pytest.raises(ValueError):r.scan_patch(patch)


def test_export_fixed_sandbox_no_shell(monkeypatch):
    seen=[]
    def run(args,**kwargs):
        seen.append(args);assert 'shell' not in kwargs
        return SimpleNamespace(returncode=0,stdout=json.dumps({'patch':base64.b64encode(b'diff fixture').decode(),'paths':['src/personal_ai_brain/state_quality.py']}).encode())
    monkeypatch.setattr(r.subprocess,'run',run)
    assert r.export_patch({'cwd':'fixed-workspace','base_sha':'a'*40})[0]==b'diff fixture'
    assert 'permissions.bridge-workspace-only.network.enabled=false' in seen[0]
    assert r.COLLECT in seen[0]
    assert 'push' not in seen[0]


@pytest.fixture
def packet(tmp_path,monkeypatch):
    source=tmp_path/'source';source.mkdir()
    def git(*args):return subprocess.check_output(['git','-C',str(source),*args],text=True).strip()
    git('init','-q','-b','main');git('config','user.name','Fixture');git('config','user.email','fixture@example.invalid');git('config','core.autocrlf','false')
    target=source/'src/personal_ai_brain/state_quality.py';target.parent.mkdir(parents=True);target.write_bytes(b'answer = 1\n')
    git('add','.');git('commit','-qm','base');base=git('rev-parse','HEAD')
    target.write_bytes(b'answer = 2\n');patch=subprocess.check_output(['git','-C',str(source),'diff','--binary','HEAD']);git('restore','.')
    git('remote','add','origin','https://github.com/igormego/personal-ai-brain.git')
    taskid=str(uuid.uuid4());validation=dict(files_changed=['src/personal_ai_brain/state_quality.py'],additions=1,deletions=1,test_summary='2 tests passed in restricted Linux sandbox')
    task=dict(task_id=taskid,state='succeeded',attempts=1,project_id='personal-ai-brain',repository='igormego/personal-ai-brain',base_sha=base,result=validation)
    app=tmp_path/'PersonalAIBrain';ledger=app/'bridge-v02/ledger';delivery=app/'bridge-v02/transport';ledger.mkdir(parents=True);delivery.mkdir()
    (ledger/'ledger.json').write_text(json.dumps({'tasks':{taskid:task}}))
    body=dict(task_id=taskid,status='succeeded',project_id='personal-ai-brain',base_sha=base,**validation)
    (delivery/'delivery.json').write_text(json.dumps({'requests':{'request':dict(phase='delivered',body='```json\n'+json.dumps(body)+'\n```')}}))
    (app/'patch-review-v01').mkdir()
    monkeypatch.setattr(r,'os',SimpleNamespace(name='nt',environ={'LOCALAPPDATA':str(tmp_path)}))
    monkeypatch.setattr(r,'project_for',lambda key:replace(project_for(key),windows_checkout=str(source)))
    from personal_ai_brain import codex_bridge_transport_runtime
    monkeypatch.setattr(codex_bridge_transport_runtime,'private_acl',lambda p:None)
    monkeypatch.setattr(r,'export_patch',lambda ws:(patch,validation['files_changed']))
    monkeypatch.setattr(r,'validate_completion',lambda ws,project:validation)
    monkeypatch.setattr(r,'remote_main',lambda repo:base)
    return taskid,source,patch,app


def test_real_isolated_staging_and_repeat(packet):
    taskid,source,patch,app=packet
    result=r.prepare_review(taskid)
    assert (result/'patch.diff').read_bytes()==patch
    assert r.run_git(source,'status','--porcelain')==b''
    assert r.run_git(result/'checkout','remote')==b''
    assert r.prepare_review(taskid)==result
    manifest=json.loads((result/'manifest.json').read_text())
    assert manifest['publication']=='NOT_APPROVED_NOT_ATTEMPTED'
    assert manifest['publication_blocker']=='owner_approval_required'


def test_stale_base_is_explicit_publication_blocker(packet,monkeypatch):
    monkeypatch.setattr(r,'remote_main',lambda repo:'b'*40)
    p=r.prepare_review(packet[0]);assert json.loads((p/'manifest.json').read_text())['publication_blocker']=='stale_base'


def test_packet_tamper_and_dirty_checkout_fail_closed(packet):
    p=r.prepare_review(packet[0]);(p/'checkout/src/personal_ai_brain/state_quality.py').write_text('tampered\n')
    with pytest.raises(ValueError,match='review_checkout_drift'):r.prepare_review(packet[0])


def test_partial_packet_not_overwritten(packet):
    taskid,_,_,app=packet;(app/'patch-review-v01'/taskid).mkdir()
    with pytest.raises(FileNotFoundError):r.prepare_review(taskid)


def test_no_publication_cli():
    import inspect
    source=inspect.getsource(r)
    assert "add_argument('task_id')" in source
    assert "'push'" not in source and "'commit'" not in source


def test_missing_delivery_fails_before_staging(packet):
    taskid,_,_,app=packet
    (app/'bridge-v02/transport/delivery.json').write_text('{"requests":{}}')
    with pytest.raises(ValueError,match='successful_delivery_required'):r.prepare_review(taskid)


def test_changed_source_base_is_not_rebased(packet):
    taskid,source,_,_=packet
    r.run_git(source,'-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','--allow-empty','-m','different base')
    with pytest.raises(ValueError,match='trusted_source_stale_or_dirty'):r.prepare_review(taskid)


def test_revalidation_drift_is_rejected(packet,monkeypatch):
    taskid,_,_,_=packet
    monkeypatch.setattr(r,'validate_completion',lambda *a:{'unexpected':'result'})
    with pytest.raises(ValueError,match='validation_or_patch_drift'):r.prepare_review(taskid)


def test_manifest_tampering_is_not_approval(packet):
    p=r.prepare_review(packet[0]);m=json.loads((p/'manifest.json').read_text());m['publication']='APPROVED'
    (p/'manifest.json').write_text(json.dumps(m))
    with pytest.raises(ValueError,match='review_manifest_drift'):r.prepare_review(packet[0])
