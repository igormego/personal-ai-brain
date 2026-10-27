"""Trusted Windows operator publisher. Never imported by either remote worker."""
import argparse
import ctypes
import json
import os
from pathlib import Path
import re
import subprocess
import urllib.request
import uuid

from . import codex_bridge_review as review
from .codex_bridge_transport import worker_lock
from .codex_bridge_v02_policy import approved_changes, project_for
from .codex_bridge_v02_validation import WSL_ROOT, validate_completion
from .codex_bridge_wsl import PREFIX, MATERIALIZE


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def atomic(path, data):
    temporary = path.with_suffix('.pending')
    with temporary.open('xb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    if os.name == 'nt':
        if not ctypes.windll.kernel32.MoveFileExW(ctypes.c_wchar_p(str(temporary)), ctypes.c_wchar_p(str(path)), 9):
            raise RuntimeError('durable_replace_failed')
    else:
        os.replace(temporary, path)


def store():
    if os.name != 'nt':
        raise RuntimeError('windows_operator_required')
    from .codex_bridge_transport_runtime import private_acl
    root = Path(os.environ['LOCALAPPDATA'])/'PersonalAIBrain/patch-review-v01'
    private_acl(root)
    home = root/'publisher-v01'
    if home.is_symlink():
        raise ValueError('unsafe_publisher_store')
    home.mkdir(exist_ok=True)
    return home


def plain(path):
    # Reject Windows junctions as well as symbolic links, including child paths.
    for item in [path, *path.parents]:
        if item.is_symlink() or getattr(item.lstat(), 'st_file_attributes', 0) & 1024:
            raise ValueError('reparse_path_denied')
    return path


def checked_patch(checkout, patch, project_id, expected):
    review.scan_patch(patch)
    if review.run_git(checkout, 'ls-files', '--others', '--exclude-standard').strip():
        raise ValueError('untracked_publish_files')
    if review.run_git(checkout, 'diff', '--cached', '--name-only').strip():
        raise ValueError('staged_publish_files')
    actual = review.run_git(checkout, 'diff', 'HEAD', '--binary', '--no-renames', '--no-ext-diff', '--no-textconv')
    if actual != patch:
        raise ValueError('publish_patch_drift')
    rows = review.run_git(checkout, 'diff', 'HEAD', '--raw', '--no-renames').decode().splitlines()
    for row in rows:
        fields = row.split()
        if fields[:2] != [':100644', '100644'] or fields[4] != 'M':
            raise ValueError('regular_modified_files_only')
    paths = []; additions = deletions = 0
    for row in review.run_git(checkout, 'diff', 'HEAD', '--numstat', '--no-renames').decode().splitlines():
        a, d, name = row.split('\t')
        additions += int(a); deletions += int(d); paths.append(name)
    approved_changes(project_id, paths, additions=additions, deletions=deletions)
    if (paths != expected['files_changed'] or additions != expected['additions'] or
            deletions != expected['deletions']):
        raise ValueError('publish_scope_drift')
    review.run_git(checkout, 'diff', '--check')


def sandbox_validate(checkout, project_id, base, patch):
    """New credential-free WSL workspace; never alter original task workspaces."""
    key = str(uuid.uuid4())
    bundle = checkout.parent/('validation-'+key+'.bundle')
    review.run_git(checkout, 'bundle', 'create', str(bundle), 'HEAD')
    with bundle.open('rb') as stream:
        result = subprocess.run([*PREFIX, '/usr/bin/python3', '-c',
            MATERIALIZE.replace('/home/pabbridge/bridge-v01', WSL_ROOT), key, base],
            stdin=stream, capture_output=True, timeout=120)
    if result.returncode:
        raise RuntimeError('review_materialization_failed')
    workspace = dict(cwd=WSL_ROOT+'/tasks/'+key, branch='codex/bridge-task-'+key, base_sha=base)
    if json.loads(result.stdout) != workspace:
        raise RuntimeError('review_workspace_identity_failed')
    # Fixed code in the same accepted sandbox; patch bytes only on stdin.
    from .codex_bridge_client import OVERRIDES
    args = [*PREFIX, '/usr/local/bin/codex']
    for setting in OVERRIDES:
        if not setting.startswith('windows.'):
            args += ['-c', setting]
    code = "import subprocess,sys; subprocess.run(['/usr/bin/git','-c','core.hooksPath=/dev/null','apply','--whitespace=error','-'],input=sys.stdin.buffer.read(),check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)"
    result = subprocess.run([*args, 'sandbox', '--include-managed-config', '--permission-profile',
        'bridge-workspace-only', '--cd', workspace['cwd'], '--', '/usr/bin/python3', '-c', code],
        input=patch, capture_output=True, timeout=60)
    if result.returncode:
        raise RuntimeError('review_sandbox_apply_failed')
    validation = validate_completion(workspace, project_id)
    if review.export_patch(workspace)[0] != patch:
        raise ValueError('review_validation_patch_drift')
    bundle.unlink()  # disposable exact Git-object transfer, not source/audit data
    return validation, workspace


def prepare_target(task_id, target):
    """Explicit current-main transfer, never an automatic rebase at publish time."""
    if not re.fullmatch('[0-9a-f]{40}', target):
        raise ValueError('exact_target_required')
    review.identifier(task_id)
    original = review.prepare_review(task_id)
    old = json.loads((original/'manifest.json').read_text())
    project = project_for(old['project_id'])
    patch = (original/'patch.diff').read_bytes()
    home = store()
    with worker_lock(home/'operator.lock'):
        if review.remote_main(project.repository) != target:
            raise ValueError('upstream_changed')
        packet = home/review.digest((task_id+target).encode())[:24]
        if packet.exists():
            verify_target(task_id, target, original=original)
            return packet
        packet.mkdir()
        checkout = packet/'checkout'
        # Public source transfer only. No canonical clone fetch/checkout mutation.
        result = subprocess.run(['git', '-c', 'core.longpaths=true', 'clone', '--no-checkout', '--no-local', '--no-hardlinks',
            project.windows_checkout, str(checkout)], capture_output=True, timeout=60)
        if result.returncode:
            raise RuntimeError('publisher_clone_failed')
        url = 'https://github.com/'+project.repository+'.git'
        review.run_git(checkout, 'config', 'core.autocrlf', 'false')
        review.run_git(checkout, '-c', 'credential.helper=', 'fetch', url, 'refs/heads/main')
        if review.run_git(checkout, 'rev-parse', 'FETCH_HEAD').decode().strip() != target:
            raise ValueError('upstream_changed')
        review.run_git(checkout, 'merge-base', '--is-ancestor', old['base_sha'], target)
        paths = old['validation']['files_changed']
        if review.run_git(checkout, 'diff', old['base_sha'], target, '--', *paths):
            raise ValueError('transfer_target_files_changed')
        review.run_git(checkout, 'remote', 'remove', 'origin')
        review.run_git(checkout, 'checkout', '--detach', target)
        (packet/'patch.diff').write_bytes(patch)
        review.run_git(checkout, 'apply', '--check', str(packet/'patch.diff'))
        review.run_git(checkout, 'apply', '--whitespace=error', str(packet/'patch.diff'))
        checked_patch(checkout, patch, project.project_id, old['validation'])
        validation, workspace = sandbox_validate(checkout, project.project_id, target, patch)
        if validation != old['validation'] or review.remote_main(project.repository) != target:
            raise ValueError('transfer_validation_or_upstream_changed')
        manifest = dict(task_id=task_id, repository=project.repository, project_id=project.project_id,
            original_base=old['base_sha'], original_patch_sha256=old['patch_sha256'],
            target_base=target, patch_sha256=review.digest(patch), validation=validation,
            validation_workspace=workspace, branch='owner-review/'+task_id+'/'+target[:12], base_branch='main')
        atomic(packet/'manifest.json', canonical(manifest))
        (packet/'REVIEW.md').write_text('# Current-main owner review\n\n'+
            json.dumps(manifest, indent=2)+'\n\nExact diff: patch.diff. Original packet is preserved.\n'+
            'Affected base blobs are identical; patch applied without modification. Fresh tests ran in a new restricted WSL workspace.\n'+
            'No publication or approval granted. Owner must review this target and complete diff before separate console authorization.\n')
        return packet


def verify_target(task_id, target, *, original=None):
    review.identifier(task_id)
    if not re.fullmatch('[0-9a-f]{40}', target):
        raise ValueError('exact_target_required')
    original = original or review.prepare_review(task_id)
    old = json.loads(plain(original/'manifest.json').read_text())
    packet = plain(store()/review.digest((task_id+target).encode())[:24])
    m = json.loads(plain(packet/'manifest.json').read_text())
    patch = plain(packet/'patch.diff').read_bytes()
    required = dict(task_id=task_id, repository=old['repository'], project_id=old['project_id'],
        original_base=old['base_sha'], original_patch_sha256=old['patch_sha256'], target_base=target,
        patch_sha256=review.digest(patch), validation=old['validation'],
        branch='owner-review/'+task_id+'/'+target[:12], base_branch='main')
    if any(m.get(k) != v for k,v in required.items()) or patch != (original/'patch.diff').read_bytes():
        raise ValueError('target_manifest_drift')
    checkout = plain(packet/'checkout')
    if review.run_git(checkout, 'rev-parse', 'HEAD').decode().strip() != target or review.run_git(checkout, 'remote').strip():
        raise ValueError('target_checkout_identity_failed')
    checked_patch(checkout, patch, m['project_id'], m['validation'])
    if review.remote_main(m['repository']) != target:
        raise ValueError('upstream_changed')
    workspace = m['validation_workspace']
    if workspace.get('base_sha') != target or workspace.get('cwd') == WSL_ROOT+'/tasks/'+task_id:
        raise ValueError('target_validation_workspace_mismatch')
    review.run_git(checkout, 'merge-base', '--is-ancestor', old['base_sha'], target)
    if review.run_git(checkout, 'diff', old['base_sha'], target, '--', *m['validation']['files_changed']):
        raise ValueError('transfer_target_files_changed')
    if validate_completion(workspace, m['project_id']) != m['validation'] or review.export_patch(workspace)[0] != patch:
        raise ValueError('target_validation_drift')
    return packet, m


def binding(manifest):
    return {key:manifest[key] for key in ('task_id','repository','original_base','original_patch_sha256',
        'target_base','patch_sha256','branch','base_branch','validation')}


def dpapi(data, *, decrypt=False):
    """Owner-controlled authorization, sealed to the interactive Windows identity."""
    from ctypes import wintypes
    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_char))]
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char))); output = Blob()
    if decrypt:
        ok = ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output))
    else:
        ok = ctypes.windll.crypt32.CryptProtectData(ctypes.byref(source), ctypes.c_wchar_p('PAB owner draft publication'), None, None, None, 1, ctypes.byref(output))
    if not ok:
        raise RuntimeError('owner_authorization_unavailable')
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        ctypes.windll.kernel32.LocalFree(output.data)


