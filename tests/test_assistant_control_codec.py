import json
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from personal_ai_brain.assistant_contract import normalize_request
from personal_ai_brain.assistant_control_codec import (
    CAPABILITY, TITLE, ControlCodecRejected, decode_project_status, encode_project_status,
)
from personal_ai_brain.control import parse_request


NOW = datetime(2026, 10, 26, 18, 30, tzinfo=timezone.utc)
AUTHOR_ID = 12345


def req(surface="chatgpt"):
    return normalize_request(
        {"schema_version": "0.1", "request_id": str(uuid4()),
         "message": "Kde sme skončili s homelabom?", "created_at": NOW.isoformat()},
        trusted_user_id="igor", trusted_surface=surface,
        trusted_reply_target="conversation.123" if surface == "chatgpt" else "wamid.A1",
        now=NOW,
    )


def status_data(**changes):
    return {
        "project_id": "homelab", "workstream_id": None,
        "project_name": "Homelab & Media Stack", "scope": "project",
        "status": "active", "phase": "post-migration",
        "attention": "active_secondary", "current_gate": "Samsung poster confirmation pending.",
        "exact_next_action": "Verify Heretic poster on TV. Do not replay Trakt writes.",
        "verification": {"last_verified": "2026-10-22", "age_days": 4,
                         "sync_status": "synced", "freshness": "current",
                         "finding_codes": []},
    } | changes


def wire_comment(request_id, result=None, **changes):
    envelope = {
        "schema_version": "0.1", "request_id": request_id,
        "capability": CAPABILITY, "status": "capability_completed",
        "execution_start": NOW.isoformat(), "execution_end": NOW.isoformat(),
        "error": None, "original_status": None,
        "result": status_data() if result is None else result,
    } | changes
    return "Personal AI Brain control result\n" + chr(96) * 3 + "json\n" + (
        json.dumps(envelope, ensure_ascii=True)) + "\n" + chr(96) * 3


def decode(request, body, selector=None, **kw):
    return decode_project_status(request, selector or {"project_id": "homelab"}, body,
                                 comment_author_id=kw.get("comment_author_id", AUTHOR_ID),
                                 authorized_author_id=kw.get("authorized_author_id", AUTHOR_ID))


def test_chatgpt_submission_reuses_accepted_control_wire_schema():
    request = req()
    envelope = encode_project_status(request, {"project_id": "homelab"})
    assert envelope.title == TITLE
    assert envelope.request_id == request.request_id
    payload = parse_request(envelope.body, now=NOW)
    assert payload["capability"] == "project_os.status"
    assert payload["arguments"] == {"project_id": "homelab"}
    assert payload["requested_at"] == request.created_at
    assert payload["requested_by"] == "chatgpt_assistant"


def test_one_typed_workstream_and_no_paths():
    original = req()
    data = encode_project_status(original, {"project_id": "homelab",
                                            "workstream_id": "media-automation"})
    assert json.loads(data.body)["arguments"] == {
        "project_id": "homelab", "workstream_id": "media-automation"}


@pytest.mark.parametrize("selector", [
    {}, {"project_id": "homelab", "repository": "other"},
    {"project_id": "homelab", "command": "whoami"},
    {"project_id": "../homelab"}, {"project_id": "homelab", "ref": "main"},
])
def test_selector_fail_closed(selector):
    with pytest.raises(ControlCodecRejected, match="invalid_project_selector"):
        encode_project_status(req(), selector)


def test_non_chatgpt_surface_cannot_use_early_adaptor():
    with pytest.raises(ControlCodecRejected, match="trusted_chatgpt_request_required"):
        encode_project_status(req("whatsapp"), {"project_id": "homelab"})
    with pytest.raises(ControlCodecRejected, match="trusted_chatgpt_request_required"):
        decode(req("whatsapp"), wire_comment(req().request_id))


def test_completed_response_correlated_to_trusted_reply_target():
    request = req()
    response = decode(request, wire_comment(request.request_id))
    assert response.status == "completed"
    assert response.request_id == request.request_id
    assert response.request_hash == request.payload_hash
    assert response.surface == "chatgpt"
    assert response.user_id == "igor"
    assert response.reply_target == "conversation.123"
    assert response.freshness == "current"
    assert "Verify Heretic poster" in response.text


