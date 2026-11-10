"""Opt-in offline script execution through Work Runner's rootless Bubblewrap.

No writable host mount is presented to a model-written script. It can write only
a size-capped private tmpfs, read caller-inline inputs as RO, and return up to
16 KiB of UTF-8 JSON on stdout. Host-side validation stages and hashes outputs.
No remote shell/SSH/Docker, GitHub credentials or model inference is exposed.

"Execution completed" is not independent semantic verification of the user goal.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
from pathlib import Path
import resource
import selectors
import subprocess

from .work_sandbox import command, kill_tree, limits
from .work_store import atomic_bytes, atomic_json
from .work_contract import RESOURCE_TYPE
from .workspace_artifacts import InputGrant, inspect_outputs, stage_inputs, WorkspaceArtifactDenied

MAX_STDOUT = 16384


def _limits():
    limits()
    resource.setrlimit(resource.RLIMIT_CPU, (30, 30))
    resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))


def _unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate_key")
        value[key] = item
    return value


def _output_grants(raw: bytes):
    """Decode only a fixed format, never a script instruction or host path."""
    try:
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique,
                             parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        if type(payload) is not dict or set(payload) != {"files"}:
            raise ValueError("format")
        files = payload["files"]
        if type(files) is not list or not 1 <= len(files) <= 16:
            raise ValueError("count")
        grants = []
        for entry in files:
            if type(entry) is not dict or set(entry) != {"path", "content_b64"}:
                raise ValueError("entry")
            if type(entry["path"]) is not str or type(entry["content_b64"]) is not str:
                raise ValueError("entry")
            content = base64.b64decode(entry["content_b64"], validate=True)
            if base64.b64encode(content).decode("ascii") != entry["content_b64"]:
                raise ValueError("noncanonical")
            grants.append(InputGrant("sandbox-output", entry["path"], content))
        return tuple(grants)
    except (UnicodeError, ValueError, binascii.Error, TypeError, RecursionError):
        raise WorkspaceArtifactDenied("invalid_generic_output") from None


def execute(request: dict, workspace: Path, artifacts: Path, guard, *,
            trusted_grants: tuple[InputGrant, ...] | None = None):
    """Return (terminal state, fixed failure/success code, safe artifact summary).

    Must be invoked only after durable reservation and operator opt-in.
    Source bytes and inputs are private; only validated path/size/digest
    metadata can pass through Work Runner status. No blind retry after failure.
    """
    try:
        if request["task_type"] == RESOURCE_TYPE:
            # Only a trusted Work Runner supplies these bytes after host-only
            # source validation. No input from the GitHub contract can become
            # a privileged resource merely by claiming a resource ID.
            if (type(trusted_grants) is not tuple or len(trusted_grants) != 1
                    or request["inputs"] != []
                    or trusted_grants[0].resource_id != request["resource_id"]
                    or trusted_grants[0].path != "host-snapshot.json"):
                raise WorkspaceArtifactDenied("resource_grant_unavailable")
            inputs = trusted_grants
        else:
            if trusted_grants is not None:
                raise WorkspaceArtifactDenied("untrusted_resource_input")
            inputs = tuple(InputGrant("user-inline", item["path"],
                          base64.b64decode(item["content_b64"], validate=True))
                           for item in request["inputs"])
        if inputs:
            staged = stage_inputs(workspace / "inputs", inputs)
        else:
            (workspace / "inputs").mkdir(mode=0o700)
            staged = ()
    except (WorkspaceArtifactDenied, OSError, ValueError, binascii.Error):
        return "failed", "generic_input_rejected", ()

    script = artifacts / "generic-script.py"
    atomic_bytes(script, request["script"].encode("utf-8"))
    process = None
    output_bytes = bytearray()
    try:
        guard()
        process = subprocess.Popen(
            command(workspace, script, generic=True),
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
            close_fds=True, start_new_session=True, preexec_fn=_limits,
        )
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while True:
                guard()
                for key, _ in selector.select(0.05):
                    chunk = os.read(key.fd, 4096)
                    if not chunk:
                        selector.unregister(key.fd)
                    else:
                        if len(output_bytes) + len(chunk) > MAX_STDOUT:
                            return "failed", "generic_output_limit", ()
                        output_bytes.extend(chunk)
                if process.poll() is not None and not selector.get_map():
                    break
        guard()
        if process.returncode != 0:
            return "failed", "generic_script_failed", ()
        # User task has no host-writable mounts; the only outward channel is
        # this bounded stdout envelope. Stage output only after independent
        # host-side path/type/size/canonical-base64 verification.
        grants = _output_grants(bytes(output_bytes))
        staged_outputs = stage_inputs(workspace / "outputs", grants)
        verified = inspect_outputs(workspace / "outputs")
        if [(a.path, a.size, a.sha256) for a in staged_outputs] != [
                (a.path, a.size, a.sha256) for a in verified]:
            raise WorkspaceArtifactDenied("output_integrity_failed")
        # Only small, safe UTF-8 text outputs are returned inline. Full file
        # contents otherwise remain in the private worker artifact store.
        # The control service still independently redacts outgoing fields.
        preview_budget = 1024
        items = []
        content_by_path = {a.path: a.content for a in grants}
        for artifact in verified:
            entry = {"path": artifact.path, "size": artifact.size,
                     "sha256": artifact.sha256}
            if (artifact.path.lower().endswith((".txt", ".md", ".json", ".py"))
                    and artifact.size <= 512 and preview_budget >= artifact.size):
                try:
                    preview = content_by_path[artifact.path].decode("utf-8")
                    if all(ch in "\n\t" or ord(ch) >= 32 for ch in preview):
                        entry["preview_text"] = preview
                        preview_budget -= artifact.size
                except UnicodeError:
                    pass
            items.append(entry)
        manifest = tuple(items)
        atomic_json(artifacts / "generic-artifacts.json", {
            "schema_version": "0.1",
            "script_sha256": hashlib.sha256(request["script"].encode("utf-8")).hexdigest(),
            "inputs": [
                {"path": a.path, "size": a.size, "sha256": a.sha256,
                 "resource_id": a.resource_id} for a in staged
            ],
            "outputs": list(manifest),
            "verification": "sandbox_exit_zero_and_artifact_integrity_only",
        })
        atomic_json(artifacts / "test-result.json", {
            "status": "passed",
            "checks": ["sandbox_exited_zero", "bounded_output", "artifact_integrity"],
            "goal_semantics_verified": False,
        })
        return "succeeded", "generic_sandbox_completed", manifest
    except (WorkspaceArtifactDenied, OSError, ValueError):
        return "failed", "generic_artifact_rejected", ()
    finally:
        if process is not None:
            kill_tree(process)
            process.stdout.close()
