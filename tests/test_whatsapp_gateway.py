import hashlib
import hmac
import json
from datetime import datetime, timezone

import pytest

from personal_ai_brain.whatsapp_gateway import (
    Gateway, GatewayRejected, MetaCloudClient, PrivateControlIssues, authenticated_events, verify_challenge,
)

NOW = datetime(2026, 11, 7, 12, tzinfo=timezone.utc)
SECRET = b"fake-app-secret"
REPO = "igormego/personal-project-brain"


def event(message_id="wamid.123", body="Ako pokračuje Homelab?", timestamp=None):
    return {"object": "whatsapp_business_account", "entry": [{"id": "123", "changes": [{
        "field": "messages", "value": {"metadata": {"phone_number_id": "456", "display_phone_number": "421900000001"},
        "messages": [{"from": "421900000002", "id": message_id, "timestamp": timestamp or str(int(NOW.timestamp())),
                      "type": "text", "text": {"body": body}}]}}]}]}


def wire(data):
    raw = json.dumps(data, ensure_ascii=False).encode()
    return raw, "sha256=" + hmac.new(SECRET, raw, hashlib.sha256).hexdigest()


class Backend:
    def __init__(self):
        self.issues = {}; self.comments = []; self.creates = 0

    def create_issue(self, title, body):
        self.creates += 1
        number = self.creates
        self.issues[number] = {"number": number, "repository": REPO, "private": True, "title": title,
                               "body": body, "author_id": 42, "is_pull_request": False}
        return number

    def get_issue(self, number):
        return self.issues[number]

    def list_comments(self, number):
        return self.comments

    def finish(self, freshness="stale"):
        request_id = json.loads(self.issues[1]["body"])["request_id"]
        result = {"project_id": "homelab", "workstream_id": None, "project_name": "Homelab",
                  "phase": "active", "status": "active", "current_gate": "gate", "exact_next_action": "next",
                  "verification": {"freshness": freshness, "last_verified": "2026-10-22", "sync_status": "synced", "finding_codes": []}}
        data = {"schema_version": "0.1", "request_id": request_id, "capability": "project_os.status",
                "status": "capability_completed", "result": result}
        self.comments = [{"author_id": 42, "body": "Personal AI Brain control result\n```json\n" + json.dumps(data) + "\n```"}]


class Meta:
    def __init__(self, fail=False):
        self.calls = []; self.fail = fail

    def send_text(self, recipient, text):
        self.calls.append((recipient, text))
        if self.fail:
            raise TimeoutError("private data")
        return "wamid.outbound"


def make(tmp_path, backend=None, meta=None):
    return Gateway(state_dir=tmp_path, backend=backend or Backend(), meta=meta or Meta(), repository=REPO,
                   owner_id=42, user_id="igor", waba_id="123", phone_id="456", recipient="421900000001",
                   sender="421900000002", app_secret=SECRET)


def test_verification_and_signature():
    assert verify_challenge({"hub.mode": "subscribe", "hub.verify_token": "secret", "hub.challenge": "abc"}, "secret") == "abc"
    with pytest.raises(GatewayRejected):
        verify_challenge({"hub.mode": "subscribe", "hub.verify_token": "bad", "hub.challenge": "abc"}, "secret")
    raw, signature = wire(event())
    assert authenticated_events(raw, signature, SECRET, waba_id="123", phone_id="456", recipient="421900000001", sender="421900000002")
    for raw_bad, sig_bad in [(raw + b" ", signature), (raw, "sha256=" + "0" * 64), (b"x" * 65537, signature)]:
        with pytest.raises(GatewayRejected):
            authenticated_events(raw_bad, sig_bad, SECRET, waba_id="123", phone_id="456", recipient="421900000001", sender="421900000002")


