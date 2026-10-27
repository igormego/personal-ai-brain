"""Opt-in Windows v0.2 coding bridge. NOT installed by a GitHub merge.

Separate exact Issues title, private delivery and execution ledgers, and trusted
local project mapping. Existing v0.1 process, secrets and ledger are preserved.
Never imports remote project code into the trusted Windows runtime.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import threading

from .codex_bridge_core import Ledger, identifier
from .codex_bridge_transport import (
    GitHub, TransportError, Worker, envelope, serve,
)
from .codex_bridge_transport_runtime import private_acl
from .codex_bridge_v02_policy import (
    PROTOCOL, project_for, validate_request,
)
from .codex_bridge_v02_transport import parse_request
from .codex_bridge_v02_validation import WSL_ROOT, validate_completion
from .codex_bridge_wsl import (
    MATERIALIZE, PREFIX, WslBackend, run_local,
)

# This is an installed-code constant, NEVER chosen by the task or GitHub Issue.
MATERIALIZE_V02 = MATERIALIZE.replace(
    "/home/pabbridge/bridge-v01", WSL_ROOT,
)
if "/home/pabbridge/bridge-v01" in MATERIALIZE_V02:
    raise RuntimeError("materializer_policy_mismatch")


class TrustedV02Backend(WslBackend):
    """Canonical trusted checkout → private Git bundle → isolated WSL task."""

    def __init__(self, project_id: str):
        self.project = project_for(project_id)
        self.checkout = self.project.windows_checkout

    def _git(self, *args, timeout: int = 60, **kwargs):
        result = subprocess.run(
            ["git", "-C", self.checkout, "-c", "core.hooksPath=/dev/null",
             "-c", "core.fsmonitor=false", *args],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            check=False, timeout=timeout, **kwargs,
        )
        if result.returncode or len(result.stdout) > 4096:
            raise ValueError("trusted_checkout_unavailable")
        return result.stdout.decode("ascii", errors="strict").strip()

    def head(self) -> str:
        # Dedicated independent clones only; never follow development worktrees.
        if self._git("rev-parse", "--is-inside-work-tree") != "true":
            raise ValueError("trusted_checkout_unavailable")
        if self._git("config", "--get", "remote.origin.url") != (
                "https://github.com/" + self.project.repository + ".git"):
            raise ValueError("trusted_checkout_wrong_repository")
        if self._git("status", "--porcelain", "--untracked-files=all"):
            raise ValueError("trusted_checkout_dirty")
        branch = self._git("branch", "--show-current")
        if branch != "main":
            raise ValueError("trusted_checkout_not_main")
        sha = self._git("rev-parse", "HEAD")
        if not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise ValueError("trusted_checkout_unavailable")
        if self._git("rev-parse", "refs/remotes/origin/main") != sha:
            raise ValueError("trusted_checkout_unapproved_revision")
        return sha

    def prepare(self, task_id: str, approved_base: str) -> dict:
        identifier(task_id)
        if (not re.fullmatch("[0-9a-f]{40}", approved_base) or
                self.head() != approved_base):
            raise ValueError("base_not_approved")
        # Git bundle reads committed HEAD only, not untracked/working-tree
        # contents or machine-local credentials. No GitHub fetch or SSH.
        with tempfile.TemporaryDirectory(prefix="pab-v02-git-bundle-") as temp:
            bundle = Path(temp) / "source.bundle"
            result = subprocess.run(
                ["git", "-C", self.checkout, "-c", "core.hooksPath=/dev/null",
                 "bundle", "create", str(bundle), "HEAD"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=60, check=False,
            )
            if result.returncode or not bundle.is_file():
                raise ValueError("source_bundle_unavailable")
            with bundle.open("rb") as stream:
                wsl = subprocess.run(
                    [*PREFIX, "/usr/bin/python3", "-c", MATERIALIZE_V02,
                     task_id, approved_base],
                    stdin=stream, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    timeout=120, check=False,
                )
        if wsl.returncode or len(wsl.stdout) > 4096:
            raise ValueError("workspace_materialization_failed")
        try:
            actual = json.loads(wsl.stdout)
        except (UnicodeError, json.JSONDecodeError):
            raise ValueError("workspace_materialization_failed") from None
        expected = dict(
            cwd=WSL_ROOT + "/tasks/" + task_id,
            branch="codex/bridge-task-" + task_id,
            base_sha=approved_base,
        )
        if actual != expected:
            raise ValueError("workspace_mapping_mismatch")
        return expected


def repository_for(project_id: str) -> str:
    return project_for(project_id).repository


def envelope_v02(result: dict, payload: dict) -> dict:
    """Reuse proven v0.1 result projection; replace only protocol identity."""
    safe = envelope(result, payload)
    safe["transport_version"] = PROTOCOL
    # Never allow a result to assert publication. Successful coding is a local,
    # uncommitted workspace patch, not a GitHub commit, PR or deployment.
    safe.pop("commit_sha", None)
    return safe


def execute(ledger: Ledger, payload: dict) -> dict:
    # Policy and canonical directory are independently fixed by trusted code.
    selected = validate_request(payload)
    backend = TrustedV02Backend(selected["project_id"])
    return run_local(
        ledger, selected, timeout=900, trusted_backend=backend,
        trusted_source=backend,
        validate_completion=lambda workspace: validate_completion(
            workspace, selected["project_id"],
        ),
    )


def locations():
    if os.name != "nt":
        raise RuntimeError("windows_required")
    app = Path(os.environ["LOCALAPPDATA"]) / "PersonalAIBrain"
    # No copying or rewriting the v0.1 token. Both workers have only narrow
    # private Issues access, and their ledgers/delivery namespaces are separate.
    token = app / "bridge-v01" / "transport" / "github-token"
    base = app / "bridge-v02"
    return token, base / "ledger", base / "transport"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=["check", "serve"])
    op = parser.parse_args().operation
    token, ledger_root, transport_root = locations()
    for path in (token.parent, token, ledger_root.parent, ledger_root, transport_root):
        private_acl(path)
    if token.stat().st_size > 1024:
        raise RuntimeError("credential_invalid")
    client = GitHub(token.read_text(encoding="utf-8").strip())
    client.verify()
    if op == "check":
        if any(i.get("title") == PROTOCOL and "pull_request" not in i
               for i in client.issues()):
            raise RuntimeError("v02_activation_queue_not_empty")
        try:
            client.request("GET", "/contents/core/project-map.yaml")
        except TransportError as error:
            if error.http_status != 403:
                raise
        else:
            raise RuntimeError("contents_permission_must_be_denied")
        for project_id in ("personal-ai-brain", "homelab-infrastructure"):
            TrustedV02Backend(project_id).head()
        print("v0.2 private mailbox, ACL and canonical main check PASS; no execution or live acceptance implied.")
        return
    serve(Worker(
        transport_root,
        Ledger(ledger_root, validator=validate_request,
               repository_for=repository_for),
        client, execute,
        title=PROTOCOL, parser=parse_request,
        formatter=envelope_v02, marker_version="v2",
    ), threading.Event())


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("Remote Codex v0.2 stopped; inspect private sanitized health, ACL and trusted checkout.")
        raise SystemExit(1) from None
