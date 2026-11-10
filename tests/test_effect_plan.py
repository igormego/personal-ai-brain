"""Effect planning is pure; no host writes, credentials or network in tests."""
from datetime import datetime, timezone

import pytest

from personal_ai_brain.effect_plan import EffectGrant, EffectPlanDenied, plan_effect


NOW = datetime(2026, 11, 10, 19, 0, tzinfo=timezone.utc)
GRANT = EffectGrant("arr/radarr", "set_monitored", "owner", "monitored", "bool", "v1")
PROPOSAL = {"resource_id": "arr/radarr", "target_id": 17, "operation": "set_monitored",
            "desired": True}


def read_current(rid, tid):
    assert (rid, tid) == ("arr/radarr", 17)
    return {"id": 17, "monitored": False}


def make(value=None, grant=GRANT, principal="owner", reader=read_current):
    return plan_effect(PROPOSAL if value is None else value, principal=principal,
                       grant=grant, read_current=reader, now=NOW)


def test_exact_target_plan_binds_trusted_observed_value_and_expiry():
    p = make()
    assert p["schema_version"] == "0.1"
    assert p["expected"] is False and p["desired"] is True
    assert p["resource_id"] == "arr/radarr" and p["target_id"] == 17
    assert p["principal"] == "owner" and p["policy_version"] == "v1"
    assert p["expires_at"] == "2026-11-10T19:05:00Z"
    assert len(p["plan_hash"]) == 64 and p["plan_hash"] == make()["plan_hash"]
    assert "credential" not in str(p)


@pytest.mark.parametrize("changed", [
    {"resource_id": "arr/sonarr"},
    {"operation": "exec"},
    {"target_id": True},
    {"target_id": -1},
    {"target_id": "/etc/passwd"},
    {"desired": "true"},
    {"desired": 1},
    {"desired": None},
    {"cmd": "docker restart"},
])
def test_model_cannot_broaden_target_operation_or_schema(changed):
    with pytest.raises(EffectPlanDenied, match="^effect_plan_denied$"):
        make({**PROPOSAL, **changed})


def test_wrong_principal_or_grant_is_denied():
    with pytest.raises(EffectPlanDenied):
        make(principal="forged")
    with pytest.raises(EffectPlanDenied):
        make(grant=EffectGrant("arr/radarr", "other", "owner", "monitored", "bool", "v1"))


@pytest.mark.parametrize("source", [
    {"id": 18, "monitored": False},
    {"id": 17, "monitored": "no"},
    {"id": 17, "monitored": False, "secret": "leak"},
    {"id": 17, "monitored": True},
    {},
])
def test_untrusted_or_unnecessary_source_fails_closed(source):
    with pytest.raises(EffectPlanDenied):
        make(reader=lambda _rid, _tid: source)


def test_read_failure_is_sanitized_and_no_write_callback_exists():
    def unavailable(_rid, _tid):
        raise RuntimeError("secret private API key")
    with pytest.raises(EffectPlanDenied) as exc:
        make(reader=unavailable)
    assert str(exc.value) == "effect_plan_denied"


def test_generic_positive_integer_operation_uses_same_planner():
    grant = EffectGrant("arr/sonarr", "set_profile", "owner", "quality_profile_id",
                        "positive_int", "v2")
    proposal = {"resource_id": "arr/sonarr", "target_id": 3,
                "operation": "set_profile", "desired": 7}
    plan = plan_effect(
        proposal, principal="owner", grant=grant,
        read_current=lambda _rid, tid: {"id": tid, "quality_profile_id": 4}, now=NOW)
    assert (plan["expected"], plan["desired"]) == (4, 7)
    assert plan["plan_hash"] != make()["plan_hash"]
