"""Source-only trusted snapshot-to-sandbox grant, no Homelab deployment."""
import hashlib
import json
from pathlib import Path
import sys
from uuid import uuid4

import pytest

from personal_ai_brain import work_contract, work_generic
from personal_ai_brain.host_audit import validate_snapshot
from personal_ai_brain.work_handoff import BASE, summary
from personal_ai_brain.work_runner import Runner
from personal_ai_brain.work_resource_grants import ResourceGrantUnavailable, load_host_resource
from personal_ai_brain.work_store import Store
from personal_ai_brain.workspace_artifacts import InputGrant


def contract(**overrides):
    value = {
        "schema_version": "0.1",
        "task_id": str(uuid4()),
        "task_type": work_contract.RESOURCE_TYPE,
        "repository": work_contract.REPOSITORY,
        "base_ref": BASE,
        "goal": "Read the registered host snapshot and report its status.",
        "acceptance_criteria": ["Read only a verified source-bound host snapshot"],
        "requested_at": work_contract.timestamp(),
        "deadline_seconds": 60,
        "script": (
            "import json,base64\nfrom pathlib import Path\n"
            "d=json.loads(Path('/workspace/inputs/host-snapshot.json').read_text())\n"
            "raw=json.dumps({'running':d['docker']['running']}).encode()\n"
            "print(json.dumps({'files':[{'path':'answer.json',"
            "'content_b64':base64.b64encode(raw).decode()}]}))\n"
        ),
        "inputs": [],
        "resource_id": work_contract.HOST_RESOURCE,
    }
    value.update(overrides)
    return value


def parsed(**changes):
    return work_contract.parse(json.dumps(contract(**changes)).encode())


def snapshot():
    # Valid host-collected fixture, no real host reads.
    from datetime import datetime, timezone
    return {
        "schema_version": "0.1",
        "collected_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "cpu_logical": 4, "memory_mib": {"total": 7335, "available": 4862},
        "disk_free_gib": {"root": 72, "data": 11824},
        "docker": {"status": "available", "running": 13, "unhealthy": 0},
        "control": {
            "status": "available",
            "image_id": "sha256:" + "a"*64,
            "read_only_rootfs": True, "privileged": False,
            "no_new_privileges": True, "docker_socket": False,
            "published_ports": 0,
        },
    }


def write_snapshot(path):
    path.write_text(json.dumps(snapshot()))
    path.chmod(0o600)


def test_trusted_loader_validates_private_source_and_receipt(tmp_path):
    path = tmp_path / "host.json"
    write_snapshot(path)
    grants, receipt = load_host_resource(work_contract.HOST_RESOURCE, path=path)
    assert len(grants) == 1
    grant = grants[0]
    assert grant.path == "host-snapshot.json"
    assert grant.resource_id == work_contract.HOST_RESOURCE
    assert receipt["sha256"] == hashlib.sha256(grant.content).hexdigest()
    assert receipt["size"] == len(grant.content)
    value = json.loads(grant.content)
    assert value["freshness"] == "current" and value["source"] == receipt["source"]
    assert value["docker"]["running"] == 13
    assert "api_key" not in grant.content.decode()


@pytest.mark.parametrize("bad", [
    {"resource_id": "homelab/private"},
    {"resource_id": "/var/lib/personal-ai-brain"},
    {"inputs": [{"path": "host-snapshot.json", "content_b64": "eA=="}]},
    {"resource_id": None}, {"resource_id": "homelab/host-snapshot", "cmd": "id"},
])
def test_contract_disallows_caller_forged_privileged_inputs(bad):
    with pytest.raises(work_contract.Rejected):
        parsed(**bad)


