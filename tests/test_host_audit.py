"""Offline acceptance for opt-in host-audit snapshot, no SSH or real Docker calls."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path

import pytest

from personal_ai_brain.control import Redactor, Rejected, build_registry
from personal_ai_brain.control_worker import Worker
from personal_ai_brain.host_audit import (
    AuditUnavailable, _INSPECT_FORMAT, _PS_FORMAT, collect_snapshot,
    publish_snapshot, read_snapshot, validate_snapshot,
)
from personal_ai_brain.github_issues import TITLE


NOW = datetime.now(timezone.utc).replace(microsecond=0)


def packet(*, at=NOW):
    return {
        "schema_version": "0.1",
        "collected_at": at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "cpu_logical": 16,
        "memory_mib": {"total": 32768, "available": 16000},
        "disk_free_gib": {"root": 125, "data": 6700},
        "docker": {"status": "available", "running": 14, "unhealthy": 0},
        "control": {
            "status": "available", "image_id": "sha256:" + "a" * 64,
            "read_only_rootfs": True, "privileged": False,
            "no_new_privileges": True, "docker_socket": False,
            "published_ports": 0,
        },
    }


def test_private_atomic_snapshot_roundtrip(tmp_path):
    path = tmp_path / "snapshot.json"
    publish_snapshot(path, packet(at=NOW))
    observed = read_snapshot(path, now=NOW + timedelta(minutes=10))
    assert observed["freshness"] == "current"
    assert observed["source"] == "host_collector_snapshot_not_live_probe"
    assert observed["control"]["read_only_rootfs"] is True
    assert observed["memory_mib"]["available"] == 16000
    assert path.stat().st_mode & 0o777 == 0o600
    assert not any(tmp_path.glob(".host-audit-*"))
    assert "Docker" not in path.read_text()


@pytest.mark.parametrize("change", [
    {"schema_version": "0.2"},
    {"cpu_logical": 0},
    {"cpu_logical": True},
    {"memory_mib": {"total": 100, "available": 101}},
    {"disk_free_gib": {"root": -1, "data": None}},
    {"docker": {"status": "available", "running": 1, "unhealthy": 2}},
    {"docker": {"status": "unavailable", "running": 3, "unhealthy": 0}},
    {"control": {"status": "available", "image_id": "private"}},
    {"private_api_token": "should-not-appear"},
])
def test_strict_schema_fail_closed(change):
    value = {**packet(), **change}
    with pytest.raises(AuditUnavailable):
        validate_snapshot(value, now=NOW)


def test_docker_and_control_unavailable_are_explicit():
    data = packet()
    data["docker"] = {"status": "unavailable", "running": None, "unhealthy": None}
    data["control"] = {key: (None if key != "status" else "unavailable")
                       for key in data["control"]}
    assert validate_snapshot(data, now=NOW)["docker"]["status"] == "unavailable"
    data["control"]["published_ports"] = 1
    with pytest.raises(AuditUnavailable):
        validate_snapshot(data, now=NOW)


@pytest.mark.parametrize("minutes", [-3, 31, 5000])
def test_stale_and_future_sources_denied(minutes):
    with pytest.raises(AuditUnavailable):
        validate_snapshot(packet(at=NOW - timedelta(minutes=minutes)), now=NOW)


def test_symlink_bad_permissions_duplicate_keys_and_oversized_denied(tmp_path):
    path = tmp_path / "snapshot.json"
    publish_snapshot(path, packet())
    link = tmp_path / "link.json"
    link.symlink_to(path)
    with pytest.raises(AuditUnavailable):
        read_snapshot(link, now=NOW)
    path.chmod(0o666)
    with pytest.raises(AuditUnavailable):
        read_snapshot(path, now=NOW)
    path.chmod(0o600)
    path.write_text('{"schema_version":"0.1","schema_version":"0.1"}')
    with pytest.raises(AuditUnavailable):
        read_snapshot(path, now=NOW)
    path.write_text("X" * 4097)
    with pytest.raises(AuditUnavailable):
        read_snapshot(path, now=NOW)


def test_untrusted_parent_directory_rejected(tmp_path):
    parent = tmp_path / "unsafe"
    parent.mkdir(mode=0o777)
    parent.chmod(0o777)
    with pytest.raises(AuditUnavailable):
        publish_snapshot(parent / "snapshot.json", packet())


def test_host_collector_uses_only_fixed_docker_queries():
    calls = []
    inspect = "true|false|sha256:" + "b" * 64 + "||0|yes"

    def probe(arguments):
        calls.append(arguments)
        if arguments == ["ps", "--format", _PS_FORMAT]:
            return "Up 3 hours (healthy)\nUp 3 hours (unhealthy)"
        if arguments == ["inspect", "--format", _INSPECT_FORMAT,
                         "personal-ai-brain-control-control-1"]:
            return inspect
        raise AssertionError("unauthorized Docker operation")

    actual = collect_snapshot(now=NOW, probe=probe)
    assert actual["control"]["image_id"] == "sha256:" + "b" * 64
    assert actual["control"]["privileged"] is False
    assert actual["control"]["docker_socket"] is False
    assert actual["docker"] == {"status": "available", "running": 2, "unhealthy": 1}
    assert len(calls) == 2
    assert "/etc" not in json.dumps(actual)
    assert "secret" not in json.dumps(actual).lower()


def test_collector_reports_docker_unavailable_without_guessing():
    actual = collect_snapshot(now=NOW, probe=lambda args: None)
    assert actual["docker"] == {"status": "unavailable", "running": None, "unhealthy": None}
    assert actual["control"]["status"] == "unavailable"


def test_audit_disabled_by_default_even_if_path_exists(tmp_path):
    snapshot = tmp_path / "snapshot.json"
    publish_snapshot(snapshot, packet())
    registry = build_registry(tmp_path, None, Redactor())
    with pytest.raises(Rejected, match="unknown_capability"):
        registry.resolve("homelab.audit", {})
    registry = build_registry(tmp_path, None, Redactor(),
                              host_audit_snapshot=snapshot)
    cap, args = registry.resolve("homelab.audit", {})
    assert cap.risk_level == "read_only" and cap.side_effect_class == "none"
    assert cap.handler(args)["freshness"] == "current"
    for supplied in ({"path": "/etc/passwd"}, {"command": "docker ps"},
                     {"project_id": "homelab"}, {"lines": 3}):
        with pytest.raises(Rejected, match="invalid_arguments"):
            registry.resolve("homelab.audit", supplied)


class FakeTransport:
    def __init__(self, issue_bodies):
        self.issues = {
            index: dict(number=index, title=TITLE, body=body, state="open")
            for index, body in enumerate(issue_bodies, 1)
        }
        self.comments = []

    def list_page(self, page):
        return [issue for issue in self.issues.values() if issue["state"] == "open"]

    def get_issue(self, number):
        return self.issues[number]

    def post_result(self, number, result):
        self.comments.append((number, result))

    def close_issue(self, number):
        self.issues[number]["state"] = "closed"


def _request(request_id, **changes):
    data = {
        "schema_version": "0.1", "request_id": request_id,
        "capability": "homelab.audit", "arguments": {},
        "requested_by": "chatgpt", "requested_at": NOW.isoformat(),
    }
    return json.dumps({**data, **changes})


def test_full_transport_result_then_duplicate_uses_original_snapshot(tmp_path):
    from uuid import uuid4
    snapshot = tmp_path / "host.json"
    publish_snapshot(snapshot, packet())
    state = tmp_path / "control-ledger"
    req = _request(str(uuid4()))
    transport = FakeTransport([req, req])
    registry = build_registry(tmp_path, None, Redactor(),
                              host_audit_snapshot=snapshot)
    worker = Worker(state, transport, registry, Redactor())
    worker.tick()
    assert transport.comments[0][1]["status"] == "capability_completed"
    assert transport.comments[0][1]["result"]["freshness"] == "current"
    assert transport.comments[1][1]["status"] == "duplicate"
    # Changing the source after a request never re-executes the request.
    snapshot.unlink()
    transport.issues[3] = dict(number=3, title=TITLE, body=req, state="open")
    Worker(state, transport, registry, Redactor()).tick()
    assert transport.comments[-1][1]["status"] == "duplicate"


def test_unavailable_snapshot_fails_closed_with_no_raw_exception(tmp_path):
    from uuid import uuid4
    registry = build_registry(tmp_path, None, Redactor(),
                              host_audit_snapshot=tmp_path / "missing.json")
    transport = FakeTransport([_request(str(uuid4()))])
    Worker(tmp_path / "ledger", transport, registry, Redactor()).tick()
    result = transport.comments[0][1]
    assert result["status"] == "capability_execution_failed"
    assert result["error"] == "source_read_failure"
    assert "missing.json" not in json.dumps(result)
