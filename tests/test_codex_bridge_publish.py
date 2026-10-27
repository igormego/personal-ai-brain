import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from test_codex_bridge_review import packet
from personal_ai_brain import codex_bridge_publish as p


@pytest.fixture
def target(packet, monkeypatch):
    key,source,patch,app=packet
    monkeypatch.setattr(p,'project_for',p.review.project_for)
    home=app/'patch-review-v01/publisher-v01';home.mkdir()
    monkeypatch.setattr(p,'store',lambda:home)
    base=p.review.run_git(source,'rev-parse','HEAD').decode().strip()
    validation=json.loads((app/'bridge-v02/ledger/ledger.json').read_text())['tasks'][key]['result']
    original_git=p.review.run_git
    def git(checkout,*args):
        if 'fetch' in args:
            (Path(checkout)/'.git/FETCH_HEAD').write_text(base+'\n');return b''
        return original_git(checkout,*args)
    monkeypatch.setattr(p.review,'run_git',git)
    ws=dict(cwd=p.WSL_ROOT+'/tasks/00000000-0000-4000-8000-000000000001',branch='codex/bridge-task-00000000-0000-4000-8000-000000000001',base_sha=base)
    monkeypatch.setattr(p,'sandbox_validate',lambda *a:(validation,ws))
    monkeypatch.setattr(p,'validate_completion',lambda *a:validation)
    folder=p.prepare_target(key,base)
    return key,base,folder,app


class Backend:
    def __init__(self,m):self.m=m;self.remote=None;self.prs=[];self.pushes=0;self.creates=0;self.fail_push=False;self.fail_create=False
    def inspect(self,branch):return self.remote,self.prs
    def push(self,checkout,branch):
        assert (checkout.parent/'publication.json').is_file()
        self.pushes+=1
        self.remote=p.review.run_git(checkout,'rev-parse','HEAD').decode().strip()
        if self.fail_push:raise RuntimeError('ambiguous push')
    def create(self,m,commit):
        assert json.loads((self.packet/'publication.json').read_text())['phase']=='pr_intent'
        self.creates+=1
        row=dict(draft=True,state='open',html_url='https://github.com/'+m['repository']+'/pull/999',
            head=dict(sha=commit,ref=m['branch'],repo=dict(full_name=m['repository'])),
            base=dict(sha=m['target_base'],ref='main',repo=dict(full_name=m['repository'])))
        self.prs=[row]
        if self.fail_create:raise RuntimeError('ambiguous PR')
        return row


@pytest.fixture
def authorized(target,monkeypatch):
    key,base,folder,app=target
    monkeypatch.setattr(p,'dpapi',lambda data,**kw:b'sealed:'+data if not kw.get('decrypt') else data.removeprefix(b'sealed:'))
    m=json.loads((folder/'manifest.json').read_text())
    (folder/'authorization.dpapi').write_bytes(p.dpapi(p.canonical(p.binding(m))))
    backend=Backend(m);backend.packet=folder
    monkeypatch.setattr(p,'GitHub',lambda repo:backend)
    return key,base,folder,backend


def test_current_target_isolated_repeat_and_original_preserved(target):
    key,base,folder,app=target
    original=app/'patch-review-v01'/key
    assert (folder/'patch.diff').read_bytes()==(original/'patch.diff').read_bytes()
    assert p.prepare_target(key,base)==folder
    assert not (folder/'publication.json').exists()
    assert p.verify_target(key,base)[0]==folder


def test_no_authorization_no_external_backend(target,monkeypatch):
    monkeypatch.setattr(p,'GitHub',lambda *a:pytest.fail('credential acquisition before approval'))
    with pytest.raises(FileNotFoundError):p.publish(target[0],target[1])


def test_full_publish_and_repeat_at_most_once(authorized):
    key,base,folder,backend=authorized
    assert p.publish(key,base)['phase']=='published'
    assert p.publish(key,base)['phase']=='published'
    assert (backend.pushes,backend.creates)==(1,1)
    assert p.review.run_git(folder/'checkout','rev-parse','HEAD').decode().strip()==base


def test_uncertain_push_reconciles_without_repush(authorized):
    key,base,folder,backend=authorized;backend.fail_push=True
    with pytest.raises(RuntimeError):p.publish(key,base)
    assert p.publish(key,base)['phase']=='published'
    assert backend.pushes==1 and backend.creates==1


def test_uncertain_push_absent_remote_never_retries(authorized):
    key,base,folder,backend=authorized;backend.fail_push=True
    with pytest.raises(RuntimeError):p.publish(key,base)
    backend.remote=None
    with pytest.raises(ValueError,match='uncertain_push'):p.publish(key,base)
    assert backend.pushes==1 and backend.creates==0