def test_direct_generic_call_cannot_self_supply_broker_grant(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    audit = tmp_path / "audit"
    audit.mkdir()
    req = parsed()
    status, code, files = work_generic.execute(req, work, audit, lambda: None)
    assert (status, code, files) == ("failed", "generic_input_rejected", ())
    assert not (work / "inputs").exists()


def test_existing_inline_generic_request_cannot_claim_trusted_resource(tmp_path):
    req = contract(task_type=work_contract.GENERIC_TYPE)
    req.pop("resource_id")
    work = tmp_path / "work"
    work.mkdir()
    audit = tmp_path / "audit"
    audit.mkdir()
    grants = (InputGrant(work_contract.HOST_RESOURCE, "host-snapshot.json", b"{}"),)
    status, code, files = work_generic.execute(req, work, audit, lambda: None,
                                                trusted_grants=grants)
    assert (status, code, files) == ("failed", "generic_input_rejected", ())


def test_resource_profile_is_off_by_default_and_retains_ledger(tmp_path):
    store = Store(tmp_path)
    req = parsed()
    store.submit(req, BASE)
    runner = Runner(store, {"base_ref": BASE, "generic_enabled": True,
                            "seed": "not-used", "min_free_bytes": 0})
    assert runner.tick()
    item = store.load(req["task_id"])
    assert item["status"] == "blocked" and item["attempts"] == 1
    assert item["history"][-1]["code"] == "resource_not_enabled"


def test_unavailable_resource_blocks_safely_without_running_script(tmp_path):
    store = Store(tmp_path)
    req = parsed()
    store.submit(req, BASE)
    def unavailable(_):
        raise ResourceGrantUnavailable("resource_unavailable")
    runner = Runner(store, {"base_ref": BASE, "generic_enabled": True,
                            "resource_enabled": True, "seed": "not-used",
                            "min_free_bytes": 0}, resource_loader=unavailable)
    assert runner.tick()
    item = store.load(req["task_id"])
    assert item["status"] == "blocked" and item["attempts"] == 1
    assert item["history"][-1]["code"] == "resource_unavailable"
    assert not (store.audit / req["task_id"] / "generic-script.py").exists()


def test_source_bound_execution_and_exact_receipt(tmp_path, monkeypatch):
    # Direct Python is only a unit-test substitute for a separate real
    # Bubblewrap/operator acceptance gate.
    monkeypatch.setattr(work_generic, "command",
                        lambda workspace, script, generic: [
                            sys.executable, "-I", "-B", str(script)])
    store = Store(tmp_path / "builder")
    req = parsed(script=(
        "import json,base64\nfrom pathlib import Path\n"
        "source=json.loads(Path('/workspace/inputs/host-snapshot.json').read_text())\n"
        "v=str(source['docker']['running']).encode()\n"
        "print(json.dumps({'files':[{'path':'answer.txt',"
        "'content_b64':base64.b64encode(v).decode()}]}))\n"
    ))
    # The sandbox has /workspace, but the test-only direct Python process
    # needs a local path substituted without changing production code.
    req["script"] = req["script"].replace(
        "/workspace/inputs/host-snapshot.json",
        str(store.workspaces / req["task_id"] / "scratch" / "inputs" / "host-snapshot.json")
    )
    req = work_contract.parse(json.dumps(req).encode())
    store.submit(req, BASE)
    path = tmp_path / "host.json"
    write_snapshot(path)
    def trusted(resource_id):
        return load_host_resource(resource_id, path=path)
    runner = Runner(store, {"base_ref": BASE, "generic_enabled": True,
                            "resource_enabled": True, "seed": "unused",
                            "min_free_bytes": 0}, resource_loader=trusted)
    assert runner.tick()
    record = store.load(req["task_id"])
    assert record["status"] == "succeeded" and record["attempts"] == 1
    receipt = record["resource_receipt"]
    result = summary(record)["result"]
    assert result["resource_receipt"] == receipt
    assert len(receipt["sha256"]) == 64
    assert receipt["resource_id"] == work_contract.HOST_RESOURCE
    assert result["artifact_manifest"][0]["preview_text"] == "13"
    assert result["goal_semantics_verified"] is False
    replay, created = store.submit(req, BASE)
    assert not created and replay["attempts"] == 1
    with pytest.raises(work_contract.Rejected, match="conflict"):
        store.submit({**req, "script": "print('changed')"}, BASE)


def test_file_must_not_be_symlink_or_stale(tmp_path):
    from datetime import datetime, timedelta, timezone
    path = tmp_path / "host.json"
    write_snapshot(path)
    link = tmp_path / "link.json"
    link.symlink_to(path)
    with pytest.raises(ResourceGrantUnavailable):
        load_host_resource(work_contract.HOST_RESOURCE, path=link)
    stale = snapshot()
    stale["collected_at"] = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    path.write_text(json.dumps(stale))
    with pytest.raises(ResourceGrantUnavailable):
        load_host_resource(work_contract.HOST_RESOURCE, path=path)
