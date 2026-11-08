import errno
import hashlib
import json
import os
import stat
import threading

import pytest

import personal_ai_brain.whatsapp_gateway as module
from personal_ai_brain.whatsapp_gateway import GatewayRejected, authenticated_events
from test_whatsapp_gateway import Backend, Meta, NOW, SECRET, event, make, wire


def one(mid="wamid.one", body="status"):
    raw, signature = wire(event(message_id=mid, body=body))
    return authenticated_events(raw, signature, SECRET, waba_id="123", phone_id="456",
                                recipient="421900000001", sender="421900000002")


def test_durable_idempotent_and_no_external_effects(tmp_path, monkeypatch):
    backend, meta = Backend(), Meta(); gateway = make(tmp_path, backend, meta)
    calls = []
    original = os.fsync
    def observe(fd):
        calls.append(stat.S_ISDIR(os.fstat(fd).st_mode))
        return original(fd)
    monkeypatch.setattr(os, "fsync", observe)
    ids = gateway.ingest(one() + one(), now=NOW)
    assert len(ids) == 2 and ids[0] == ids[1]
    record = json.loads(next(tmp_path.glob("*.json")).read_text())
    assert record["request_id"] == ids[0] and record["control"] == "received"
    assert "status" not in json.dumps(record)
    assert False in calls
    if os.name != "nt": assert True in calls
    assert make(tmp_path, backend, meta).ingest(one(), now=NOW) == [ids[0]]
    assert gateway.poll() == ["received"]
    assert gateway.post(*wire(event(message_id="wamid.one", body="status")), now=NOW) == ["received"]
    assert backend.creates == 0 and meta.calls == []


def test_conflicts_are_preflighted_before_any_write(tmp_path):
    gateway = make(tmp_path)
    with pytest.raises(GatewayRejected, match="message_conflict"):
        gateway.ingest(one("wamid.new") + one("wamid.new", "different"), now=NOW)
    assert not list(tmp_path.glob("*.json"))
    gateway.ingest(one("wamid.existing"), now=NOW)
    with pytest.raises(GatewayRejected, match="message_conflict"):
        gateway.ingest(one("wamid.other") + one("wamid.existing", "different"), now=NOW)
    assert len(list(tmp_path.glob("*.json"))) == 1


def test_partial_save_replays_same_uuid(tmp_path, monkeypatch):
    gateway = make(tmp_path); original = module._save; count = 0
    def fail_second(path, record):
        nonlocal count
        count += 1
        if count == 2: raise OSError(errno.ENOSPC, "no space")
        original(path, record)
    monkeypatch.setattr(module, "_save", fail_second)
    batch = one("wamid.first") + one("wamid.second")
    with pytest.raises(GatewayRejected, match="ingest_unavailable"):
        gateway.ingest(batch, now=NOW)
    retained = json.loads(next(tmp_path.glob("*.json")).read_text())["request_id"]
    monkeypatch.setattr(module, "_save", original)
    ids = make(tmp_path).ingest(batch, now=NOW)
    assert ids[0] == retained and len(set(ids)) == 2


def test_directory_sync_failure_after_rename_retries_sync(tmp_path, monkeypatch):
    if os.name == "nt": pytest.skip("directory fsync is POSIX only")
    gateway = make(tmp_path); original = module._sync_directory; calls = 0
    def fail_once(root):
        nonlocal calls
        calls += 1
        if calls == 1: raise OSError(errno.EIO, "sync failure")
        original(root)
    monkeypatch.setattr(module, "_sync_directory", fail_once)
    with pytest.raises(GatewayRejected, match="ingest_unavailable"):
        gateway.ingest(one(), now=NOW)
    assert len(list(tmp_path.glob("*.json"))) == 1
    assert len(gateway.ingest(one(), now=NOW)) == 1
    assert calls == 2


