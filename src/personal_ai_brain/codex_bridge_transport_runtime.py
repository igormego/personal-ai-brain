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


# Fixed validator runs all task code under the accepted restrictive Linux sandbox.
# No task-controlled test command, paths, environment, or unrestricted output.
VALIDATE = r'''
import json, pathlib, re, subprocess, sys, tempfile, os, resource
cwd, base = sys.argv[1:]
os.chdir(cwd)
def git(*args):
    r=subprocess.run(['/usr/bin/git','-c','core.hooksPath=/dev/null','-c','core.fsmonitor=false',
        '-c','diff.external=','-c','core.worktree='+cwd,*args],capture_output=True,timeout=30)
    if r.returncode or len(r.stdout)>65536: raise RuntimeError('git_validation_failed')
    return r.stdout.decode().strip()
if git('rev-parse','HEAD') != base: raise RuntimeError('unexpected_commit')
if git('ls-files','--others','--exclude-standard'): raise RuntimeError('untracked_files')
git('diff','--check')
rows=git('diff','HEAD','--numstat','--no-ext-diff','--no-textconv').splitlines()
files=[]; additions=deletions=0
for row in rows:
    a,d,p=row.split('\t')
    if not re.fullmatch(r'(src/personal_ai_brain/[a-z0-9_]+\.py|tests/test_[a-z0-9_]+\.py|docs/[a-zA-Z0-9_-]+\.md|README\.md)',p):
        raise RuntimeError('change_scope_denied')
    if pathlib.Path(p).is_symlink(): raise RuntimeError('symlink_denied')
    files.append(p); additions+=int(a); deletions+=int(d)
if not files or len(files)>32 or additions+deletions>3000: raise RuntimeError('change_size_denied')
fd,name=tempfile.mkstemp(prefix='.pab-validation-',dir=cwd)
try:
    with os.fdopen(fd,'wb') as log:
        def limits(): resource.setrlimit(resource.RLIMIT_FSIZE,(1048576,1048576))
        r=subprocess.run(['/usr/bin/python3','-m','pytest','-q','-p','no:cacheprovider'],
             stdout=log,stderr=subprocess.STDOUT,timeout=180,preexec_fn=limits)
    if r.returncode: raise RuntimeError('sandboxed_tests_failed')
    with open(name,'rb') as log:
        log.seek(max(0,os.path.getsize(name)-4096)); tail=log.read().decode(errors='replace')
    matches=re.findall(r'(\d+) passed',tail)
    if not matches: raise RuntimeError('missing_test_summary')
finally: pathlib.Path(name).unlink(missing_ok=True)
if git('rev-parse','HEAD')!=base: raise RuntimeError('unexpected_commit')
if git('diff','HEAD','--numstat','--no-ext-diff','--no-textconv').splitlines()!=rows:
    raise RuntimeError('tests_changed_tree')
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
