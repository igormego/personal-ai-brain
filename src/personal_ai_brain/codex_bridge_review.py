"""Operator-only, read-only task export/staging. No push, PR or approval endpoint."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import urllib.request
import uuid

from .codex_bridge_core import identifier
from .codex_bridge_transport import worker_lock
from .codex_bridge_v02_policy import approved_changes, project_for
from .codex_bridge_v02_validation import WSL_ROOT, validate_completion
from .codex_bridge_wsl import PREFIX
from .codex_bridge_client import OVERRIDES

MAX_PATCH = 2_097_152
COLLECT = r'''
import base64,hashlib,json,pathlib,subprocess,sys
cwd,base=sys.argv[1:]
def git(*args):
 r=subprocess.run(['/usr/bin/git','--git-dir='+cwd+'/.git','--work-tree='+cwd,
 '-c','core.hooksPath=/dev/null','-c','core.fsmonitor=false','-c','diff.external=',
 *args],capture_output=True,timeout=30)
 if r.returncode or len(r.stdout)>2097152:raise RuntimeError('export_git_failed')
 return r.stdout
if git('rev-parse','HEAD').decode().strip()!=base:raise RuntimeError('unexpected_commit')
if git('remote').strip() or git('diff','--cached','--name-only').strip():raise RuntimeError('unsupported_git_state')
if git('ls-files','--others','--exclude-standard').strip():raise RuntimeError('untracked_export_not_supported')
git('diff','HEAD','--check')
raw=git('diff','HEAD','--raw','--no-renames','--no-ext-diff','--no-textconv').decode().splitlines()
for row in raw:
 fields=row.split()
 if fields[0]!=':100644' or fields[1]!='100644' or fields[4]!='M':raise RuntimeError('regular_modified_files_only')
patch=git('diff','HEAD','--binary','--no-renames','--no-ext-diff','--no-textconv')
if not patch or b'GIT binary patch' in patch:raise RuntimeError('unsupported_patch')
print(json.dumps({'patch':base64.b64encode(patch).decode(),'paths':git('diff','HEAD','--name-only','--no-renames').decode().splitlines()}))
'''


def digest(data):
    return hashlib.sha256(data).hexdigest()


def run_git(checkout, *args):
    r = subprocess.run(['git','-C',str(checkout),'-c','core.longpaths=true','-c','core.hooksPath=/dev/null',
        '-c','core.fsmonitor=false','-c','diff.external=',*args],
        capture_output=True,timeout=60)
    if r.returncode or len(r.stdout)>MAX_PATCH:
        raise RuntimeError('review_git_failed')
    return r.stdout


def task_record(ledger, task_id):
    identifier(task_id)
    task = ledger.get('tasks',{}).get(task_id)
    if not task or task.get('state')!='succeeded' or task.get('attempts')!=1:
        raise ValueError('successful_single_attempt_required')
    project = project_for(task.get('project_id'))
    if task.get('repository') != project.repository:
        raise ValueError('repository_mismatch')
    if not re.fullmatch('[0-9a-f]{40}',task.get('base_sha','')):
        raise ValueError('invalid_base')
    return task, project


def scan_patch(patch):
    if not patch or len(patch)>MAX_PATCH or b'\0' in patch:
        raise ValueError('patch_size_or_binary')
    patch.decode('utf-8')
    if re.search(rb'github_pat_[A-Za-z0-9_]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY|(?i:authorization:|bearer )[A-Za-z0-9+/_.=-]{12,}',patch):
        raise ValueError('secret_pattern_detected')


def export_patch(workspace):
    args=[*PREFIX,'/usr/local/bin/codex']
    for item in OVERRIDES:
        if not item.startswith('windows.'):args.extend(['-c',item])
    args.extend(['sandbox','--include-managed-config','--permission-profile',
        'bridge-workspace-only','--cd',workspace['cwd'],'--','/usr/bin/python3',
        '-c',COLLECT,workspace['cwd'],workspace['base_sha']])
    r=subprocess.run(args,capture_output=True,timeout=60)
    if r.returncode or len(r.stdout)>3_000_000:raise RuntimeError('sandbox_export_failed')
    data=json.loads(r.stdout);patch=base64.b64decode(data['patch'],validate=True)
    scan_patch(patch)
    return patch,data['paths']


def remote_main(repository):
    # Public metadata only; no publishing credential and no task-controlled URL.
    project=next(p for p in (project_for('personal-ai-brain'),project_for('homelab-infrastructure')) if p.repository==repository)
    request=urllib.request.Request('https://api.github.com/repos/'+project.repository+'/commits/main',headers={'Accept':'application/vnd.github+json'})
    with urllib.request.urlopen(request,timeout=20) as response:
        raw=response.read(1_048_577)
    if len(raw)>1_048_576:raise RuntimeError('remote_metadata_limit')
    sha=json.loads(raw)['sha']
    if not re.fullmatch('[0-9a-f]{40}',sha):raise RuntimeError('remote_identity_failed')
    return sha


def prepare_review(task_id):
    if os.name!='nt':raise RuntimeError('windows_operator_required')
    from .codex_bridge_transport_runtime import private_acl
    app=Path(os.environ['LOCALAPPDATA'])/'PersonalAIBrain'
    ledger_path=app/'bridge-v02/ledger/ledger.json'
    delivery_path=app/'bridge-v02/transport/delivery.json'
    for p in [ledger_path.parent,delivery_path.parent]:private_acl(p)
    before=ledger_path.read_bytes();delivery_before=delivery_path.read_bytes()
    task,project=task_record(json.loads(before),task_id)
    delivery=json.loads(delivery_before)
    results=[]
    for row in delivery.get('requests',{}).values():
        if row.get('phase')!='delivered':continue
        try:body=json.loads(row['body'].split('```json\n')[1].split('\n```')[0])
        except (KeyError,ValueError,IndexError):continue
        if body.get('task_id')==task_id and body.get('status')=='succeeded':results.append(body)
    if not results:raise ValueError('successful_delivery_required')
    source=Path(project.windows_checkout)
    if run_git(source,'config','--get','remote.origin.url').decode().strip()!='https://github.com/'+project.repository+'.git':raise ValueError('source_identity_failed')
    if run_git(source,'branch','--show-current').strip()!=b'main':raise ValueError('trusted_source_not_main')
    if run_git(source,'rev-parse','HEAD').decode().strip()!=task['base_sha'] or run_git(source,'status','--porcelain').strip():raise ValueError('trusted_source_stale_or_dirty')
    workspace={'cwd':WSL_ROOT+'/tasks/'+task_id,'branch':'codex/bridge-task-'+task_id,'base_sha':task['base_sha']}
    patch,paths=export_patch(workspace)
    validation=validate_completion(workspace,project.project_id)
    if validation!=task.get('result') or export_patch(workspace)!=(patch,paths):raise ValueError('validation_or_patch_drift')
    approved_changes(project.project_id,paths,additions=validation['additions'],deletions=validation['deletions'])
    if paths!=validation['files_changed']:raise ValueError('file_manifest_mismatch')
    if any(any(b.get(k)!=v for k,v in {'base_sha':task['base_sha'],'project_id':project.project_id,**validation}.items()) for b in results):raise ValueError('delivery_mismatch')
    if ledger_path.read_bytes()!=before or delivery_path.read_bytes()!=delivery_before:raise ValueError('audit_changed_during_review')
    home=app/'patch-review-v01'
    if not home.exists():
        home.mkdir()
        literal=str(home).replace("'", "''")
        script="$p='"+literal+"'; $sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value; & icacls $p /inheritance:r /grant:r ('*'+$sid+':(OI)(CI)F') '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' | Out-Null; exit $LASTEXITCODE"
        result=subprocess.run(['powershell','-NoProfile','-NonInteractive','-Command','-'],input=script,text=True,capture_output=True,timeout=15)
        if result.returncode:raise RuntimeError('review_acl_failed')
    if home.is_symlink():raise ValueError('unsafe_review_root')
    private_acl(home)
    packet=home/task_id;sha=digest(patch)
    with worker_lock(home/'review.lock'):
        if packet.exists():
            if packet.is_symlink():raise ValueError('unsafe_packet')
            recorded=json.loads((packet/'manifest.json').read_text())
            if any(recorded.get(k)!=v for k,v in {'task_id':task_id,'repository':project.repository,'base_sha':task['base_sha'],'validation':validation,'proposed_branch':'owner-review/'+task_id,'publication':'NOT_APPROVED_NOT_ATTEMPTED'}.items()):raise ValueError('review_manifest_drift')
            if run_git(packet/'checkout','rev-parse','HEAD').decode().strip()!=task['base_sha'] or run_git(packet/'checkout','ls-files','--others','--exclude-standard').strip():raise ValueError('review_checkout_drift')
            if recorded['patch_sha256']!=sha or digest((packet/'patch.diff').read_bytes())!=sha:raise ValueError('review_conflict')
            if run_git(packet/'checkout','diff','HEAD','--binary','--no-renames','--no-ext-diff','--no-textconv')!=patch:raise ValueError('review_checkout_drift')
            return packet
        packet.mkdir()
        # No resume/overwrite after interrupted staging: an existing partial packet
        # fails closed above, for explicit operator inspection.
        checkout=packet/'checkout'
        subprocess.run(['git','clone','--no-local','--no-hardlinks','--no-checkout',str(source),str(checkout)],check=True,capture_output=True,timeout=60)
        branch='owner-review/'+task_id
        run_git(checkout,'config','core.autocrlf','false')
        run_git(checkout,'remote','remove','origin')
        run_git(checkout,'checkout','-b',branch,task['base_sha'])
        (packet/'patch.diff').write_bytes(patch)
        run_git(checkout,'apply','--check',str(packet/'patch.diff'))
        run_git(checkout,'apply','--whitespace=error',str(packet/'patch.diff'))
        if run_git(checkout,'diff','HEAD','--binary','--no-renames','--no-ext-diff','--no-textconv')!=patch:raise ValueError('staged_patch_mismatch')
        head=remote_main(project.repository)
        manifest={'version':1,'task_id':task_id,'project_id':project.project_id,'repository':project.repository,'base_sha':task['base_sha'],'remote_main_sha':head,'patch_sha256':sha,'validation':validation,'proposed_branch':branch,'proposed_pr_title':'Review remote task '+task_id,'publication':'NOT_APPROVED_NOT_ATTEMPTED','publication_blocker':'stale_base' if head!=task['base_sha'] else 'owner_approval_required'}
        (packet/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
        (packet/'REVIEW.md').write_text('# Owner review packet\n\nRepository: '+project.repository+'\nTask: '+task_id+'\nBase: '+task['base_sha']+'\nPatch SHA256: '+sha+'\n\nFiles:\n'+''.join('- '+p+'\n' for p in paths)+'\nValidation: '+validation['test_summary']+'\nPublication gate: '+manifest['publication_blocker']+'\n\nInspect patch.diff and checkout. No repository code runs in this Windows checkout. Tests ran only in the original restricted WSL sandbox against the identical patch.\n\nExplicit owner approval must bind repository, task, base, patch checksum and proposed branch. This tool implements no publication operation. A separate future trusted publisher must recheck current main, patch integrity, approval and uncertain prior outcomes before any write. Never rebase silently, retry uncertain publication, merge or deploy automatically.\n')
    return packet


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('task_id')
    args=parser.parse_args()
    print(prepare_review(args.task_id))


if __name__=='__main__':
    main()
