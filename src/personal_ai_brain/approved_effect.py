"""Source-only gated effect adapter over the existing PAB Control request ledger.

No command, URL, path, Docker socket or host operation is available here.
A trusted operator supplies fixed grants, an approval verifier secret and
registered application adapters. The normal deployed worker supplies none.

Before handler invocation, Control's existing durable UUID reservation is
fsynced. An interrupted or uncertain external operation is NEVER retried.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import re
from typing import Callable

from .effect_plan import EffectGrant


class EffectDenied(ValueError):
    """Fixed safe error; external details must not leave the trusted host."""


class EffectUncertain(RuntimeError):
    """A write may have occurred. Human/operator read-back required."""


def _deny():
    raise EffectDenied("effect_denied")


PLAN_FIELDS = frozenset({
    "schema_version", "resource_id", "target_id", "operation", "field",
    "expected", "desired", "principal", "policy_version", "expires_at",
    "plan_hash",
})


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _value(value, value_type):
    if value_type == "bool" and type(value) is bool:
        return value
    if value_type == "positive_int" and type(value) is int and 1 <= value <= 2147483647:
        return value
    _deny()


def validate_arguments(value):
    """Pure transport shape check; authorization happens in the trusted broker."""
    if (type(value) is not dict or set(value) != {"plan", "approval"}
            or type(value["plan"]) is not dict or set(value["plan"]) != PLAN_FIELDS):
        _deny()
    plan, approval = value["plan"], value["approval"]
    if (type(approval) is not dict
            or set(approval) != {"request_id", "plan_hash", "signature"}
            or any(type(approval[k]) is not str for k in approval)):
        _deny()
    if (plan["schema_version"] != "0.1"
            or any(type(plan[k]) is not str or not re.fullmatch(
                r"[a-z0-9][a-z0-9./_-]{0,63}", plan[k])
                   for k in ("resource_id", "operation", "field", "principal", "policy_version"))
            or type(plan["target_id"]) is not int or not 1 <= plan["target_id"] <= 2147483647
            or type(plan["expires_at"]) is not str
            or not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", plan["expires_at"])
            or type(plan["plan_hash"]) is not str
            or not re.fullmatch("[0-9a-f]{64}", plan["plan_hash"])
            or not re.fullmatch("[0-9a-f]{64}", approval["signature"])
            or not re.fullmatch("[0-9a-f]{64}", approval["plan_hash"])
            or not re.fullmatch("[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}", approval["request_id"])
            or type(plan["expected"]) is not type(plan["desired"])
            or type(plan["desired"]) not in (bool, int)
            or plan["expected"] == plan["desired"]
            or approval["plan_hash"] != plan["plan_hash"]):
        _deny()
    fields = {key: plan[key] for key in plan if key != "plan_hash"}
    if not hmac.compare_digest(hashlib.sha256(_canonical(fields)).hexdigest(), plan["plan_hash"]):
        _deny()
    # Input deliberately contains no variable URLs, paths, shell, JSON filters or identifiers.
    return {"plan": dict(plan), "approval": dict(approval)}


def _deadline(plan, now):
    try:
        when = datetime.strptime(plan["expires_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError, OverflowError):
        _deny()
    if not timedelta(0) < when - now <= timedelta(minutes=5):
        _deny()


def _signed_receipt(request_id, plan_hash, principal):
    """Approval authority signs these fields; transport cannot change any of them."""
    return _canonical({
        "schema_version": "0.1", "request_id": request_id,
        "plan_hash": plan_hash, "principal": principal,
    })


class ApprovedEffectBroker:
    """Fixed trusted adapter admission; NO privileged methods registered by default.

    `grants` is operator-supplied and exact. `adapter` must implement
    read_current(resource_id, target_id) and apply_cas(plan) using trusted
    credentials, not model-provided URLs. Approval signing authority is a
    separate operator process; only a verifier key belongs here.

    apply_cas MUST enforce the expected prior value atomically or refuse the
    effect. A source that cannot provide this guarantee is not eligible.
    """
    def __init__(self, *, grants: dict[str, EffectGrant], adapter,
                 verification_key: bytes, clock: Callable[[], datetime] | None = None):
        if (type(grants) is not dict or not grants
                or any(type(key) is not str or not isinstance(grant, EffectGrant)
                       or key != grant.resource_id for key, grant in grants.items())
                or not isinstance(verification_key, bytes) or len(verification_key) < 32
                or not callable(getattr(adapter, "read_current", None))
                or not callable(getattr(adapter, "apply_cas", None))
                or (clock is not None and not callable(clock))):
            _deny()
        self._grants = dict(grants)
        self._adapter = adapter
        self._key = verification_key
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def execute(self, arguments: dict, *, request_id: str) -> dict:
        # The caller must separately bind the actual authenticated GitHub
        # issue author to owner before invoking this method.
        args = validate_arguments(arguments)
        plan, receipt = args["plan"], args["approval"]
        if type(request_id) is not str or receipt["request_id"] != request_id:
            _deny()
        grant = self._grants.get(plan["resource_id"])
        if grant is None or any((
            plan["operation"] != grant.operation,
            plan["field"] != grant.field,
            plan["principal"] != grant.principal,
            plan["policy_version"] != grant.policy_version,
            grant.principal != "owner",
        )):
            _deny()
        expected = _value(plan["expected"], grant.value_type)
        desired = _value(plan["desired"], grant.value_type)
        if expected == desired:
            _deny()
        now = self._clock()
        if not isinstance(now, datetime) or now.tzinfo is None:
            _deny()
        _deadline(plan, now.astimezone(timezone.utc))
        mac = hmac.new(self._key, _signed_receipt(request_id, plan["plan_hash"], grant.principal),
                       hashlib.sha256).hexdigest()
        if not hmac.compare_digest(mac, receipt["signature"]):
            _deny()

        try:
            before = self._adapter.read_current(plan["resource_id"], plan["target_id"])
        except Exception:
            _deny()  # Nothing was attempted.
        if (type(before) is not dict or set(before) != {"id", grant.field}
                or type(before["id"]) is not int or before["id"] != plan["target_id"]
                or type(before[grant.field]) is not type(expected)
                or before[grant.field] != expected):
            _deny()  # Stale plan, not an invitation to retry or reinterpret.

        try:
            # The adapter must provide its own last-moment compare-and-set
            # enforcement. The assistant supplies NO command or credential.
            self._adapter.apply_cas(dict(plan))
        except Exception:
            raise EffectUncertain("effect_outcome_uncertain") from None
        try:
            after = self._adapter.read_current(plan["resource_id"], plan["target_id"])
            if (type(after) is not dict or set(after) != {"id", grant.field}
                    or type(after["id"]) is not int or after["id"] != plan["target_id"]
                    or type(after[grant.field]) is not type(desired)
                    or after[grant.field] != desired):
                raise ValueError("readback_mismatch")
        except Exception:
            raise EffectUncertain("effect_outcome_uncertain") from None
        return {
            "schema_version": "0.1", "resource_id": plan["resource_id"],
            "operation": plan["operation"], "target_id": plan["target_id"],
            "policy_version": grant.policy_version, "plan_hash": plan["plan_hash"],
            "expected": expected, "observed": desired, "effect_performed": True,
            "verified_by": "trusted_adapter_readback",
        }