def test_roundtrip_restart_duplicate_and_stale(tmp_path):
    backend, meta = Backend(), Meta()
    gateway = make(tmp_path, backend, meta)
    data = wire(event())
    assert gateway.post(*data, now=NOW) == ["pending"]
    backend.finish()
    gateway = make(tmp_path, backend, meta)
    assert gateway.poll() == ["sent"]
    assert gateway.post(*data, now=NOW) == ["sent"]
    assert backend.creates == 1 and len(meta.calls) == 1
    assert "stale" in meta.calls[0][1] and "2026-10-22" in meta.calls[0][1]
    persisted = next(tmp_path.glob("*.json")).read_text()
    assert "Ako pokračuje" not in persisted
    assert json.loads(backend.issues[1]["body"])["requested_by"] == "whatsapp_assistant"


def test_conflict_and_wrong_endpoints_no_effect(tmp_path):
    backend = Backend(); gateway = make(tmp_path, backend)
    gateway.post(*wire(event()), now=NOW)
    with pytest.raises(GatewayRejected, match="message_conflict"):
        gateway.post(*wire(event(body="different")), now=NOW)
    for key, value in [("from", "421900000003"), ("type", "image"), ("timestamp", "not-a-time")]:
        item = event(message_id="wamid.other")
        item["entry"][0]["changes"][0]["value"]["messages"][0][key] = value
        assert gateway.post(*wire(item), now=NOW) == []
    item = event(); item["entry"][0]["changes"][0]["value"]["metadata"]["phone_number_id"] = "777"
    assert gateway.post(*wire(item), now=NOW) == []
    assert gateway.post(*wire(event(timestamp="1")), now=NOW) == []
    assert backend.creates == 1


def test_untrusted_result_and_issue_fail_closed(tmp_path):
    backend, meta = Backend(), Meta(); gateway = make(tmp_path, backend, meta)
    gateway.post(*wire(event()), now=NOW); backend.finish()
    backend.comments[0]["author_id"] = 99
    assert gateway.poll() == ["pending"]
    backend.finish(); backend.issues[1]["private"] = False
    with pytest.raises(GatewayRejected, match="untrusted_issue"):
        gateway.poll()
    assert meta.calls == []


def test_ambiguous_send_is_never_repeated_after_restart(tmp_path):
    backend, meta = Backend(), Meta(fail=True); gateway = make(tmp_path, backend, meta)
    gateway.post(*wire(event()), now=NOW); backend.finish()
    assert gateway.poll() == ["delivery_uncertain"]
    assert make(tmp_path, backend, meta).poll() == ["delivery_uncertain"]
    assert len(meta.calls) == 1


def test_ambiguous_submission_is_never_repeated(tmp_path):
    class Broken(Backend):
        def create_issue(self, title, body):
            super().create_issue(title, body)
            raise TimeoutError()
    backend = Broken(); gateway = make(tmp_path, backend)
    assert gateway.post(*wire(event()), now=NOW) == ["control_uncertain"]
    assert make(tmp_path, backend).post(*wire(event()), now=NOW) == ["control_uncertain"]
    assert backend.creates == 1


def test_meta_api_is_injected_and_bounded():
    calls = []
    def fake(url, headers, body):
        calls.append((url, headers, body)); return {"messages": [{"id": "wamid.reply"}]}
    client = MetaCloudClient(phone_id="456", access_token="fake", api_version="v22.0", http_post=fake)
    assert client.send_text("421900000002", "status") == "wamid.reply"
    assert calls[0][0] == "https://graph.facebook.com/v22.0/456/messages"
    assert calls[0][2]["to"] == "421900000002"


def test_private_transport_checks_repository_and_constrains_paths():
    calls = []
    def fake(method, path, payload):
        calls.append((method, path, payload))
        if path == "/repos/" + REPO:
            return {"private": True, "full_name": REPO, "has_issues": True}
        return {"number": 9}
    transport = PrivateControlIssues(fake)
    assert transport.create_issue("pab-control/v0.1", "{}") == 9
    assert calls[-1][1] == "/repos/" + REPO + "/issues"
    with pytest.raises(GatewayRejected):
        transport.create_issue("other", "{}")