def console_authorize(challenge):
    # Never accept redirected stdin, command-line flags, Issues or model fields.
    with open('CONIN$', 'r') as console, open('CONOUT$', 'w') as output:
        if not console.isatty():
            raise RuntimeError('interactive_owner_console_required')
        output.write('After reviewing the complete diff, type exactly:\n'+challenge+'\n> '); output.flush()
        if console.readline().rstrip('\r\n') != challenge:
            raise ValueError('owner_approval_not_granted')


def authorize(task_id, target):
    packet,m = verify_target(task_id, target)
    b = binding(m)
    challenge = 'AUTHORIZE DRAFT '+review.digest(canonical(b))
    print(json.dumps(b, indent=2), flush=True)
    console_authorize(challenge)
    with worker_lock(store()/'operator.lock'):
        _,fresh = verify_target(task_id, target)
        if binding(fresh) != b:
            raise ValueError('approval_context_changed')
        if (packet/'authorization.dpapi').exists():
            raise ValueError('authorization_already_exists')
        atomic(packet/'authorization.dpapi', dpapi(canonical(b)))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise RuntimeError('github_redirect_denied')


class GitHub:
    """Operator credential manager only; never called by preflight or WSL."""
    def __init__(self, repository):
        if repository not in [project_for(p).repository for p in ('personal-ai-brain','homelab-infrastructure')]:
            raise ValueError('repository_denied')
        self.repository = repository
        result = subprocess.run(['git', '-c', 'credential.interactive=false', 'credential', 'fill'],
            input=b'protocol=https\nhost=github.com\n\n', capture_output=True, timeout=30)
        if result.returncode:
            raise RuntimeError('operator_credential_unavailable')
        fields = dict(line.split(b'=',1) for line in result.stdout.splitlines() if b'=' in line)
        self._token = fields.get(b'password', b'').decode()
        if not self._token:
            raise RuntimeError('operator_credential_unavailable')
        if self.api('GET','/user').get('login') != 'igormego':
            raise RuntimeError('operator_identity_mismatch')
        repo = self.api('GET','/repos/'+repository)
        if repo.get('full_name') != repository or repo.get('default_branch') != 'main':
            raise RuntimeError('github_repository_mismatch')

    def api(self, method, path, data=None):
        request = urllib.request.Request('https://api.github.com'+path, method=method,
            data=None if data is None else canonical(data), headers={'Authorization':'Bearer '+self._token,
            'Accept':'application/vnd.github+json','Content-Type':'application/json'})
        try:
            with urllib.request.build_opener(NoRedirect).open(request, timeout=30) as response:
                body = response.read(2_097_153)
            if len(body)>2_097_152:
                raise RuntimeError('github_response_limit')
            return json.loads(body)
        except Exception:
            raise RuntimeError('github_outcome_requires_reconciliation') from None

    def inspect(self, branch):
        from urllib.parse import quote
        refs = self.api('GET','/repos/'+self.repository+'/git/matching-refs/heads/'+quote(branch, safe=''))
        refs = [r for r in refs if r['ref']=='refs/heads/'+branch]
        prs = []
        for page in range(1,12):
            rows = self.api('GET','/repos/'+self.repository+'/pulls?state=all&head=igormego:'+quote(branch,safe='')+'&per_page=100&page='+str(page))
            prs.extend(rows)
            if len(rows)<100:break
        else:raise RuntimeError('github_reconciliation_limit')
        if len(refs)>1 or len(prs)>1:
            raise ValueError('ambiguous_publication')
        return (refs[0]['object']['sha'] if refs else None), prs

    def push(self, checkout, branch):
        # Global operator credential helper; no task-controlled Git config/hooks.
        result = subprocess.run(['git','-C',str(checkout),'-c','core.hooksPath=/dev/null',
            '-c','credential.interactive=false','push','--porcelain',
            '--force-with-lease=refs/heads/'+branch+':',
            'https://github.com/'+self.repository+'.git','HEAD:refs/heads/'+branch],
            capture_output=True, timeout=120)
        if result.returncode:
            raise RuntimeError('push_outcome_requires_reconciliation')

    def create(self, manifest, commit):
        return self.api('POST','/repos/'+self.repository+'/pulls', dict(
            title='Review remote task '+manifest['task_id'], head=manifest['branch'],base='main',draft=True,
            body='Owner-approved retained patch.\n\nPatch SHA256: '+manifest['patch_sha256']+
            '\nTarget base: '+manifest['target_base']+'\nPublication commit: '+commit+
            '\n\nNo merge or deployment authorization.'))


