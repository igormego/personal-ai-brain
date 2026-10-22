"""Trusted task workspaces; no caller-supplied path, URL or base selection."""
from pathlib import Path
import subprocess
from .codex_bridge_core import CWD, identifier

ROOT = Path(CWD).parent / ".pab-bridge-workspaces"

def git(*args):
    result = subprocess.run(["git", "-C", CWD, *args], capture_output=True,
                            text=True, timeout=60, check=True)
    return result.stdout.strip()

def prepare(task_id, approved_base):
    identifier(task_id)
    # approved_base is supplied by trusted local policy, never derived from goal text.
    if git("rev-parse", approved_base + "^{commit}") != approved_base:
        raise ValueError("invalid_approved_base")
    root = ROOT.resolve()
    root.mkdir(parents=True, exist_ok=True)
    path = root / task_id
    branch = "codex/bridge-task-" + task_id
    if path.exists() or git("branch", "--list", branch):
        raise ValueError("workspace_already_allocated")
    subprocess.run(["git", "-C", CWD, "worktree", "add", "-b", branch,
                    str(path), approved_base], check=True, capture_output=True,
                   timeout=60)
    return {"cwd": str(path), "branch": branch, "base_sha": approved_base}

def cleanup(task_id):
    identifier(task_id)
    path = ROOT.resolve() / task_id
    branch = "codex/bridge-task-" + task_id
    if path.is_symlink() or path.parent != ROOT.resolve():
        raise ValueError("unsafe_workspace")
    actual = subprocess.run(["git", "-C", str(path), "symbolic-ref", "--short", "HEAD"],
                            check=True, capture_output=True, text=True, timeout=10).stdout.strip()
    if actual != branch:
        raise ValueError("workspace_owner_mismatch")
    # No force: dirty artifacts must be preserved.
    git("worktree", "remove", str(path))
    git("branch", "-D", branch)