def test_crash_after_send_reservation_stops_replay(tmp_path):
    backend, meta = Backend(), Meta(); gateway = make(tmp_path, backend, meta)
    gateway.post(*wire(event(message_id="wamid.test==")), now=NOW); backend.finish()
    path = next(tmp_path.glob("*.json")); record = json.loads(path.read_text())
    record["delivery"] = "sending"; path.write_text(json.dumps(record))
    assert make(tmp_path, backend, meta).poll() == ["delivery_uncertain"]
    assert meta.calls == []


def test_forged_signed_json_duplicate_fields_and_oversized_text(tmp_path):
    gateway = make(tmp_path)
    raw = b'{"object":"whatsapp_business_account","object":"other"}'
    signature = "sha256=" + hmac.new(SECRET, raw, hashlib.sha256).hexdigest()
    with pytest.raises(GatewayRejected):
        gateway.post(raw, signature, now=NOW)
    assert gateway.post(*wire(event(body="x" * 4097)), now=NOW) == []
    assert list(tmp_path.glob("*.json")) == []


def test_wrong_result_uuid_and_multiple_results_fail_closed(tmp_path):
    backend, meta = Backend(), Meta(); gateway = make(tmp_path, backend, meta)
    gateway.post(*wire(event()), now=NOW); backend.finish()
    backend.comments[0]["body"] = backend.comments[0]["body"].replace(json.loads(backend.issues[1]["body"])["request_id"], "00000000-0000-4000-8000-000000000000")
    with pytest.raises(GatewayRejected):
        gateway.poll()
    backend.finish(); backend.comments.append(backend.comments[0])
    with pytest.raises(GatewayRejected, match="ambiguous_result"):
        gateway.poll()
    assert not meta.calls


def test_runtime_end_to_end_over_loopback_with_fake_clients(tmp_path):
    import http.client
    import threading
    from personal_ai_brain.whatsapp_runtime import RuntimeHTTPServer
    backend, meta = Backend(), Meta()
    gateway = make(tmp_path, backend, meta)
    server = RuntimeHTTPServer(("127.0.0.1", 0), gateway, "verify")
    worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
    try:
        raw, signature = wire(event(timestamp=str(int(datetime.now(timezone.utc).timestamp()))))
        conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
        conn.request("POST", "/webhook", raw, {"X-Hub-Signature-256": signature})
        response = conn.getresponse(); assert response.status == 200; response.read(); conn.close()
        backend.finish(); assert gateway.poll() == ["sent"]
        assert backend.creates == 1 and len(meta.calls) == 1
    finally:
        server.shutdown(); worker.join(); server.server_close()


def test_signed_mixed_batch_ignores_out_of_scope_and_replay_is_idempotent(tmp_path):
    backend, meta = Backend(), Meta(); gateway = make(tmp_path, backend, meta)
    data = event(message_id="wamid.one")
    value = data["entry"][0]["changes"][0]["value"]
    valid = value["messages"][0]
    value["messages"] = [dict(valid, **{"from": "999"}), dict(valid, type="image"),
                         dict(valid, id="wamid.invalid", timestamp="bad"),
                         valid, dict(valid, id="wamid.two"), valid]
    value["statuses"] = [{"id": "irrelevant", "status": "delivered"}]
    assert gateway.post(*wire(data), now=NOW) == ["pending", "pending", "pending"]
    assert gateway.post(*wire(data), now=NOW) == ["pending", "pending", "pending"]
    assert backend.creates == 2 and len(list(tmp_path.glob("*.json"))) == 2
    assert not meta.calls
    # A valid event for another recipient or WABA cannot poison the trusted batch.
    other = event(message_id="wamid.other")["entry"][0]
    other["id"] = "999"
    data["entry"].insert(0, other)
    assert len(gateway.post(*wire(data), now=NOW)) == 3
    assert backend.creates == 2


