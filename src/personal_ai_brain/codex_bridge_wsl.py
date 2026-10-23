"""Trusted local WSL transport. Nothing in this module is a task RPC surface."""
import json
from pathlib import Path
import subprocess
import tempfile

from .codex_bridge_core import CWD, identifier

BACKEND = "wsl-codex-v01"
ROOT = "/home/pabbridge/bridge-v01"
PREFIX = [r"C:\Windows\System32\wsl.exe", "--distribution", "Debian",
          "--user", "pabbridge", "--cd", "/home/pabbridge", "--exec",
          "/usr/bin/env", "-i", "HOME=/home/pabbridge",
          "PATH=/usr/local/bin:/usr/bin:/bin", "LANG=C.UTF-8",
          "CODEX_HOME=/home/pabbridge/.codex"]

# Trusted setup runs outside the command sandbox, receiving only a Git bundle.
# The model never receives this program, its stdin, or the Windows source path.
MATERIALIZE = r'''
import json, os, pathlib, subprocess, sys, tempfile, uuid
task, base = sys.argv[1:]
if str(uuid.UUID(task)) != task or len(base) != 40 or any(c not in '0123456789abcdef' for c in base):
    raise ValueError('invalid_identity')
root = pathlib.Path('/home/pabbridge/bridge-v01')
for folder in (root, root/'tasks', root/'state'):
    folder.mkdir(mode=0o700, exist_ok=True)
    if folder.is_symlink() or folder.resolve() != folder:
        raise ValueError('unsafe_root')
path = root/'tasks'/task
if path.exists() or path.is_symlink():
    raise ValueError('workspace_already_allocated')
branch = 'codex/bridge-task-' + task
def git(*args):
    return subprocess.run(['/usr/bin/git', *args], check=True, capture_output=True,
                          text=True, timeout=60).stdout.strip()
with tempfile.NamedTemporaryFile(dir=root/'state', suffix='.bundle') as bundle:
    while chunk := sys.stdin.buffer.read(1024*1024):
        bundle.write(chunk)
    bundle.flush()
    git('clone', '--quiet', '--no-checkout', '--no-hardlinks', bundle.name, str(path))
    if git('-C', str(path), 'rev-parse', base+'^{commit}') != base:
        raise ValueError('base_mismatch')
    git('-C', str(path), 'remote', 'remove', 'origin')
    git('-C', str(path), 'checkout', '--quiet', '-b', branch, base)
    git('-C', str(path), 'config', 'user.name', 'PAB Internal Coding Task')
    git('-C', str(path), 'config', 'user.email', 'pab-task@localhost')
    if git('-C', str(path), 'rev-parse', 'HEAD') != base:
        raise ValueError('base_mismatch')
print(json.dumps(dict(cwd=str(path), branch=branch, base_sha=base)))
'''


def select_backend(name):
    if name != BACKEND:
        raise ValueError("unknown_backend")
    return WslBackend()


class WslBackend:
    def launch_args(self):
        from .codex_bridge_client import OVERRIDES
        args = [*PREFIX, "/usr/local/bin/codex", "app-server", "--listen", "stdio://"]
        for setting in OVERRIDES:
            if not setting.startswith("windows."):
                args.extend(["-c", setting])
        return args

    def prepare(self, task_id, approved_base):
        identifier(task_id)
        if (not isinstance(approved_base, str) or len(approved_base) != 40 or
                any(c not in "0123456789abcdef" for c in approved_base)):
            raise ValueError("invalid_approved_base")
        from .codex_bridge_workspace import git
        if git("rev-parse", "HEAD") != approved_base:
            raise ValueError("base_not_approved")
        # Private transient transfer stays on Windows; only tracked Git objects
        # cross into WSL. Never transfer working-tree files or credentials.
        with tempfile.TemporaryDirectory(prefix="pab-git-transfer-") as temporary:
            bundle = Path(temporary) / "source.bundle"
            subprocess.run(["git", "-C", CWD, "bundle", "create", str(bundle), "HEAD"],
                           check=True, capture_output=True, timeout=60)
            with bundle.open("rb") as stream:
                result = subprocess.run([*PREFIX, "/usr/bin/python3", "-c", MATERIALIZE,
                                         task_id, approved_base], stdin=stream,
                                        capture_output=True, timeout=120, check=True)
        if len(result.stdout) > 4096:
            raise ValueError("invalid_workspace_result")
        workspace = json.loads(result.stdout)
        expected = dict(cwd=ROOT + "/tasks/" + task_id,
                        branch="codex/bridge-task-" + task_id, base_sha=approved_base)
        if workspace != expected:
            raise ValueError("workspace_mapping_mismatch")
        return expected


def run_local(ledger, payload, *, validate_completion=None, timeout=120):
    """Intentionally invoked local API; no listener, poller or task-selected backend.

    The validator is trusted operator code, never supplied by the task payload.
    A startup failure consumes the reservation and cannot silently retry later.
    """
    from .codex_bridge_client import AppServer, execute
    from .codex_bridge_core import public_result
    task = ledger.submit(payload)
    if task["state"] != "submitted":
        return public_result(task)
    try:
        client = AppServer(execution_backend=BACKEND)
    except Exception:
        try:
            ledger.transition(task["task_id"], "submitted", "reserved")
            task = ledger.transition(task["task_id"], "reserved", "uncertain",
                                     failure_class="backend_startup_failed")
        except ValueError as error:
            if str(error) != "state_conflict":
                raise
            task = ledger.load()["tasks"][task["task_id"]]
        return public_result(task)
    try:
        try:
            task = execute(ledger, payload, client, timeout=timeout,
                           validate_completion=validate_completion)
        except ValueError as error:
            if str(error) != "state_conflict":
                raise
            task = ledger.load()["tasks"][task["task_id"]]
        return public_result(task)
    finally:
        client.close()
