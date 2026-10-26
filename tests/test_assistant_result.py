from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from personal_ai_brain.assistant_contract import normalize_request
from personal_ai_brain.assistant_result import (
    CAPABILITY, ERROR_MESSAGES, MAX_TEXT_BYTES, ResultRejected, build_result,
)


NOW = datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc)


def request(surface="chatgpt"):
    return normalize_request(
        {"schema_version": "0.1", "request_id": str(uuid4()),
         "message": "Where did we stop with Homelab?", "created_at": NOW.isoformat()},
        trusted_user_id="igor", trusted_surface=surface,
        trusted_reply_target="conversation.99" if surface == "chatgpt" else "wamid.A1",
        now=NOW,
    )


def test_completed_result_is_correlated_and_read_only():
    original = request()
    result = build_result(original, status="completed", freshness="stale",
                          public_text="Last verified on 2026-10-23; next step is acceptance.")
    assert result.schema_version == original.schema_version
    assert result.request_id == original.request_id
    assert result.request_hash == original.payload_hash
    assert result.user_id == original.user_id
    assert result.surface == original.surface
    assert result.reply_target == original.reply_target
    assert result.capability == CAPABILITY == "project_os.status"
    assert result.error_code is None
    with pytest.raises(FrozenInstanceError):
        result.status = "failed"


def test_same_shape_for_both_surfaces():
    for surface in ("chatgpt", "whatsapp"):
        orig = request(surface)
        reply = build_result(orig, status="completed", freshness="current",
                             public_text="Verified project status.")
        assert reply.surface == surface
        assert reply.reply_target == orig.reply_target


@pytest.mark.parametrize("freshness", ["current", "stale", "unknown", "conflicting"])
def test_completed_explicit_freshness(freshness):
    assert build_result(request(), status="completed", freshness=freshness,
                        public_text="Unknown means unverified, not empty.").freshness == freshness


@pytest.mark.parametrize("status,error_code,freshness", [
    ("unavailable", "source_unavailable", "unknown"),
    ("unavailable", "source_conflict", "conflicting"),
    ("rejected", "request_rejected", "unknown"),
    ("failed", "execution_failed", "unknown"),
])
def test_failures_use_only_fixed_public_messages(status, error_code, freshness):
    result = build_result(request(), status=status, freshness=freshness,
                          error_code=error_code)
    assert result.text == ERROR_MESSAGES[error_code]
    assert result.error_code == error_code
    assert "token" not in result.text.lower()


@pytest.mark.parametrize("kwargs", [
    {"status": "completed", "freshness": "current"},
    {"status": "completed", "freshness": "current", "public_text": ""},
    {"status": "completed", "freshness": "current", "public_text": "\x1b[31m"},
    {"status": "completed", "freshness": "current", "public_text": "\ud800"},
    {"status": "completed", "freshness": "current", "public_text": "č" * 1537},
    {"status": "completed", "freshness": "current", "public_text": "Hi", "error_code": "execution_failed"},
    {"status": "unavailable", "freshness": "unknown", "public_text": "raw auth token"},
    {"status": "unavailable", "freshness": "unknown", "error_code": "invalid_server_response"},
    {"status": "unavailable", "freshness": "current", "error_code": "source_unavailable"},
    {"status": "failed", "freshness": "unknown", "error_code": "source_unavailable"},
    {"status": "rejected", "freshness": "unknown", "error_code": "execution_failed"},
    {"status": "running", "freshness": "unknown"},
    {"status": "completed", "freshness": None, "public_text": "hello"},
    {"status": "completed", "freshness": "unverified", "public_text": "hello"},
])
def test_invalid_result_contract_fails_closed(kwargs):
    with pytest.raises(ResultRejected):
        build_result(request(), **kwargs)


def test_completed_text_boundary_uses_utf8_bytes():
    text = "č" * (MAX_TEXT_BYTES // 2)
    result = build_result(request(), status="completed", freshness="unknown",
                          public_text=text)
    assert len(result.text.encode("utf-8")) == MAX_TEXT_BYTES


def test_invalid_request_object_and_version_rejected():
    with pytest.raises(ResultRejected, match="request_required"):
        build_result({"request_id": "fake"}, status="completed", freshness="unknown",
                     public_text="hello")
    with pytest.raises(ResultRejected, match="request_version"):
        build_result(replace(request(), schema_version="9.9"),
                     status="completed", freshness="unknown", public_text="hello")


def test_failures_cannot_echo_private_exception():
    secret = "github_pat_PRIVATE_DO_NOT_ECHO"
    with pytest.raises(ResultRejected):
        build_result(request(), status="failed", freshness="unknown",
                     public_text=secret, error_code="execution_failed")
    safe = build_result(request(), status="failed", freshness="unknown",
                        error_code="execution_failed")
    assert secret not in repr(safe)