def test_count_and_byte_capacity_fail_closed(tmp_path, monkeypatch):
    gateway = make(tmp_path)
    monkeypatch.setattr(module, "MAX_INGEST_RECORDS", 1)
    with pytest.raises(GatewayRejected, match="ingest_capacity"):
        gateway.ingest(one("wamid.1") + one("wamid.2"), now=NOW)
    assert not list(tmp_path.glob("*.json"))
    first = gateway.ingest(one("wamid.1"), now=NOW)
    assert gateway.ingest(one("wamid.1"), now=NOW) == first
    with pytest.raises(GatewayRejected, match="ingest_capacity"):
        gateway.ingest(one("wamid.2"), now=NOW)
    monkeypatch.setattr(module, "MAX_INGEST_RECORDS", 10)
    monkeypatch.setattr(module, "MAX_INGEST_BYTES", 1)
    with pytest.raises(GatewayRejected, match="ingest_capacity"):
        gateway.ingest(one("wamid.2"), now=NOW)
    assert len(list(tmp_path.glob("*.json"))) == 1


def test_corrupt_state_and_enospc_do_not_ack(tmp_path, monkeypatch):
    gateway = make(tmp_path)
    bad = tmp_path / ("0" * 64 + ".json"); bad.write_text("{")
    with pytest.raises(GatewayRejected, match="ingest_unavailable"):
        gateway.ingest(one(), now=NOW)
    bad.unlink()
    def enospc(*_): raise OSError(errno.ENOSPC, "disk full")
    monkeypatch.setattr(os, "replace", enospc)
    with pytest.raises(GatewayRejected, match="ingest_unavailable"):
        gateway.ingest(one(), now=NOW)
    assert not list(tmp_path.glob("*.json"))


def test_concurrent_ingest_is_serialized_and_retry_safe(tmp_path, monkeypatch):
    gateway = make(tmp_path); second = make(tmp_path)
    entered = threading.Event(); release = threading.Event(); original = module._save; errors = []
    def blocking(path, record):
        entered.set(); assert release.wait(5); original(path, record)
    def execute():
        try: gateway.ingest(one(), now=NOW)
        except Exception as e: errors.append(e)
    monkeypatch.setattr(module, "_save", blocking)
    thread = threading.Thread(target=execute); thread.start()
    try:
        assert entered.wait(5)
        with pytest.raises(GatewayRejected, match="gateway_busy"):
            second.ingest(one(), now=NOW)
    finally:
        release.set(); thread.join(timeout=5)
    assert not thread.is_alive() and not errors
    assert len(second.ingest(one(), now=NOW)) == 1
    assert len(list(tmp_path.glob("*.json"))) == 1


@pytest.mark.parametrize("control,delivery,expected", [
    ("submitting", "pending", "control_uncertain"),
    ("uncertain", "pending", "control_uncertain"),
    ("submitted", "sending", "delivery_uncertain"),
    ("submitted", "uncertain", "delivery_uncertain"),
    ("submitted", "sent", "sent"),
    ("submitted", "failed", "failed"),
])
def test_existing_states_remain_unchanged(tmp_path, control, delivery, expected):
    backend, meta = Backend(), Meta(); gateway = make(tmp_path, backend, meta)
    gateway.ingest(one(), now=NOW)
    path = next(tmp_path.glob("*.json")); record = json.loads(path.read_text())
    record.update(control=control, delivery=delivery)
    if control == "submitted": record["issue"] = 1
    path.write_text(json.dumps(record))
    assert gateway.ingest(one(), now=NOW) == [record["request_id"]]
    assert gateway.poll() == [expected]
    assert json.loads(path.read_text()) == record
    assert backend.creates == 0 and meta.calls == []