def test_identical_delivery_is_deterministic_without_network():
    request = req()
    raw = wire_comment(request.request_id)
    assert decode(request, raw) == decode(request, raw)


def test_author_identity_checked_using_out_of_band_metadata():
    request = req()
    body = wire_comment(request.request_id)
    for author in (0, False, None, AUTHOR_ID + 1):
        with pytest.raises(ControlCodecRejected, match="untrusted_result_author"):
            decode(request, body, comment_author_id=author)
    with pytest.raises(ControlCodecRejected, match="untrusted_result_author"):
        decode(request, body, authorized_author_id=0)


def test_rejects_wrong_request_capability_and_project_not_echoing_data():
    request = req()
    wrong = req()
    with pytest.raises(ControlCodecRejected, match="result_correlation"):
        decode(request, wire_comment(wrong.request_id))
    with pytest.raises(ControlCodecRejected, match="result_correlation"):
        decode(request, wire_comment(request.request_id, capability="work.cancel"))
    with pytest.raises(ControlCodecRejected, match="result_correlation"):
        decode(request, wire_comment(request.request_id,
                                     result=status_data(project_id="recipe-intelligence")))


def test_wrong_workstream_does_not_borrow_another_result():
    request = req()
    with pytest.raises(ControlCodecRejected, match="result_correlation"):
        decode(request, wire_comment(request.request_id),
               {"project_id": "homelab", "workstream_id": "media-automation"})


def test_rejects_invalid_status_and_corrupt_or_double_key_json():
    request = req()
    with pytest.raises(ControlCodecRejected, match="result_status"):
        decode(request, wire_comment(request.request_id, status="running"))
    with pytest.raises(ControlCodecRejected, match="result_schema"):
        decode(request, "not a GitHub result")
    body = wire_comment(request.request_id)
    bad = body.replace('"status": "capability_completed",',
                       '"status": "capability_completed", "status": "failed",')
    with pytest.raises(ControlCodecRejected, match="result_schema"):
        decode(request, bad)


def test_truncated_and_failed_result_is_explicit_not_partial_or_exception():
    request = req()
    limited = decode(request, wire_comment(request.request_id, result={
        "truncated": True, "preview_json": '{"status": "active"', "full_result": "local_private_audit"
    }))
    assert limited.status == "unavailable"
    assert "active" not in limited.text
    failed = decode(request, wire_comment(request.request_id,
                    status="capability_execution_failed", error="source_read_failure",
                    result=None))
    assert failed.status == "unavailable"
    assert "source_read_failure" not in failed.text


def test_rejection_and_duplicate_use_existing_durable_transport_truth():
    request = req()
    rejected = decode(request, wire_comment(request.request_id, status="request_rejected",
                      error="request_conflict", result=None))
    assert rejected.status == "rejected"
    replay = decode(request, wire_comment(request.request_id, status="duplicate",
                    original_status="capability_completed"))
    assert replay.status == "completed"
    stopped = decode(request, wire_comment(request.request_id, status="duplicate",
                    original_status="capability_execution_failed", result=None))
    assert stopped.status == "unavailable"


def test_success_field_is_redacted_with_fixed_bounds():
    request = req()
    source = status_data(
        exact_next_action="Next, inspect token=privateVALUE and github_pat_AAA123456789."
    )
    response = decode(request, wire_comment(request.request_id, result=source))
    assert "privateVALUE" not in response.text
    assert "github_pat_" not in response.text
    assert "[REDACTED]" in response.text
    with pytest.raises(ControlCodecRejected, match="result_size"):
        decode(request, wire_comment(request.request_id,
                                     result=status_data(current_gate="x" * 2000)))


@pytest.mark.parametrize("fault", [
    {"verification": None},
    {"verification": {"freshness": "magically_live"}},
    {"phase": None},
    {"verification": {"freshness": "current", "last_verified": None,
                      "sync_status": "synced", "finding_codes": ["bad value!"]}},
])
def test_incomplete_or_unsafe_backend_data_fail_closed(fault):
    request = req()
    with pytest.raises(ControlCodecRejected, match="result_schema"):
        decode(request, wire_comment(request.request_id,
                                     result=status_data(**fault)))
