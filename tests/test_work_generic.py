"""Opt-in generic Work Runner source contract / integration regression.

The mock-executor and local-script tests are NOT live Bubblewrap acceptance.
The production profile stays disabled until actual host namespace checks pass.
"""
import base64
import hashlib
import json
from pathlib import Path
import sys
from uuid import uuid4

import pytest

from personal_ai_brain import work_contract as contract
from personal_ai_brain import work_generic
from personal_ai_brain.work_handoff import BASE, summary, arguments
from personal_ai_brain.work_runner import Runner
from personal_ai_brain.work_sandbox import command
from personal_ai_brain.work_store import Store


def task(**kw):
    data = {
        "schema_version": "0.1",
        "task_id": str(uuid4()),
        "task_type": contract.GENERIC_TYPE,
        "repository": contract.REPOSITORY,
        "base_ref": BASE,
        "goal": "Produce an output from a previously unseen local computation.",
        "acceptance_criteria": ["output file is present"],
        "requested_at": contract.timestamp(),
        "deadline_seconds": 45,
        "script": "from pathlib import Path\nPath('outputs/result.txt').write_text('ready')\n",
        "inputs": [],
    }
    data.update(kw)
    return data


def parsed(**kw):
    return contract.parse(json.dumps(task(**kw)).encode())


def test_generic_request_accepted_by_existing_handoff_contract():
    value = parsed()
    assert arguments("work.submit", {"contract": value}, BASE)["contract"] == value


@pytest.mark.parametrize("extra", [
    {"shell": "id"},
    {"script": ""},
    {"script": "\x00"},
    {"script": "x" * 5000},
    {"inputs": [{"path": "../escape", "content_b64": "eA=="}]},
    {"inputs": [{"resource_id": "homelab/privileged", "path": "a.txt", "content_b64": "eA=="}]},
    {"inputs": [{"path": "a.txt", "content_b64": "!!"}]},
    {"inputs": [{"path": "a.txt", "content_b64": "eA"}]},
    {"inputs": [{"path": "a.txt", "content_b64": "eA=="}, {"path": "a.txt", "content_b64": "eQ=="}]},
    {"deadline_seconds": 181},
    {"repository": "somebody/other"},
    {"base_ref": "b" * 40},
])
def test_generic_bad_envelopes_are_rejected(extra):
    with pytest.raises(contract.Rejected):
        parsed(**extra)


def test_generic_contract_is_fail_closed_on_size_and_bad_input_type():
    with pytest.raises(contract.Rejected):
        parsed(inputs=[{"path": "x.txt", "content_b64": "eA=="}] * 9)
    with pytest.raises(contract.Rejected):
        parsed(inputs=[{"path": "x.txt", "content_b64": 123}])
    with pytest.raises(contract.Rejected):
        contract.parse(b" " * 8193)


def test_existing_bubblewrap_profile_uses_ro_input_overmount_without_git():
    args = command(Path("/scratch/task"), Path("/private/script.py"), generic=True)
    joined = " ".join(args)
    assert "--unshare-all" in args and "--cap-drop" in args
    assert "--clearenv" in args and "--disable-userns" in args
    assert "--ro-bind /scratch/task/inputs /workspace/inputs" in joined
    assert "/workspace/.git" not in args
    assert "/private/script.py" in args
    assert args[-4:] == ["/usr/bin/python3", "-I", "-B", "/fixture.py"]
    old = command(Path("/scratch/task"), Path("/private/fixture.py"))
    assert "/workspace/.git" in old and "/workspace/inputs" not in old


def test_runner_gate_is_disabled_by_default_and_retains_record(tmp_path):
    store = Store(tmp_path)
    value = parsed()
    record, created = store.submit(value, BASE)
    assert created and record["status"] == "submitted"
    policy = {"base_ref": BASE, "seed": "/never/reached", "min_free_bytes": 0}
    assert Runner(store, policy).tick()
    record = store.load(value["task_id"])
    assert record["status"] == "blocked"
    assert record["attempts"] == 1
    assert record["history"][-1]["code"] == "generic_not_enabled"
    assert not (store.workspaces / value["task_id"]).exists()


def test_generic_runner_reuses_single_durable_task_and_status_manifest(tmp_path, monkeypatch):
    store = Store(tmp_path)
    value = parsed()
    store.submit(value, BASE)

    def fake_execute(request, workspace, artifacts, guard):
        guard()
        assert request["script"] == value["script"]
        assert workspace.name == "scratch"
        assert workspace.parent.name == value["task_id"]
        return "succeeded", "generic_sandbox_completed", (
            {"path": "results/output.json", "size": 2, "sha256": hashlib.sha256(b"{}").hexdigest()},
        )

    monkeypatch.setattr(work_generic, "execute", fake_execute)
    policy = {"base_ref": BASE, "generic_enabled": True,
              "seed": "/never/reached", "min_free_bytes": 0}
    runner = Runner(store, policy)
    assert runner.tick()
    assert not runner.tick()
    item = store.load(value["task_id"])
    assert item["attempts"] == 1 and item["status"] == "succeeded"
    result = summary(item)["result"]
    assert result["artifact_manifest"][0]["path"] == "results/output.json"
    assert result["goal_semantics_verified"] is False
    replay, created = store.submit(value, BASE)
    assert not created and replay["attempts"] == 1
    with pytest.raises(contract.Rejected, match="conflict"):
        store.submit({**value, "script": "print('changed')"}, BASE)


def test_executes_trusted_ci_fixture_with_input_and_produces_artifact(tmp_path, monkeypatch):
    # This is a *trusted CI fixture* run by Python, not proof of Bwrap isolation.
    # In production the command is always built by work_sandbox.command.
    monkeypatch.setattr(work_generic, "command", lambda workspace, script, generic:
                        [sys.executable, "-I", "-B", str(script)])
    data = base64.b64encode(b"3,7,11").decode()
    value = parsed(
        script="from pathlib import Path\n"
               "nums = list(map(int, Path('inputs/nums.txt').read_text().split(',')))\n"
               "Path('outputs/total.txt').write_text(str(sum(nums)))\n",
        inputs=[{"path": "nums.txt", "content_b64": data}],
    )
    workspace = tmp_path / "scratch"
    workspace.mkdir()
    artifacts = tmp_path / "audit"
    artifacts.mkdir()
    status, code, manifest = work_generic.execute(value, workspace, artifacts, lambda: None)
    assert (status, code) == ("succeeded", "generic_sandbox_completed")
    assert (workspace / "outputs/total.txt").read_text() == "21"
    assert len(manifest) == 1 and manifest[0]["sha256"] == hashlib.sha256(b"21").hexdigest()
    report = json.loads((artifacts / "test-result.json").read_text())
    assert report["goal_semantics_verified"] is False
    provenance = json.loads((artifacts / "generic-artifacts.json").read_text())
    assert provenance["inputs"][0]["resource_id"] == "user-inline"


def test_empty_artifact_does_not_become_success(tmp_path, monkeypatch):
    monkeypatch.setattr(work_generic, "command", lambda workspace, script, generic:
                        [sys.executable, "-I", "-B", str(script)])
    value = parsed(script="pass\n")
    workspace = tmp_path / "scratch"
    workspace.mkdir()
    artifacts = tmp_path / "audit"
    artifacts.mkdir()
    status, code, files = work_generic.execute(value, workspace, artifacts, lambda: None)
    assert (status, code, files) == ("failed", "generic_no_artifacts", ())
