"""Opt-in, offline task-local Python execution using the Work Runner sandbox.

The owner/operator must enable the profile and independently validate effective
Bubblewrap namespace isolation on the actual host before accepting real tasks.
This code never has GitHub credentials, Docker/SSH access or an API model.
Task scripts and inputs are untrusted; only a non-root, network-disabled sandbox
may execute them. Successful interpreter exit is NOT semantic goal acceptance.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import resource
import selectors
import subprocess

from .work_contract import Abort
from .work_sandbox import command, kill_tree, limits
from .work_store import atomic_bytes, atomic_json
from .workspace_artifacts import InputGrant, inspect_outputs, stage_inputs, WorkspaceArtifactDenied

MAX_STDOUT = 16384


def _limits():
    limits()
    resource.setrlimit(resource.RLIMIT_CPU, (30, 30))
    resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))


def execute(request: dict, workspace: Path, artifacts: Path, guard):
    """Return (terminal_status, fixed_code, bounded_artifact_manifest).

    The runner owns the durable task transition and no-retry semantics. It must
    call this only for a validated workspace.python.v1 request after reservation
    and after checking generic_enabled. No repo checkout is required.
    """
    inputs = tuple(
        InputGrant(item["resource_id"], item["path"],
                   base64.b64decode(item["content_b64"], validate=True))
        for item in request["inputs"]
    )
    try:
        staged = stage_inputs(workspace / "inputs", inputs) if inputs else ()
        if not inputs:
            (workspace / "inputs").mkdir(mode=0o700)
        output = workspace / "outputs"
        output.mkdir(mode=0o700)
    except (WorkspaceArtifactDenied, OSError, ValueError):
        return "failed", "generic_input_rejected", ()
    script = artifacts / "generic-script.py"
    atomic_bytes(script, request["script"].encode("utf-8"))
    output_bytes = 0
    process = None
    try:
        guard()
        process = subprocess.Popen(
            command(workspace, script, generic=True),
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
            close_fds=True, start_new_session=True, preexec_fn=_limits,
        )
        with selectors.DefaultSelector() as selector, (artifacts / "generic-execution.log").open("ab", buffering=0) as log:
            selector.register(process.stdout, selectors.EVENT_READ)
            while True:
                guard()
                for key, _ in selector.select(0.05):
                    chunk = os.read(key.fd, 4096)
                    if not chunk:
                        selector.unregister(key.fd)
                    else:
                        output_bytes += len(chunk)
                        if output_bytes > MAX_STDOUT:
                            return "failed", "generic_output_limit", ()
                        log.write(chunk)
                        os.fsync(log.fileno())
                if process.poll() is not None and not selector.get_map():
                    break
        guard()
        if process.returncode != 0:
            return "failed", "generic_script_failed", ()
        # The same bytes must still be present after script execution; mounted
        # inputs are RO. Independent validation also rejects type substitutions.
        current = inspect_outputs(workspace / "inputs") if inputs else ()
        if [(a.path, a.sha256) for a in current] != [(a.path, a.sha256) for a in staged]:
            return "failed", "generic_input_changed", ()
        found = inspect_outputs(output)
        if not found:
            return "failed", "generic_no_artifacts", ()
        manifest = tuple(
            {"path": item.path, "size": item.size, "sha256": item.sha256}
            for item in found
        )
        atomic_json(artifacts / "generic-artifacts.json", {
            "schema_version": "0.1",
            "script_sha256": hashlib.sha256(request["script"].encode("utf-8")).hexdigest(),
            "inputs": [
                {"path": a.path, "size": a.size, "sha256": a.sha256,
                 "resource_id": a.resource_id} for a in staged
            ],
            "outputs": list(manifest),
            "verification": "process_exit_zero_and_artifact_integrity_only",
        })
        atomic_json(artifacts / "test-result.json", {
            "status": "passed",
            "checks": ["sandbox_exited_zero", "input_integrity", "artifact_integrity"],
            "goal_semantics_verified": False,
        })
        return "succeeded", "generic_sandbox_completed", manifest
    except (WorkspaceArtifactDenied, OSError, ValueError):
        return "failed", "generic_artifact_rejected", ()
    finally:
        if process is not None:
            kill_tree(process)
            process.stdout.close()
