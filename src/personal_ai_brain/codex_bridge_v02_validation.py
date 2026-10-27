"""Trusted source-only v0.2 completion gate for *future* Local/Wsl operator wiring.

The policy source and validator are inserted by the installed Windows owner
process, never read from the model-editable WSL task checkout. All tests run
inside the same root-denied, offline Codex sandbox as accepted v0.1. This file
alone is not a transport listener, installer or remote execution capability.
"""
from __future__ import annotations

import inspect
import json
from pathlib import Path
import re
import subprocess
import uuid

from . import codex_bridge_v02_policy as policy

WSL_ROOT = "/home/pabbridge/bridge-v02"
MAX_RESULT_BYTES = 16384

# Executed only by trusted Windows-side validate_completion() through the fixed
# WSL Codex sandbox. No calls to shell, network, ssh, Docker or publisher.
_BODY = r'''
import hashlib, json, os, pathlib, re, resource, stat, subprocess, sys, tempfile, uuid

cwd, base, project_id = sys.argv[1:]
profile = project_for(project_id)
task = pathlib.Path(cwd).name
if (str(uuid.UUID(task)) != task or uuid.UUID(task).version != 4 or
        cwd != '/home/pabbridge/bridge-v02/tasks/' + task):
    raise RuntimeError('unsafe_workspace')
if not re.fullmatch('[0-9a-f]{40}', base):
    raise RuntimeError('invalid_base')
root = pathlib.Path(cwd)
if root.is_symlink() or root.resolve() != root or not root.is_dir():
    raise RuntimeError('unsafe_workspace')
os.chdir(cwd)

def git(*args):
    command = ['/usr/bin/git', '-c', 'core.hooksPath=/dev/null',
               '-c', 'core.fsmonitor=false', '-c', 'diff.external=', *args]
    r = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                       timeout=30, check=False)
    if r.returncode or len(r.stdout) > 2_097_152:
        raise RuntimeError('git_validation_failed')
    return r.stdout

def inspect_tree():
    if git('rev-parse','HEAD').decode().strip() != base:
        raise RuntimeError('unexpected_commit')
    git('diff','HEAD','--check','--no-ext-diff','--no-textconv')
    tracked = [p.decode('ascii') for p in git(
        'diff','HEAD','--name-only','--no-renames','--no-ext-diff',
        '--no-textconv','-z').split(b'\0') if p]
    untracked = [p.decode('ascii') for p in git(
        'ls-files','--others','--exclude-standard','-z').split(b'\0') if p]
    if len(set(tracked + untracked)) != len(tracked) + len(untracked):
        raise RuntimeError('duplicate_path')
    paths = tracked + untracked
    if not paths:
        raise RuntimeError('change_scope_denied')
    totals = {}
    for row in git('diff','HEAD','--numstat','--no-renames',
                   '--no-ext-diff','--no-textconv','-z').split(b'\0'):
        if not row: continue
        fields = row.split(b'\t',2)
        if len(fields) != 3 or fields[0] == b'-' or fields[1] == b'-':
            raise RuntimeError('binary_or_invalid_diff')
        totals[fields[2].decode('ascii')] = (int(fields[0]), int(fields[1]))
    before = hashlib.sha256()
    additions = sum(v[0] for v in totals.values())
    deletions = sum(v[1] for v in totals.values())
    for name in sorted(paths):
        if not allowed_change(project_id,name):
            raise RuntimeError('change_scope_denied')
        path = root/name
        if (path.is_symlink() or path.resolve() != path or not path.is_file() or
                not stat.S_ISREG(path.stat().st_mode) or path.stat().st_size > MAX_FILE_BYTES):
            raise RuntimeError('unsafe_changed_file')
        body = path.read_bytes()
        if b'\0' in body:
            raise RuntimeError('binary_changed_file')
        try: body.decode('utf-8')
        except UnicodeDecodeError: raise RuntimeError('binary_changed_file') from None
        before.update(name.encode('ascii')+b'\0')
        before.update(hashlib.sha256(body).digest())
        if name in untracked:
            additions += body.count(b'\n') + (1 if body and not body.endswith(b'\n') else 0)
    sorted_paths = approved_changes(project_id, paths, additions=additions, deletions=deletions)
    patch = git('diff','HEAD','--binary','--no-renames','--no-ext-diff','--no-textconv')
    before.update(hashlib.sha256(patch).digest())
    before.update(base.encode('ascii'))
    return (sorted_paths, additions, deletions, before.hexdigest())

before = inspect_tree()
with tempfile.TemporaryDirectory(prefix='.pab-v02-validate-',dir=cwd) as directory:
    log_path = pathlib.Path(directory)/'tests.log'
    if profile.test_profile == 'pab-pytest':
        command = ['/usr/bin/python3','-m','pytest','-q','-p','no:cacheprovider',
                   '--basetemp',str(pathlib.Path(directory)/'pytest'), 'tests']
        expression = r'(?m)^([0-9]{1,6}) passed in [0-9.]+s\s*$'
    elif profile.test_profile == 'movie-intelligence-node':
        tests = sorted((root/'tools/movie-intelligence/test').glob('*.test.mjs'))
        if not tests or len(tests)>100:
            raise RuntimeError('trusted_tests_missing')
        command = ['/usr/bin/node','--test','--test-reporter=tap',
                   *map(str,tests)]
        expression = r'(?m)^# pass ([0-9]{1,6})\s*$'
    else:
        raise RuntimeError('unknown_test_profile')
    with log_path.open('wb') as handle:
        def limit_output():
            resource.setrlimit(resource.RLIMIT_FSIZE,(1_048_576,1_048_576))
        # Keep executed repository tests inside a minimal, credential-free
        # environment. Suppress Python bytecode files that would otherwise
        # contaminate the validated patch after successful tests.
        test_env = {'PATH':'/usr/bin:/bin', 'LANG':'C.UTF-8', 'HOME':cwd,
                    'PYTHONDONTWRITEBYTECODE':'1'}
        r = subprocess.run(command, stdout=handle, stderr=subprocess.STDOUT,
                           env=test_env, timeout=240, check=False,
                           preexec_fn=limit_output)
    if r.returncode != 0 or log_path.stat().st_size > 1_048_576:
        raise RuntimeError('sandboxed_tests_failed')
    tail = log_path.read_bytes()[-8192:].decode('utf-8',errors='replace')
    match = re.findall(expression,tail)
    if not match or int(match[-1]) < 1:
        raise RuntimeError('missing_test_summary')
if inspect_tree() != before:
    raise RuntimeError('tests_changed_tree')
files,additions,deletions,_=before
print(json.dumps(dict(files_changed=files,additions=additions,deletions=deletions,
       test_summary=match[-1]+' tests passed in restricted Linux sandbox')))
'''