def matching_pr(pr, m, commit):
    return (pr.get('draft') is True and pr.get('state')=='open' and
        pr.get('head',{}).get('sha')==commit and pr.get('head',{}).get('ref')==m['branch'] and
        pr.get('head',{}).get('repo',{}).get('full_name')==m['repository'] and
        pr.get('base',{}).get('ref')=='main' and pr.get('base',{}).get('sha')==m['target_base'] and
        pr.get('base',{}).get('repo',{}).get('full_name')==m['repository'])


def publish(task_id, target):
    home = store()
    with worker_lock(home/'operator.lock'):
        packet,m = verify_target(task_id,target)
        b = binding(m)
        if json.loads(dpapi(plain(packet/'authorization.dpapi').read_bytes(), decrypt=True)) != b:
            raise ValueError('exact_owner_authorization_required')
        journal = packet/'publication.json'
        backend = GitHub(m['repository'])
        remote,prs = backend.inspect(m['branch'])
        if journal.exists():
            state = json.loads(plain(journal).read_text())
            if state['binding'] != b:
                raise ValueError('publication_binding_conflict')
            commit = state['commit']
            if remote != commit:
                raise ValueError('uncertain_push_do_not_retry')
            if prs:
                if not matching_pr(prs[0],m,commit):
                    raise ValueError('existing_pr_conflict')
                state.update(phase='published',pr_url=prs[0]['html_url'])
                atomic(journal,canonical(state));return state
            if state['phase'] in ('pr_intent','published'):
                raise ValueError('uncertain_pr_do_not_retry')
            if state['phase'] != 'push_intent':
                raise ValueError('unknown_publication_state')
        else:
            if remote or prs:
                raise ValueError('unowned_remote_publication')
            checkout = packet/'publish-checkout'
            if checkout.exists():
                raise ValueError('partial_publish_checkout')
            result = subprocess.run(['git','-c','core.longpaths=true','clone','--no-local','--no-hardlinks','--no-checkout',
                str(packet/'checkout'),str(checkout)],capture_output=True,timeout=60)
            if result.returncode:raise RuntimeError('publish_clone_failed')
            review.run_git(checkout,'remote','remove','origin')
            review.run_git(checkout,'config','core.autocrlf','false')
            review.run_git(checkout,'checkout','-b',m['branch'],target)
            review.run_git(checkout,'apply','--whitespace=error',str(packet/'patch.diff'))
            checked_patch(checkout,(packet/'patch.diff').read_bytes(),m['project_id'],m['validation'])
            review.run_git(checkout,'add','--',*m['validation']['files_changed'])
            review.run_git(checkout,'-c','user.name=PAB Owner Publisher','-c','user.email=pab-publisher@localhost',
                'commit','-m','Owner-reviewed remote task '+task_id)
            if review.run_git(checkout,'diff',target,'HEAD','--binary','--no-renames','--no-ext-diff','--no-textconv') != (packet/'patch.diff').read_bytes():
                raise ValueError('publication_commit_patch_drift')
            commit = review.run_git(checkout,'rev-parse','HEAD').decode().strip()
            state = dict(binding=b,commit=commit,phase='push_intent')
            if review.remote_main(m['repository']) != target:raise ValueError('upstream_changed')
            atomic(journal,canonical(state))  # BEFORE any external write, including an uncertain push
            backend.push(checkout,m['branch'])
            remote,prs = backend.inspect(m['branch'])
            if remote != commit or prs:raise ValueError('push_reconciliation_failed')
        if review.remote_main(m['repository']) != target:raise ValueError('upstream_changed')
        state['phase']='pr_intent';atomic(journal,canonical(state))
        pr = backend.create(m,commit)
        if not matching_pr(pr,m,commit):raise ValueError('created_pr_requires_reconciliation')
        state.update(phase='published',pr_url=pr['html_url']);atomic(journal,canonical(state))
        return state


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation',choices=['transfer','preflight','authorize','publish'])
    parser.add_argument('task_id');parser.add_argument('target',help='exact reviewed main commit SHA')
    args=parser.parse_args()
    review.identifier(args.task_id)
    if not re.fullmatch('[0-9a-f]{40}',args.target):parser.error('exact target SHA required')
    if args.operation=='transfer':print(prepare_target(args.task_id,args.target))
    elif args.operation=='preflight':
        packet,m=verify_target(args.task_id,args.target);print(json.dumps(binding(m),indent=2));print(packet)
    elif args.operation=='authorize':authorize(args.task_id,args.target)
    else:print(json.dumps(publish(args.task_id,args.target),indent=2))


if __name__=='__main__':main()