def test_duplicate_batch_of_1000_and_uncertain_record_never_auto_removed(tmp_path, monkeypatch):
    gateway = make(tmp_path)
    ids = gateway.ingest(one() * 1000, now=NOW)
    assert len(ids) == 1000 and len(set(ids)) == 1
    path = next(tmp_path.glob("*.json")); record = json.loads(path.read_text())
    record.update(control="uncertain", created_at="2020-01-01T00:00:00+00:00")
    path.write_text(json.dumps(record))
    monkeypatch.setattr(module, "MAX_INGEST_RECORDS", 1)
    with pytest.raises(GatewayRejected, match="ingest_capacity"):
        gateway.ingest(one("wamid.other"), now=NOW)
    assert path.exists() and json.loads(path.read_text())["control"] == "uncertain"


def test_corrupted_record_and_foreign_binding_fail_closed(tmp_path):
    gateway = make(tmp_path); gateway.ingest(one(), now=NOW)
    path = next(tmp_path.glob("*.json")); record = json.loads(path.read_text())
    record["binding"] = "0" * 64; path.write_text(json.dumps(record))
    with pytest.raises(GatewayRejected, match="ingest_state_invalid"):
        gateway.ingest(one("wamid.other"), now=NOW)
    assert len(list(tmp_path.glob("*.json"))) == 1


def test_worker_resumes_received_and_reuses_original_uuid(tmp_path):
    backend, meta = Backend(), Meta()
    gateway = make(tmp_path, backend, meta)
    ids = gateway.ingest(one(), now=NOW)
    assert gateway.work() == ["pending"]
    assert backend.creates == 1
    assert json.loads(backend.issues[1]["body"])["request_id"] == ids[0]
    backend.finish()
    assert gateway.work() == ["sent"]
    assert gateway.work() == []
    assert gateway.ingest(one(), now=NOW) == ids
    assert backend.creates == 1 and len(meta.calls) == 1


def test_worker_ambiguous_create_is_not_repeated(tmp_path):
    class Broken(Backend):
        def create_issue(self, title, body):
            super().create_issue(title, body)
            raise TimeoutError()
    backend = Broken()
    gateway = make(tmp_path, backend)
    gateway.ingest(one(), now=NOW)
    assert gateway.work() == ["control_uncertain"]
    assert make(tmp_path, backend).work() == []
    assert backend.creates == 1


def test_worker_releases_ingress_lock_during_network(tmp_path):
    backend = Backend()
    gateway = make(tmp_path, backend)
    gateway.ingest(one("wamid.original"), now=NOW)
    old_create = backend.create_issue
    def create(title, body):
        assert len(gateway.ingest(one("wamid.new"), now=NOW)) == 1
        return old_create(title, body)
    backend.create_issue = create
    assert gateway.work(max_items=1) == ["pending"]
    assert backend.creates == 1
    assert len(list(tmp_path.glob("*.json"))) == 2


def test_worker_reservation_survives_interruption_and_blocks_recreate(tmp_path):
    gateway = make(tmp_path)
    gateway.ingest(one(), now=NOW)
    path = next(tmp_path.glob("*.json"))
    record = json.loads(path.read_text())
    record["control"] = "submitting"
    path.write_text(json.dumps(record))
    assert make(tmp_path).work() == []


def test_worker_limit_and_duplicate_guard(tmp_path):
    backend = Backend()
    gateway = make(tmp_path, backend)
    gateway.ingest(one("wamid.first") + one("wamid.second"), now=NOW)
    with pytest.raises(GatewayRejected, match="invalid_worker_limit"):
        gateway.work(max_items=0)
    assert len(gateway.work(max_items=1)) == 1
    assert backend.creates == 1
    assert len(gateway.work(max_items=1)) == 1
    assert backend.creates <= 2  # sorted records may include the first pending item again


def test_worker_round_robin_does_not_starve_new_receipts(tmp_path):
    backend = Backend()
    gateway = make(tmp_path, backend)
    mids = [f"wamid.{i}" for i in range(18)]
    gateway.ingest([one(mid)[0] for mid in mids], now=NOW)
    for _ in range(18):
        gateway.work(max_items=1)
    assert backend.creates == 18
    assert gateway.work(max_items=1) == ["pending"]