def test_uncertain_pr_reconciles_without_duplicate(authorized):
    key,base,folder,backend=authorized;backend.fail_create=True
    with pytest.raises(RuntimeError):p.publish(key,base)
    assert p.publish(key,base)['phase']=='published'
    assert backend.creates==1


def test_uncertain_pr_absent_never_retries(authorized):
    key,base,folder,backend=authorized;backend.fail_create=True
    with pytest.raises(RuntimeError):p.publish(key,base)
    backend.prs=[]
    with pytest.raises(ValueError,match='uncertain_pr'):p.publish(key,base)
    assert backend.creates==1


@pytest.mark.parametrize('kind',['foreign_ref','foreign_pr'])
def test_unowned_remote_conflicts(authorized,kind):
    key,base,folder,backend=authorized
    if kind=='foreign_ref':backend.remote='b'*40
    else:backend.prs=[{}]
    with pytest.raises(ValueError,match='unowned_remote'):p.publish(key,base)
    assert backend.pushes==backend.creates==0


def test_non_draft_existing_pr_refused(authorized):
    key,base,folder,backend=authorized;p.publish(key,base)
    backend.prs[0]['draft']=False
    with pytest.raises(ValueError,match='existing_pr_conflict'):p.publish(key,base)
    assert backend.creates==1


def test_upstream_changed_blocks_before_credentials(authorized,monkeypatch):
    key,base,folder,backend=authorized
    monkeypatch.setattr(p.review,'remote_main',lambda repo:'c'*40)
    with pytest.raises(ValueError,match='upstream_changed'):p.publish(key,base)
    assert backend.pushes==backend.creates==0


@pytest.mark.parametrize('key,value',[('repository','attacker/repo'),('branch','main'),('target_base','a'*40),('patch_sha256','b'*64),('original_base','c'*40)])
def test_manifest_binding_tamper_fails(target,key,value):
    task,base,folder,_=target;m=json.loads((folder/'manifest.json').read_text());m[key]=value
    (folder/'manifest.json').write_text(json.dumps(m))
    with pytest.raises(ValueError,match='manifest_drift'):p.verify_target(task,base)


def test_forged_arbitrary_approval_field_is_not_authorization(target,monkeypatch):
    task,base,folder,_=target
    m=json.loads((folder/'manifest.json').read_text());m['approved']=True
    (folder/'manifest.json').write_text(json.dumps(m))
    with pytest.raises(FileNotFoundError):p.publish(task,base)


def test_wrong_sealed_approval_fails(authorized):
    task,base,folder,backend=authorized
    (folder/'authorization.dpapi').write_bytes(b'sealed:{}')
    with pytest.raises(ValueError,match='exact_owner_authorization'):p.publish(task,base)
    assert backend.pushes==0


def test_validation_workspace_cannot_point_at_original(target):
    task,base,folder,_=target;m=json.loads((folder/'manifest.json').read_text())
    m['validation_workspace']['cwd']=p.WSL_ROOT+'/tasks/'+task
    (folder/'manifest.json').write_text(json.dumps(m))
    with pytest.raises(ValueError,match='workspace_mismatch'):p.verify_target(task,base)


def test_dirty_publish_review_refused(authorized):
    task,base,folder,backend=authorized
    (folder/'checkout/extra.txt').write_text('unexpected')
    with pytest.raises(ValueError,match='untracked_publish'):p.publish(task,base)


def test_approval_interactive_exact_and_rechecked(target,monkeypatch):
    task,base,folder,_=target
    monkeypatch.setattr(p,'dpapi',lambda x:x)
    seen=[];monkeypatch.setattr(p,'console_authorize',lambda value:seen.append(value))
    p.authorize(task,base)
    assert seen[0].startswith('AUTHORIZE DRAFT ')
    assert json.loads((folder/'authorization.dpapi').read_bytes())==p.binding(json.loads((folder/'manifest.json').read_text()))
    with pytest.raises(ValueError,match='already_exists'):p.authorize(task,base)


def test_partial_staging_is_not_overwritten(target):
    task,base,folder,_=target
    (folder/'manifest.json').unlink()
    with pytest.raises(FileNotFoundError):p.prepare_target(task,base)


def test_atomic_intent_partial_file_blocks_retry(tmp_path):
    path=tmp_path/'journal.json';path.with_suffix('.pending').write_bytes(b'incomplete')
    with pytest.raises(FileExistsError):p.atomic(path,b'{}')
    assert not path.exists()


