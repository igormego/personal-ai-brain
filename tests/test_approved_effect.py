"""Synthetic source acceptance: zero host/Docker/filesystem effects."""
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
from pathlib import Path
from uuid import uuid4

import pytest

from personal_ai_brain.approved_effect import (
    ApprovedEffectBroker, EffectDenied, EffectUncertain,
    _signed_receipt, validate_arguments,
)
from personal_ai_brain.control import Redactor, Rejected, build_registry
from personal_ai_brain.control_worker import Worker
from personal_ai_brain.effect_plan import EffectGrant, plan_effect
from personal_ai_brain.github_issues import TITLE


NOW = datetime(2026, 11, 14, 22, 10, 0, tzinfo=timezone.utc)
KEY = b"offline synthetic host verifier material" * 2
GRANT = EffectGrant("fixture/service", "set_enabled", "owner", "enabled", "bool", "v1")
OWNER = {"id": 55916200, "login": "igormego"}


class SyntheticAdapter:
    """Trust-side compare-and-set fixture; no external calls."""
    def __init__(self):
        self.value = False
        self.attempts = 0
        self.fail_after = False
        self.bad_readback = False

    def read_current(self, resource_id, target_id):
        assert resource_id == "fixture/service" and target_id == 17
        return {"id": target_id, "enabled": self.value}

    def apply_cas(self, plan):
        self.attempts += 1
        if self.value is not plan["expected"]:
            raise RuntimeError("stale")
        self.value = plan["desired"]
        if self.fail_after:
            raise RuntimeError("private unexpected failure")
        if self.bad_readback:
            self.value = False


def broker(adapter=None):
    a = adapter or SyntheticAdapter()
    return ApprovedEffectBroker(
        grants={GRANT.resource_id: GRANT}, adapter=a,
        verification_key=KEY, clock=lambda: NOW,
    ), a


def payload(*, request_id=None, override_plan=None):
    request_id = request_id or str(uuid4())
    plan = plan_effect(
        {"resource_id": "fixture/service", "operation": "set_enabled",
         "target_id": 17, "desired": True},
        principal="owner", grant=GRANT, now=NOW,
        read_current=lambda rid, tid: {"id": tid, "enabled": False},
    )
    if override_plan:
        plan.update(override_plan)
    signature = hmac.new(
        KEY, _signed_receipt(request_id, plan["plan_hash"], "owner"),
        hashlib.sha256,
    ).hexdigest()
    args = {"plan": plan, "approval": {
        "request_id": request_id, "plan_hash": plan["plan_hash"],
        "signature": signature,
    }}
    return request_id, args


def test_exact_signed_plan_executes_once_and_verifies_readback():
    b, adapter = broker()
    rid, args = payload()
    outcome = b.execute(args, request_id=rid)
    assert outcome["effect_performed"] is True
    assert outcome["expected"] is False and outcome["observed"] is True
    assert outcome["plan_hash"] == args["plan"]["plan_hash"]
    assert adapter.attempts == 1


@pytest.mark.parametrize("field,value", [
    ("operation", "docker.run"),
    ("resource_id", "fixture/other"),
    ("field", "private_path"),
    ("principal", "other"),
    ("policy_version", "v2"),
    ("target_id", 18),
    ("expected", True),
    ("desired", 100),
    ("expires_at", "2026-11-14T22:20:00Z"),
])
def test_plan_tampering_does_not_invoke_adapter(field, value):
    b, adapter = broker()
    rid, args = payload()
    args["plan"][field] = value
    with pytest.raises(EffectDenied):
        b.execute(args, request_id=rid)
    assert adapter.attempts == 0


@pytest.mark.parametrize("change", [
    {"shell": "sudo rm -rf /"},
    {"path": "/var/lib/docker"},
    {"url": "http://host"},
    {"credential": "secret"},
])
def test_cannot_inject_new_effect_request_fields(change):
    rid, args = payload()
    args.update(change)
    with pytest.raises(EffectDenied):
        validate_arguments(args)


def test_wrong_request_id_or_forged_approval_rejected():
    b, adapter = broker()
    rid, args = payload()
    with pytest.raises(EffectDenied):
        b.execute(args, request_id=str(uuid4()))
    args["approval"]["signature"] = "0" * 64
    with pytest.raises(EffectDenied):
        b.execute(args, request_id=rid)
    assert adapter.attempts == 0


def test_expired_or_future_plan_rejected():
    adapter = SyntheticAdapter()
    rid, args = payload()
    for offset in (-1, 100):
        b = ApprovedEffectBroker(
            grants={GRANT.resource_id: GRANT}, adapter=adapter,
            verification_key=KEY,
            clock=lambda offset=offset: NOW + timedelta(minutes=offset),
        )
        with pytest.raises(EffectDenied):
            b.execute(args, request_id=rid)
    assert adapter.attempts == 0


