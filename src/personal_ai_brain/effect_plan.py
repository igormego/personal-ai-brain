"""Reviewable, source-only effect plans over independently authorized resources.

This module proposes a typed exact-target change. It neither obtains a user
approval nor performs a write, and it must not be registered as a live tool
without a separately reviewed authorization and durable-execution boundary.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from typing import Callable


class EffectPlanDenied(ValueError):
    """Fixed code; never echo model, endpoint or sensitive source content."""


def _deny():
    raise EffectPlanDenied("effect_plan_denied")


@dataclass(frozen=True)
class EffectGrant:
    """Trusted operator-provided policy, never constructed from a model request."""

    resource_id: str
    operation: str
    principal: str
    field: str
    value_type: str
    policy_version: str

    def __post_init__(self):
        if (not all(type(x) is str and re.fullmatch(r"[a-z0-9][a-z0-9./_-]{0,63}", x)
                    for x in (self.resource_id, self.operation, self.principal,
                              self.field, self.policy_version))
                or self.value_type not in ("bool", "positive_int")):
            _deny()


def _value(value, value_type):
    if value_type == "bool":
        if type(value) is not bool:
            _deny()
    elif value_type == "positive_int":
        if type(value) is not int or not 1 <= value <= 2147483647:
            _deny()
    else:
        _deny()
    return value


def plan_effect(
    proposal: dict, *, principal: str, grant: EffectGrant,
    read_current: Callable[[str, int], dict], now: datetime,
) -> dict:
    """Bind an observed exact target/diff to a five-minute reviewable digest.

    `read_current` is a trusted GET-only broker/provider, never a model URL or
    callable. The caller must independently authenticate `principal` and bind
    the returned plan hash to a later owner approval + durable one-shot ledger.
    """
    if (type(proposal) is not dict
            or set(proposal) != {"resource_id", "target_id", "operation", "desired"}
            or type(principal) is not str or not isinstance(grant, EffectGrant)
            or not isinstance(now, datetime) or now.tzinfo is None
            or not callable(read_current)):
        _deny()
    rid, op, tid = proposal["resource_id"], proposal["operation"], proposal["target_id"]
    if (rid != grant.resource_id or op != grant.operation
            or principal != grant.principal
            or type(tid) is not int or not 1 <= tid <= 2147483647):
        _deny()
    desired = _value(proposal["desired"], grant.value_type)
    try:
        actual = read_current(rid, tid)
    except Exception:
        _deny()
    if (type(actual) is not dict or set(actual) != {"id", grant.field}
            or type(actual["id"]) is not int or actual["id"] != tid):
        _deny()
    current = _value(actual[grant.field], grant.value_type)
    if current == desired:
        _deny()  # Nothing to approve or apply.
    fields = {
        "schema_version": "0.1",
        "resource_id": rid, "target_id": tid,
        "operation": op, "field": grant.field,
        "expected": current, "desired": desired,
        "principal": principal, "policy_version": grant.policy_version,
        "expires_at": (now.astimezone(timezone.utc) + timedelta(minutes=5))
            .isoformat(timespec="seconds").replace("+00:00", "Z"),
    }
    canonical = json.dumps(fields, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return {**fields, "plan_hash": hashlib.sha256(canonical.encode()).hexdigest()}
