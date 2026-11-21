"""Offline Unix socket acceptance; the only adapter is a synthetic boolean."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import socket
import threading
from uuid import uuid4

import pytest

from personal_ai_brain.approved_effect import (
    ApprovedEffectBroker, EffectDenied, EffectUncertain, _signed_receipt,
)
from personal_ai_brain.effect_plan import EffectGrant, plan_effect
from personal_ai_brain.host_effect_socket import (
    HostEffectClient, HostEffectSocketServer, MAX_REQUEST,
)

NOW = datetime(2026, 11, 14, 22, 10, tzinfo=timezone.utc)
KEY = b"local-only synthetic verifier" * 2
GRANT = EffectGrant("fixture/service", "set_enabled", "owner", "enabled", "bool", "v1")


class FakeCAS:
    def __init__(self, value=False):
        self.value = value
        self.attempts = 0
        self.uncertain = False

    def read_current(self, resource_id, target_id):
        assert resource_id == "fixture/service"
        assert target_id == 17
        return {"id": 17, "enabled": self.value}

    def apply_cas(self, plan):
        self.attempts += 1
        if plan["expected"] is not self.value:
            raise ValueError("stale")
        self.value = plan["desired"]
        if self.uncertain:
            raise RuntimeError("private host adapter details must not leak")


def signed():
    rid = str(uuid4())
    plan = plan_effect(
        {"resource_id": "fixture/service", "operation": "set_enabled",
         "target_id": 17, "desired": True},
        principal="owner", grant=GRANT,
        read_current=lambda *_: {"id": 17, "enabled": False}, now=NOW,
    )
    sig = hmac.new(KEY, _signed_receipt(rid, plan["plan_hash"], "owner"),
                   hashlib.sha256).hexdigest()
    return rid, {"plan": plan, "approval": {
        "request_id": rid, "plan_hash": plan["plan_hash"], "signature": sig,
    }}


def host(adapter):
    return ApprovedEffectBroker(
        grants={"fixture/service": GRANT}, adapter=adapter,
        verification_key=KEY, clock=lambda: NOW,
    )


def serve_call(server, run):
    errors = []

    def worker():
        try:
            server.serve_one()
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    try:
        return run()
    finally:
        thread.join(timeout=2)
        assert not thread.is_alive()
        assert not errors


def test_real_socket_roundtrip_exact_plan_readback_and_no_secret_in_client(tmp_path):
    path = tmp_path / "effect.sock"
    adapter = FakeCAS()
    rid, args = signed()
    client = HostEffectClient(path)
    assert vars(client) == {"_path": path}
    with HostEffectSocketServer(path, allowed_uid=os.geteuid(), broker=host(adapter)) as server:
        assert path.exists()
        assert path.stat().st_mode & 0o777 == 0o600
        result = serve_call(server, lambda: client.execute(args, request_id=rid))
        assert result["verified_by"] == "trusted_adapter_readback"
        assert result["observed"] is True
        assert adapter.attempts == 1
    assert not path.exists()


def test_replaying_same_signed_request_cannot_repeat_a_change(tmp_path):
    path = tmp_path / "effect.sock"
    adapter = FakeCAS()
    rid, args = signed()
    client = HostEffectClient(path)
    with HostEffectSocketServer(path, allowed_uid=os.geteuid(), broker=host(adapter)) as server:
        serve_call(server, lambda: client.execute(args, request_id=rid))
        with pytest.raises(EffectDenied):
            serve_call(server, lambda: client.execute(args, request_id=rid))
    assert adapter.attempts == 1


def test_wrong_peer_uid_denied_before_approval_or_adapter(tmp_path):
    path = tmp_path / "effect.sock"
    adapter = FakeCAS()
    rid, args = signed()
    with HostEffectSocketServer(path, allowed_uid=os.geteuid() + 1, broker=host(adapter)) as server:
        with pytest.raises(EffectDenied):
            serve_call(server, lambda: HostEffectClient(path).execute(args, request_id=rid))
    assert adapter.attempts == 0


def test_uncertain_after_write_is_not_accepted_as_success(tmp_path):
    path = tmp_path / "effect.sock"
    adapter = FakeCAS()
    adapter.uncertain = True
    rid, args = signed()
    with HostEffectSocketServer(path, allowed_uid=os.geteuid(), broker=host(adapter)) as server:
        with pytest.raises(EffectUncertain):
            serve_call(server, lambda: HostEffectClient(path).execute(args, request_id=rid))
    assert adapter.attempts == 1
    assert adapter.value is True


@pytest.mark.parametrize("new_args", [
    {"command": "docker restart plex"},
    {"path": "/dev/sdb"},
    {"network": "ssh://home"},
    {"approval": {"signature": "0" * 64}},
])
def test_model_cannot_expand_socket_request(new_args, tmp_path):
    path = tmp_path / "not-installed.sock"
    rid, args = signed()
    args.update(new_args)
    with pytest.raises(EffectDenied):
        HostEffectClient(path).execute(args, request_id=rid)


@pytest.mark.parametrize("bad", [
    {"schema_version": "0.1", "request_id": "none",
     "operation": "shell.exec", "arguments": {}},
    {"schema_version": "0.1", "request_id": "none",
     "operation": "effect.execute", "arguments": {"path": "/dev/sda"}},
    {"schema_version": "0.1", "request_id": "none",
     "operation": "effect.execute", "arguments": {}},
    {"schema_version": "0.1", "request_id": "none",
     "operation": "effect.execute", "arguments": {}, "token": "injected"},
])
def test_unknown_socket_operations_never_reach_adapter(bad, tmp_path):
    path = tmp_path / "effect.sock"
    adapter = FakeCAS()
    with HostEffectSocketServer(path, allowed_uid=os.geteuid(), broker=host(adapter)) as server:
        def caller():
            with socket.socket(socket.AF_UNIX) as conn:
                conn.settimeout(2)
                conn.connect(str(path))
                conn.sendall((json.dumps(bad) + "\n").encode())
                raw = conn.recv(4096)
                return json.loads(raw)
        response = serve_call(server, caller)
        assert response == {"schema_version": "0.1", "status": "denied", "result": None}
    assert adapter.attempts == 0


def test_oversized_request_fails_closed(tmp_path):
    path = tmp_path / "effect.sock"
    adapter = FakeCAS()
    with HostEffectSocketServer(path, allowed_uid=os.geteuid(), broker=host(adapter)) as server:
        def caller():
            with socket.socket(socket.AF_UNIX) as conn:
                conn.settimeout(2)
                conn.connect(str(path))
                conn.sendall(b"x" * (MAX_REQUEST + 1) + b"\n")
                return json.loads(conn.recv(4096))
        assert serve_call(server, caller)["status"] == "denied"
    assert adapter.attempts == 0


def test_parent_permission_and_existing_path_fail_closed(tmp_path):
    path = tmp_path / "effect.sock"
    b = host(FakeCAS())
    path.write_text("must remain unchanged")
    with pytest.raises(EffectDenied):
        with HostEffectSocketServer(path, allowed_uid=os.geteuid(), broker=b):
            pass
    assert path.read_text() == "must remain unchanged"
    path.unlink()
    tmp_path.chmod(0o777)
    with pytest.raises(EffectDenied):
        with HostEffectSocketServer(path, allowed_uid=os.geteuid(), broker=b):
            pass
    assert not path.exists()


def test_split_uid_socket_requires_operator_owned_setgid_group_directory(tmp_path):
    path = tmp_path / "effect.sock"
    adapter = FakeCAS()
    # Never make a cross-UID socket visible in an unreviewed directory.
    with pytest.raises(EffectDenied):
        with HostEffectSocketServer(path, allowed_uid=os.geteuid(),
                                    broker=host(adapter), socket_mode=0o660):
            pass
    tmp_path.chmod(0o2750)
    rid, args = signed()
    with HostEffectSocketServer(path, allowed_uid=os.geteuid(),
                                broker=host(adapter), socket_mode=0o660) as server:
        assert path.stat().st_mode & 0o777 == 0o660
        assert path.stat().st_gid == tmp_path.stat().st_gid
        serve_call(server, lambda: HostEffectClient(path).execute(args, request_id=rid))
        assert adapter.attempts == 1
    assert not path.exists()


def test_existing_control_worker_can_reach_separate_host_broker_without_approval_key(tmp_path):
    from personal_ai_brain.control import Redactor, build_registry
    from personal_ai_brain.control_worker import Worker
    from personal_ai_brain.github_issues import TITLE

    class Transport:
        def __init__(self, body):
            self.issue = {"number": 1, "state": "open", "title": TITLE,
                          "body": body, "user": {"id": 55916200, "login": "igormego"}}
            self.comments = []

        def list_page(self, page):
            return [self.issue] if self.issue["state"] == "open" else []

        def get_issue(self, number):
            return self.issue

        def post_result(self, number, result):
            self.comments.append(result)

        def close_issue(self, number):
            self.issue["state"] = "closed"

    path = tmp_path / "effect.sock"
    rid, args = signed()
    envelope = {"schema_version": "0.1", "request_id": rid,
                "capability": "effect.execute", "arguments": args,
                "requested_by": "chatgpt", "requested_at": datetime.now(timezone.utc).isoformat()}
    transport = Transport(json.dumps(envelope))
    adapter = FakeCAS()
    registry = build_registry(
        tmp_path, None, Redactor(),
        approved_effect_provider=HostEffectClient(path),
    )
    assert "effect.execute" in registry.capabilities
    with HostEffectSocketServer(path, allowed_uid=os.geteuid(), broker=host(adapter)) as server:
        serve_call(server, lambda: Worker(
            tmp_path / "control-ledger", transport, registry, Redactor()).tick())
    assert transport.comments[0]["status"] == "capability_completed"
    assert transport.comments[0]["result"]["verified_by"] == "trusted_adapter_readback"
    assert adapter.value is True and adapter.attempts == 1
    assert "signature" not in str(transport.comments[0]["result"])
    # The normal runtime never injects HostEffectClient by itself.
    assert "effect.execute" not in build_registry(tmp_path, None, Redactor()).capabilities