def test_stale_source_and_cas_failure_cannot_be_retried_as_success():
    b, adapter = broker()
    adapter.value = True
    rid, args = payload()
    with pytest.raises(EffectDenied):
        b.execute(args, request_id=rid)
    assert adapter.attempts == 0


def test_ambiguous_after_apply_is_explicitly_uncertain():
    b, adapter = broker()
    adapter.fail_after = True
    rid, args = payload()
    with pytest.raises(EffectUncertain, match="effect_outcome_uncertain"):
        b.execute(args, request_id=rid)
    assert adapter.attempts == 1
    assert adapter.value is True


def test_wrong_readback_is_uncertain_not_retried():
    b, adapter = broker()
    adapter.bad_readback = True
    rid, args = payload()
    with pytest.raises(EffectUncertain):
        b.execute(args, request_id=rid)
    assert adapter.attempts == 1


class FakeTransport:
    def __init__(self, requests):
        self.issues = {
            n: {"number": n, "state": "open", "title": TITLE, "body": body,
                "user": author}
            for n, (body, author) in enumerate(requests, start=1)
        }
        self.comments = []

    def list_page(self, page):
        return list(self.issues.values())

    def get_issue(self, number):
        return self.issues[number]

    def post_result(self, number, result):
        self.comments.append((number, result))

    def close_issue(self, number):
        self.issues[number]["state"] = "closed"


def as_body(rid, args, *, requested_by="chatgpt"):
    return json.dumps({
        "schema_version": "0.1", "request_id": rid,
        "capability": "effect.execute", "arguments": args,
        "requested_by": requested_by, "requested_at": NOW.isoformat(),
    })


def test_source_default_denies_effect_registration(tmp_path):
    reg = build_registry(tmp_path, None, Redactor())
    with pytest.raises(Rejected, match="unknown_capability"):
        reg.resolve("effect.execute", payload()[1])


def test_owner_bound_transport_exact_once_conflict_and_replay(tmp_path):
    b, adapter = broker()
    registry = build_registry(tmp_path, None, Redactor(), approved_effect_provider=b)
    rid, args = payload()
    cap, valid = registry.resolve("effect.execute", args)
    assert cap.risk_level == "approved_effect"
    assert cap.side_effect_class == "trusted_host_effect"
    assert valid == args

    bad = json.loads(as_body(rid, args))
    bad["arguments"]["plan"]["desired"] = False
    transport = FakeTransport([
        (as_body(rid, args), OWNER),
        (as_body(rid, args), OWNER),
        (json.dumps(bad), OWNER),
    ])
    worker = Worker(tmp_path / "ledger", transport, registry, Redactor())
    worker.tick()
    assert [result[1]["status"] for result in transport.comments] == [
        "capability_completed", "duplicate", "request_rejected",
    ]
    assert adapter.attempts == 1
    result = transport.comments[0][1]["result"]
    assert result["verified_by"] == "trusted_adapter_readback"
    assert "signature" not in str(result)
    # Re-opened issue still gets durable result, not a second effect.
    transport.issues[4] = dict(transport.issues[1], number=4, state="open")
    Worker(tmp_path / "ledger", transport, registry, Redactor()).tick()
    assert adapter.attempts == 1


def test_auth_metadata_cannot_impersonate_owner(tmp_path):
    b, adapter = broker()
    registry = build_registry(tmp_path, None, Redactor(), approved_effect_provider=b)
    rid, args = payload()
    transport = FakeTransport([
        (as_body(rid, args, requested_by="igormego"), {"id": 1, "login": "igormego"}),
        (as_body(rid, args, requested_by="igormego"), {"id": 55916200, "login": "other"}),
    ])
    Worker(tmp_path / "ledger", transport, registry, Redactor()).tick()
    assert all(item[1]["status"] == "request_rejected" for item in transport.comments)
    assert adapter.attempts == 0


def test_failed_external_effect_is_never_replayed(tmp_path):
    b, adapter = broker()
    adapter.fail_after = True
    registry = build_registry(tmp_path, None, Redactor(), approved_effect_provider=b)
    rid, args = payload()
    body = as_body(rid, args)
    transport = FakeTransport([(body, OWNER), (body, OWNER)])
    Worker(tmp_path / "ledger", transport, registry, Redactor()).tick()
    assert transport.comments[0][1]["status"] == "capability_execution_failed"
    assert transport.comments[0][1]["error"] == "effect_outcome_uncertain_needs_review"
    assert transport.comments[1][1]["status"] == "duplicate"
    assert adapter.attempts == 1
    assert adapter.value is True
