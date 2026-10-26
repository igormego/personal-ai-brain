"""Trusted Windows installation binding. No request-selected runtime settings."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import threading

from .codex_bridge_core import CWD, Ledger, identifier
from .codex_bridge_transport import GitHub, TransportError, Worker, serve
from .codex_bridge_wsl import PREFIX, ROOT, run_local


def locations():
    if os.name != 'nt':
        raise RuntimeError('windows_required')
    base = Path(os.environ['LOCALAPPDATA']) / 'PersonalAIBrain' / 'bridge-v01'
    return base / 'ledger', base / 'transport'


def private_acl(path):
    # Paths are local trusted configuration, passed as a positional argument.
    script = r'''$p=$args[0]; $me=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value;
    $a=Get-Acl -LiteralPath $p; $ok=$a.AreAccessRulesProtected;
    if ((Get-Item -LiteralPath $p).Attributes -band [IO.FileAttributes]::ReparsePoint) {$ok=$false};
    foreach($r in $a.Access){$sid=$r.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value;
      if($r.AccessControlType -eq 'Allow' -and $sid -notin @($me,'S-1-5-18','S-1-5-32-544')){$ok=$false}};
    if(-not $ok){exit 1}'''
    # -Command arguments become source in PowerShell; use stdin and literal-safe quoting.
    literal = str(path).replace("'", "''")
    source = "$args=@('" + literal + "'); " + script
    result = subprocess.run([r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe',
                             '-NoProfile', '-NonInteractive', '-Command', '-'],
                            input=source, text=True, capture_output=True, timeout=15)
    if result.returncode:
        raise RuntimeError('private_acl_required')


# Trusted policy is supplied by the installed worker, never imported from task code.
# Both editable files map to one pure, socket-free test module. No fallback suite.
VALIDATE = r'''
import hashlib, json, pathlib, re, subprocess, sys, tempfile, os, resource
cwd, base = sys.argv[1:]
os.chdir(cwd)
allowed = {'src/personal_ai_brain/state_quality.py', 'tests/test_state_quality.py'}
def git(*args):
    r=subprocess.run(['/usr/bin/git','-c','core.hooksPath=/dev/null','-c','core.fsmonitor=false',
        '-c','diff.external=','-c','core.worktree='+cwd,*args],capture_output=True,timeout=30)
    if r.returncode or len(r.stdout)>1048576: raise RuntimeError('git_validation_failed')
    return r.stdout

def inspect():
    if git('rev-parse','HEAD').decode().strip() != base: raise RuntimeError('unexpected_commit')
    if git('ls-files','--others','--exclude-standard'): raise RuntimeError('untracked_files')
    git('diff','HEAD','--check')
    rows=git('diff','HEAD','--numstat','--no-renames','--no-ext-diff','--no-textconv').decode().splitlines()
    files=[]; additions=deletions=0
    for row in rows:
        a,d,p=row.split('\t')
        if p not in allowed: raise RuntimeError('change_scope_denied')
        path=pathlib.Path(p)
        if path.is_symlink() or path.resolve()!=pathlib.Path(cwd)/p:
            raise RuntimeError('symlink_denied')
        if not path.is_file(): raise RuntimeError('file_required')
        if git('ls-tree',base,'--',p).split()[0]!=b'100644': raise RuntimeError('regular_file_required')
        files.append(p); additions+=int(a); deletions+=int(d)
    if not files or len(files)>2 or additions+deletions>3000: raise RuntimeError('change_size_denied')
    # Compare complete patch bytes, not just numstat: same-size edits must fail too.
    patch=git('diff','HEAD','--binary','--no-renames','--no-ext-diff','--no-textconv')
    return files,additions,deletions,hashlib.sha256(patch).hexdigest()

before=inspect()
with tempfile.TemporaryDirectory(prefix='.pab-validation-',dir=cwd) as temporary:
    name=pathlib.Path(temporary)/'output'
    with name.open('wb') as log:
        def limits(): resource.setrlimit(resource.RLIMIT_FSIZE,(1048576,1048576))
        r=subprocess.run(['/usr/bin/python3','-m','pytest','-q','-p','no:cacheprovider',
             '--basetemp',str(pathlib.Path(temporary)/'pytest'), 'tests/test_state_quality.py'],
             stdout=log,stderr=subprocess.STDOUT,timeout=180,preexec_fn=limits)
    if r.returncode: raise RuntimeError('sandboxed_tests_failed')
    if name.stat().st_size>1048576: raise RuntimeError('test_output_limit')
    with name.open('rb') as log:
        log.seek(max(0,name.stat().st_size-4096)); tail=log.read().decode(errors='replace')
    matches=re.findall(r'(?m)^(\d{1,6}) passed(?: in [0-9.]+s)?\s*$',tail)
    if not matches: raise RuntimeError('missing_test_summary')
if inspect()!=before: raise RuntimeError('tests_changed_tree')
files,additions,deletions,_=before
print(json.dumps(dict(files_changed=files,additions=additions,deletions=deletions,
                     test_summary=matches[-1]+' tests passed in restricted Linux sandbox')))
'''


def validate_completion(workspace):
    from .codex_bridge_client import OVERRIDES
    task = workspace['cwd'].removeprefix(ROOT + '/tasks/')
    identifier(task)
    if workspace['cwd'] != ROOT + '/tasks/' + task or not re.fullmatch('[0-9a-f]{40}', workspace['base_sha']):
        raise ValueError('invalid_workspace')
    args = [*PREFIX, '/usr/local/bin/codex']
    for setting in OVERRIDES:
        if not setting.startswith('windows.'):
            args.extend(['-c', setting])
    args += ['sandbox', '--include-managed-config', '--permission-profile', 'bridge-workspace-only',
             '--cd', workspace['cwd'], '--', '/usr/bin/python3', '-c', VALIDATE,
             workspace['cwd'], workspace['base_sha']]
    result = subprocess.run(args, capture_output=True, timeout=210)
    if result.returncode or len(result.stdout) > 16384:
        raise RuntimeError('completion_validation_failed')
    return json.loads(result.stdout)


def execute(ledger, payload):
    return run_local(ledger, payload, validate_completion=validate_completion, timeout=600)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('operation', choices=['check', 'serve'])
    args = parser.parse_args()
    ledger_path, root = locations()
    token_path = root / 'github-token'
    for path in (root, token_path, ledger_path):
        private_acl(path)
    if token_path.stat().st_size > 1024:
        raise RuntimeError('credential_invalid')
    client = GitHub(token_path.read_text(encoding='utf-8').strip())
    client.verify()
    if args.operation == 'check':
        if any(i.get('title') == 'pab-codex/v0.1' and 'pull_request' not in i for i in client.issues()):
            raise RuntimeError('activation_queue_not_empty')
        try:
            client.request('GET', '/contents/core/project-map.yaml')
        except TransportError as error:
            if error.http_status != 403:
                raise
        else:
            raise RuntimeError('contents_permission_must_be_denied')
        print('Private Issues read PASS; private Contents denied; ACL PASS. Write grant requires owner UI confirmation until external acceptance.')
        return
    os.chdir(CWD)
    serve(Worker(root, Ledger(ledger_path), client, execute), threading.Event())


if __name__ == '__main__':
    try:
        main()
    except Exception:
        # Never echo exception/HTTP bodies, credentials or environment values.
        print('Bridge transport stopped; inspect sanitized health status or credential ACL.')
        raise SystemExit(1) from None