def test_signed_large_batch_is_durable_before_worker_and_survives_restart(tmp_path):
    backend, meta = Backend(), Meta()
    gateway = make(tmp_path, backend, meta)
    data = event(message_id="wamid.bulk")
    message = data["entry"][0]["changes"][0]["value"]["messages"][0]
    data["entry"][0]["changes"][0]["value"]["messages"] = [message] * 1000
    raw, signature = wire(data)
    assert len(raw) > module.MAX_WEBHOOK_BYTES
    ids = gateway.receive(raw, signature, now=NOW)
    assert len(ids) == 1000 and len(set(ids)) == 1
    assert backend.creates == 0 and not meta.calls
    restarted = make(tmp_path, backend, meta)
    assert restarted.receive(raw, signature, now=NOW) == ids
    assert restarted.work() == ["pending"]
    assert backend.creates == 1


def test_meta_request_byte_limit_hmac_and_aggregate_updates(tmp_path):
    gateway = make(tmp_path)
    raw, sig = wire(event())
    padded = raw + b" " * (module.MAX_META_WEBHOOK_BYTES - len(raw))
    signed = "sha256=" + __import__("hmac").new(SECRET, padded, hashlib.sha256).hexdigest()
    assert gateway.receive(padded, signed, now=NOW)
    with pytest.raises(GatewayRejected, match="payload_size"):
        gateway.receive(padded + b" ", signed, now=NOW)
    with pytest.raises(GatewayRejected, match="invalid_signature"):
        gateway.receive(raw + b" ", sig, now=NOW)
    data = event()
    changes = data["entry"][0]["changes"]
    changes[:] = changes * 1001
    with pytest.raises(GatewayRejected, match="invalid_payload"):
        gateway.receive(*wire(data), now=NOW)


def test_meta_http_ack_is_independent_of_slow_backend(tmp_path):
    import http.client
    from datetime import datetime, timezone
    from personal_ai_brain.whatsapp_runtime import RuntimeHTTPServer
    from test_whatsapp_gateway import event, wire
    gate_started, release = threading.Event(), threading.Event()
    backend = Backend()
    old_create = backend.create_issue
    def delayed(title, body):
        gate_started.set()
        assert release.wait(3)
        return old_create(title, body)
    backend.create_issue = delayed
    gateway = make(tmp_path, backend)
    server = RuntimeHTTPServer(("127.0.0.1", 0), gateway, "verify")
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    raw, signature = wire(event(message_id="wamid.slow", timestamp=str(int(datetime.now(timezone.utc).timestamp()))))
    conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
    try:
        conn.request("POST", "/webhook", raw, {"X-Hub-Signature-256": signature})
        reply = conn.getresponse()
        assert reply.status == 200
        reply.read()
        worker_thread = threading.Thread(target=gateway.work)
        worker_thread.start()
        assert gate_started.wait(3)
        raw2, signature2 = wire(event(message_id="wamid.other", timestamp=str(int(datetime.now(timezone.utc).timestamp()))))
        conn2 = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
        conn2.request("POST", "/webhook", raw2, {"X-Hub-Signature-256": signature2})
        reply2 = conn2.getresponse()
        assert reply2.status == 200
        reply2.read()
        conn2.close()
        assert backend.creates == 0
    finally:
        release.set()
        conn.close()
        if "worker_thread" in locals():
            worker_thread.join(timeout=5)
        server.shutdown()
        server_thread.join(timeout=5)
        server.server_close()
    assert len(list(tmp_path.glob("*.json"))) == 2


def test_meta_mixed_batch_skips_invalid_text_without_poisoning_valid(tmp_path):
    backend = Backend()
    gateway = make(tmp_path, backend)
    data = event(message_id="wamid.good")
    messages = data["entry"][0]["changes"][0]["value"]["messages"]
    messages.append(dict(messages[0], id="wamid.bad", text={"body": "x" * 4097}))
    accepted = gateway.receive(*wire(data), now=NOW)
    assert len(accepted) == 1
    assert gateway.work() == ["pending"]
    assert backend.creates == 1
