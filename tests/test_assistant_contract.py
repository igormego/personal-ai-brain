"""No adapter/transport dependencies. Contract does not execute or persist."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from personal_ai_brain.assistant_contract import RequestRejected, normalize_request


NOW = datetime(2026, 10, 26, 10, 0, tzinfo=timezone.utc)


def sample(**changes):
    data = {"schema_version": "0.1", "request_id": str(uuid4()),
            "message": "Where are we with Recipe Intelligence?",
            "created_at": NOW.isoformat()}
    return data | changes


def normalize(data=None, **trusted):
    context = {"trusted_user_id": "igor", "trusted_surface": "chatgpt",
               "trusted_reply_target": "conversation.123", "now": NOW}
    return normalize_request(data if data is not None else sample(), **(context | trusted))


def test_one_envelope_two_adapters_share_same_contract():
    data = sample()
    chatgpt = normalize(data)
    whatsapp = normalize(data, trusted_surface="whatsapp", trusted_reply_target="wamid.A1")
    assert chatgpt.user_id == whatsapp.user_id == "igor"
    assert (chatgpt.surface, whatsapp.surface) == ("chatgpt", "whatsapp")
    assert chatgpt.request_id == whatsapp.request_id == data["request_id"]
    assert chatgpt.payload_hash != whatsapp.payload_hash
    assert len(chatgpt.payload_hash) == 64


def test_identical_delivery_same_hash_and_modified_payload_conflicts():
    data = sample()
    a = normalize(data)
    assert a == normalize(dict(reversed(list(data.items()))))
    assert a.payload_hash != normalize(data | {"message": "Changed request"}).payload_hash
    assert a.payload_hash != normalize(data, trusted_user_id="other").payload_hash
    assert a.payload_hash != normalize(data, trusted_reply_target="conversation.456").payload_hash


def test_equivalent_timestamps_normalize_to_same_utc_hash():
    data = sample()
    assert normalize(data).payload_hash == normalize(data | {
        "created_at": "2026-10-26T11:00:00+01:00"}).payload_hash


@pytest.mark.parametrize("patch", [
    {"user_id": "spoof"}, {"surface": "whatsapp"}, {"reply_target": "wrong"},
    {"attachments": []}, {"capability": "run_shell"},
    {"schema_version": "0.2"}, {"request_id": "../a"},
    {"request_id": str(uuid4()).upper()}, {"request_id": 123},
    {"message": " "}, {"message": None}, {"message": "a" * 4097},
    {"message": "\x00"}, {"message": "\ud800"},
    {"created_at": "2026-10-26"}, {"created_at": "2026-99-26T10:00:00Z"},
    {"created_at": "2026-10-26T10:00:00"},
    {"created_at": (NOW + timedelta(minutes=6)).isoformat()},
    {"created_at": (NOW - timedelta(days=8)).isoformat()},
    {"created_at": 26},
])
def test_untrusted_payload_rejections(patch):
    with pytest.raises(RequestRejected):
        normalize(sample(**patch))


@pytest.mark.parametrize("override", [
    {"trusted_user_id": ""}, {"trusted_user_id": "a/b"},
    {"trusted_surface": "telegram"}, {"trusted_surface": None},
    {"trusted_surface": []},
    {"trusted_reply_target": "../other"}, {"trusted_reply_target": ""},
    {"trusted_reply_target": "target with space"},
    {"now": datetime(2026, 10, 26, 10)},
])
def test_trusted_adapter_inputs_are_checked(override):
    with pytest.raises(RequestRejected):
        normalize(sample(), **override)


def test_boundaries_and_surfaces_do_not_perform_actions(tmp_path):
    data = sample(message="č" * 2048)
    request = normalize(data)
    assert len(request.message.encode("utf-8")) == 4096
    assert not list(tmp_path.iterdir())


def test_stringified_body_not_implicitly_accepted():
    with pytest.raises(RequestRejected, match="request_schema"):
        normalize('{"schema_version":"0.1"}')