def validator_script() -> str:
    # Uses the *installed trusted policy module*, never imports or executes the
    # Codex-edited task checkout's policy. Keep policy+validator versions pinned.
    source = inspect.getsource(policy)
    script = source + "\n" + _BODY
    compile(script, "<trusted_v02_validator>", "exec")
    return script


def validate_completion(workspace: object, project_id: object) -> dict:
    """Fixed sandbox invocation; **not wired** into current v0.1 worker."""
    from .codex_bridge_client import OVERRIDES
    from .codex_bridge_wsl import PREFIX

    profile = policy.project_for(project_id)
    if type(workspace) is not dict or set(workspace) != {"cwd", "branch", "base_sha"}:
        raise ValueError("invalid_workspace")
    base = workspace["base_sha"]
    cwd = workspace["cwd"]
    if type(cwd) is not str or type(base) is not str or not re.fullmatch("[0-9a-f]{40}", base):
        raise ValueError("invalid_workspace")
    task = cwd.removeprefix(WSL_ROOT + "/tasks/")
    try:
        parsed = uuid.UUID(task)
    except (ValueError, AttributeError):
        raise ValueError("invalid_workspace") from None
    if (parsed.version != 4 or str(parsed) != task or
            cwd != WSL_ROOT + "/tasks/" + task or
            workspace["branch"] != "codex/bridge-task-" + task):
        raise ValueError("invalid_workspace")

    args = [*PREFIX, "/usr/local/bin/codex"]
    for setting in OVERRIDES:
        if not setting.startswith("windows."):
            args.extend(["-c",setting])
    args += [
        "sandbox", "--include-managed-config", "--permission-profile",
        "bridge-workspace-only", "--cd", cwd, "--",
        "/usr/bin/python3", "-c", validator_script(), cwd, base, profile.project_id,
    ]
    try:
        result = subprocess.run(args, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, timeout=320,
                                check=False)
        if result.returncode or len(result.stdout) > MAX_RESULT_BYTES:
            raise ValueError("validator_failed")
        payload = json.loads(result.stdout)
        if type(payload) is not dict or set(payload) != {
            "files_changed", "additions", "deletions", "test_summary",
        }:
            raise ValueError("validator_failed")
        policy.approved_changes(
            project_id, payload["files_changed"],
            additions=payload["additions"], deletions=payload["deletions"],
        )
        if (type(payload["test_summary"]) is not str or not
                re.fullmatch(r"[1-9][0-9]{0,5} tests passed in restricted Linux sandbox",
                             payload["test_summary"])):
            raise ValueError("validator_failed")
        return payload
    except (OSError, ValueError, UnicodeError, subprocess.TimeoutExpired,
            RecursionError, TypeError):
        raise RuntimeError("v02_completion_validation_failed") from None