def test_no_worker_wiring_and_only_draft_api():
    import inspect
    source=inspect.getsource(p)
    assert 'draft=True' in source and "'merge'" not in source
    for name in ['codex_bridge_v02_runtime.py','codex_bridge_transport_runtime.py']:
        assert 'codex_bridge_publish' not in Path(p.__file__).with_name(name).read_text()


def test_transfer_refuses_affected_blob_changes(target,monkeypatch):
    task,base,folder,_=target
    next_head='d'*40
    monkeypatch.setattr(p.review,'remote_main',lambda repo:next_head)
    real=p.review.run_git
    def git(checkout,*args):
        if args[:2]==('rev-parse','FETCH_HEAD'):return next_head.encode()+b'\n'
        if args[:2]==('merge-base','--is-ancestor'):return b''
        if len(args)>3 and args[0]=='diff' and args[1]==base and args[2]==next_head:return b'changed base blob'
        return real(checkout,*args)
    monkeypatch.setattr(p.review,'run_git',git)
    with pytest.raises(ValueError,match='target_files_changed'):p.prepare_target(task,next_head)


def test_publication_journal_binding_conflict(authorized):
    task,base,folder,backend=authorized;p.publish(task,base)
    state=json.loads((folder/'publication.json').read_text());state['binding']['patch_sha256']='f'*64
    (folder/'publication.json').write_text(json.dumps(state))
    with pytest.raises(ValueError,match='binding_conflict'):p.publish(task,base)
    assert backend.creates==1


def test_pr_payload_draft_only(monkeypatch):
    backend=object.__new__(p.GitHub);backend.repository='igormego/personal-ai-brain'
    seen=[];monkeypatch.setattr(backend,'api',lambda *args:seen.append(args))
    m=dict(task_id='fixture',branch='owner-review/fixture',patch_sha256='a'*64,target_base='b'*40)
    backend.create(m,'c'*40)
    assert seen[0][0]=='POST' and seen[0][2]['draft'] is True and seen[0][2]['base']=='main'


def test_redirects_refused():
    with pytest.raises(RuntimeError,match='redirect_denied'):p.NoRedirect().redirect_request(None,None,None,None,None,None)


@pytest.mark.skipif(p.os.name!='nt',reason='Windows owner DPAPI')
def test_real_owner_dpapi_roundtrip():
    data=b'publisher synthetic authorization test, not an approval'
    sealed=p.dpapi(data)
    assert sealed!=data and p.dpapi(sealed,decrypt=True)==data
    with pytest.raises(RuntimeError):p.dpapi(b'invalid sealed record',decrypt=True)


def test_console_approval_refuses_redirected_input(monkeypatch):
    import io
    monkeypatch.setattr('builtins.open',lambda *a,**k:io.StringIO('AUTHORIZE DRAFT forged\n'))
    with pytest.raises(RuntimeError,match='interactive_owner_console'):p.console_authorize('AUTHORIZE DRAFT trusted')


def test_real_current_main_transfer_preserves_original_diff(target,monkeypatch):
    task,base,folder,app=target
    source=Path(p.project_for('personal-ai-brain').windows_checkout)
    (source/'unrelated.md').write_bytes(b'unrelated newer main\n')
    p.review.run_git(source,'add','unrelated.md')
    p.review.run_git(source,'-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-m','unrelated main advance')
    new=p.review.run_git(source,'rev-parse','HEAD').decode().strip()
    # Existing original source mapping remains pinned to base; fetched main is a
    # separate repository in production, simulated here by fetching local objects.
    p.review.run_git(source,'checkout','--detach',base)
    p.review.run_git(source,'checkout','-B','main',base)
    real=p.review.run_git
    def git(checkout,*args):
        if 'fetch' in args:
            import subprocess
            subprocess.run(['git','-C',str(checkout),'fetch',str(source),new],check=True,capture_output=True)
            return b''
        return real(checkout,*args)
    monkeypatch.setattr(p.review,'run_git',git)
    monkeypatch.setattr(p.review,'remote_main',lambda repo:new)
    validation=json.loads((folder/'manifest.json').read_text())['validation']
    ws=dict(cwd=p.WSL_ROOT+'/tasks/00000000-0000-4000-8000-000000000002',branch='codex/bridge-task-00000000-0000-4000-8000-000000000002',base_sha=new)
    monkeypatch.setattr(p,'sandbox_validate',lambda *a:(validation,ws))
    transferred=p.prepare_target(task,new)
    assert transferred!=folder
    assert p.verify_target(task,new)[1]['target_base']==new
    assert (transferred/'patch.diff').read_bytes()==(folder/'patch.diff').read_bytes()
    assert p.review.run_git(source,'rev-parse','HEAD').decode().strip()==base
