from datetime import date, datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from personal_ai_brain.assistant_contract import normalize_request
from personal_ai_brain.assistant_project_os import (
    StatusRejected, answer_project_status, select_project_status, validate_selector,
)
from personal_ai_brain.control import Redactor, Rejected, build_registry
from personal_ai_brain.core_agent import CoreSnapshot, Finding, StateRecord


TODAY = date(2026, 10, 26)


def record(
    project_id="homelab", scope="project", path=".project/state.yaml",
    project_name="Homelab & Media Stack", last_verified="2026-10-22",
    sync_status="synced", status="active",
):
    return StateRecord(
        project_id=project_id, project_name=project_name, scope=scope,
        repository="igormego/homelab-infrastructure", path=path,
        attention="active_secondary", status=status, phase="post-migration",
        last_verified=last_verified, sync_status=sync_status,
        current_gate="Finish the approved poster check.",
        exact_next_action="Confirm the Samsung display; do not execute writes.",
    )


def snapshot(*records, findings=()):
    return CoreSnapshot(generated_on=TODAY.isoformat(), records=list(records),
                        findings=list(findings))


def request():
    return normalize_request(
        {"schema_version": "0.1", "request_id": str(uuid4()), "message": "Where are we with Homelab?",
         "created_at": "2026-10-26T12:00:00Z"},
        trusted_user_id="igor", trusted_surface="chatgpt", trusted_reply_target="conversation.123",
        now=datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc))


def test_selects_project_and_reports_verified_gate_and_next_step():
    status = select_project_status(snapshot(record()), {"project_id": "homelab"}, today=TODAY)
    assert status["phase"] == "post-migration"
    assert status["exact_next_action"].startswith("Confirm the Samsung")
    assert status["verification"]["last_verified"] == "2026-10-22"
    assert status["verification"]["freshness"] == "current"
    assert status["verification"]["age_days"] == 4


def test_selects_one_workstream_only_by_logical_slug():
    root = record()
    workstream = record(scope="workstream",
                        path=".project/workstreams/media-automation.yaml",
                        project_name="Homelab & Media Stack / media-automation",
                        last_verified="2026-10-18")
    data = snapshot(root, workstream)
    result = select_project_status(data, {"project_id": "homelab",
                                         "workstream_id": "media-automation"}, today=TODAY)
    assert result["scope"] == "workstream"
    assert result["verification"]["freshness"] == "stale"
    assert select_project_status(data, {"project_id": "homelab"}, today=TODAY)["scope"] == "project"


@pytest.mark.parametrize("selector", [
    None, [], {}, {"workstream_id": "media"}, {"project_id": "../homelab"},
    {"project_id": "homelab", "repo": "other"},
    {"project_id": "homelab", "ref": "evil"},
    {"project_id": "homelab", "path": ".env"},
    {"project_id": "homelab", "workstream_id": "../../"},
    {"project_id": ""}, {"project_id": "a" * 65},
    {"project_id": "homelab", "workstream_id": 123},
])
def test_rejects_any_repo_path_command_or_malformed_selector(selector):
    with pytest.raises(StatusRejected, match="invalid_project_selector"):
        validate_selector(selector)


def test_unrelated_findings_are_not_misattributed_to_selected_project():
    s = snapshot(record(), findings=[Finding("error", "state_unreadable", "Recipe Intelligence",
                                              "Private details must not leak.")])
    output = select_project_status(s, {"project_id": "homelab"}, today=TODAY)
    assert output["verification"]["freshness"] == "current"
    assert output["verification"]["finding_codes"] == []


def test_target_integrity_error_forces_unknown_not_successful_freshness():
    s = snapshot(record(), findings=[Finding("error", "state_required_field_missing",
                                              "Homelab & Media Stack", "Missing dependencies")])
    output = select_project_status(s, {"project_id": "homelab"}, today=TODAY)
    assert output["verification"]["freshness"] == "unknown"
    assert output["verification"]["finding_codes"] == ["state_required_field_missing"]


@pytest.mark.parametrize("kwargs,expected", [
    ({"last_verified": ""}, "unknown"),
    ({"last_verified": "2026-10-27"}, "unknown"),
    ({"last_verified": "bogus"}, "unknown"),
    ({"last_verified": "2026-10-01"}, "stale"),
    ({"sync_status": "pending"}, "unknown"),
    ({"sync_status": "synced_but_old"}, "stale"),
    ({"status": "stale_needs_refresh"}, "stale"),
])
def test_avoids_inventing_current_state_from_stale_or_missing_fields(kwargs, expected):
    s = snapshot(record(**kwargs))
    assert select_project_status(s, {"project_id": "homelab"},
                                 today=TODAY)["verification"]["freshness"] == expected


def test_duplicate_records_fail_closed_and_unavailable_state_is_explicit():
    one = record()
    with pytest.raises(StatusRejected, match="source_conflict"):
        select_project_status(snapshot(one, one), {"project_id": "homelab"}, today=TODAY)
    with pytest.raises(StatusRejected, match="source_unavailable"):
        select_project_status(snapshot(one), {"project_id": "recipe-intelligence"}, today=TODAY)


def test_assistant_result_correlates_only_with_trusted_request():
    req = request()
    result = answer_project_status(req, snapshot(record()), {"project_id": "homelab"}, today=TODAY)
    assert result.status == "completed"
    assert result.request_id == req.request_id
    assert result.request_hash == req.payload_hash
    assert result.surface == "chatgpt"
    assert result.reply_target == "conversation.123"
    assert result.capability == "project_os.status"
    assert "Samsung display" in result.text


def test_assistant_error_does_not_reveal_unknown_source_details():
    result = answer_project_status(request(), snapshot(), {"project_id": "unknown"}, today=TODAY)
    assert result.status == "unavailable"
    assert result.error_code == "source_unavailable"
    assert result.freshness == "unknown"


def test_sensitive_data_is_not_echoed_in_report():
    rec = record()
    rec.current_gate = "Do not show token=xyzSECRET or github_pat_1234567890"
    s = answer_project_status(request(), snapshot(rec), {"project_id": "homelab"}, today=TODAY)
    assert "xyzSECRET" not in s.text
    assert "github_pat_" not in s.text
    assert "[REDACTED]" in s.text


def test_registered_read_only_handler_reuses_core_collector_without_new_reader(monkeypatch):
    import personal_ai_brain.control as control
    calls = []
    state = snapshot(record())
    def collect(reader):
        calls.append(reader)
        return None, state, []
    monkeypatch.setattr(control.runtime, "collect", collect)
    reader = object()
    registry = build_registry(Path("/does-not-matter"), lambda: reader, Redactor())
    capability, arguments = registry.resolve("project_os.status", {"project_id": "homelab"})
    assert capability.risk_level == "read_only"
    assert capability.side_effect_class == "none"
    result = capability.handler(arguments)
    assert result["project_id"] == "homelab"
    assert calls == [reader]
    with pytest.raises(Rejected, match="invalid_project_selector"):
        registry.resolve("project_os.status", {"project_id": "homelab", "repository": "other"})


def test_registry_has_no_new_write_surface():
    registry = build_registry(Path("/unused"), lambda: None, Redactor())
    cap = registry.capabilities["project_os.status"]
    assert cap.risk_level == "read_only"
    assert cap.side_effect_class == "none"
    assert "repository" not in cap.argument_schema["properties"]