def test_batch_retry_after_partial_progress_never_recreates_issue(tmp_path):
    class Interrupted(Backend):
        def __init__(self):
            super().__init__(); self.once = True
        def get_issue(self, number):
            if self.once:
                self.once = False
                raise TimeoutError()
            return super().get_issue(number)
    backend = Interrupted(); gateway = make(tmp_path, backend)
    data = event(message_id="wamid.first")
    messages = data["entry"][0]["changes"][0]["value"]["messages"]
    messages.append(dict(messages[0], id="wamid.second"))
    with pytest.raises(GatewayRejected, match="control_unavailable"):
        gateway.post(*wire(data), now=NOW)
    assert backend.creates == 1
    assert make(tmp_path, backend).post(*wire(data), now=NOW) == ["pending", "pending"]
    assert backend.creates == 2


def test_signed_payload_and_array_bounds_are_explicit(tmp_path):
    from personal_ai_brain.whatsapp_gateway import MAX_WEBHOOK_BYTES
    gateway = make(tmp_path)
    data = event(); messages = data["entry"][0]["changes"][0]["value"]["messages"]
    messages[:] = [dict(messages[0], **{"from": "999"}) for _ in range(100)]
    raw, _ = wire(data)
    assert len(raw) < MAX_WEBHOOK_BYTES
    padded = raw + b" " * (MAX_WEBHOOK_BYTES - len(raw))
    sign = lambda b: "sha256=" + hmac.new(SECRET, b, hashlib.sha256).hexdigest()
    assert gateway.post(padded, sign(padded), now=NOW) == []
    with pytest.raises(GatewayRejected, match="payload_size"):
        gateway.post(padded + b" ", sign(padded + b" "), now=NOW)
    messages.append(messages[0])
    with pytest.raises(GatewayRejected, match="invalid_payload"):
        gateway.post(*wire(data), now=NOW)
    assert not list(tmp_path.glob("*.json"))


def test_untrusted_and_unsupported_do_not_bypass_raw_signature(tmp_path):
    gateway = make(tmp_path)
    data = event(); data["entry"][0]["changes"][0]["value"]["messages"][0]["type"] = "image"
    raw, sig = wire(data)
    with pytest.raises(GatewayRejected, match="invalid_signature"):
        gateway.post(raw + b" ", sig, now=NOW)
    assert gateway.post(raw, sig, now=NOW) == []


def test_http_signed_out_of_scope_event_is_acknowledged(tmp_path):
    import http.client
    import threading
    from personal_ai_brain.whatsapp_runtime import RuntimeHTTPServer
    backend = Backend(); gateway = make(tmp_path, backend)
    server = RuntimeHTTPServer(("127.0.0.1", 0), gateway, "verify")
    worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
    try:
        data = event(timestamp=str(int(datetime.now(timezone.utc).timestamp())))
        data["entry"][0]["changes"][0]["value"]["messages"][0]["type"] = "image"
        raw, signature = wire(data)
        conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
        conn.request("POST", "/webhook", raw, {"X-Hub-Signature-256": signature})
        response = conn.getresponse(); assert response.status == 200; response.read(); conn.close()
        assert backend.creates == 0
    finally:
        server.shutdown(); worker.join(); server.server_close()


def test_http_retry_after_transient_failure_reuses_reserved_issue(tmp_path):
    import http.client
    import threading
    from personal_ai_brain.whatsapp_runtime import RuntimeHTTPServer
    class Interrupted(Backend):
        def __init__(self):
            super().__init__(); self.once = True
        def get_issue(self, number):
            if self.once:
                self.once = False
                raise TimeoutError()
            return super().get_issue(number)
    backend = Interrupted(); server = RuntimeHTTPServer(("127.0.0.1", 0), make(tmp_path, backend), "verify")
    worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
    try:
        raw, signature = wire(event(timestamp=str(int(datetime.now(timezone.utc).timestamp()))))
        for expected in (503, 200):
            conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
            conn.request("POST", "/webhook", raw, {"X-Hub-Signature-256": signature})
            response = conn.getresponse(); assert response.status == expected; response.read(); conn.close()
        assert backend.creates == 1
    finally:
        server.shutdown(); worker.join(); server.server_close()
