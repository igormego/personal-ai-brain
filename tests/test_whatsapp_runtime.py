import http.client
import io
import json
import threading
import time
from pathlib import Path
from urllib.parse import urlencode

import pytest

from personal_ai_brain.whatsapp_gateway import GatewayRejected, MAX_WEBHOOK_BYTES
from personal_ai_brain.whatsapp_runtime import JsonHTTP, RuntimeHTTPServer, load_runtime, poll_loop


class FakeGateway:
    def __init__(self):
        self.received = []; self.polls = 0; self.fail_poll = False

    def post(self, raw, signature):
        if signature != "fake-valid":
            raise GatewayRejected("invalid_signature")
        self.received.append(raw)

    def poll(self):
        self.polls += 1
        if self.fail_poll:
            raise RuntimeError("private error")
        return []


@pytest.fixture
def running():
    gateway = FakeGateway()
    server = RuntimeHTTPServer(("127.0.0.1", 0), gateway, "test-verify")
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    yield server, gateway
    server.shutdown(); thread.join(); server.server_close()


def call(server, method, path, body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
    conn.request(method, path, body, headers or {})
    response = conn.getresponse(); result = response.status, response.read(); conn.close()
    return result


def test_http_challenge_signature_size_and_health(running):
    server, gateway = running
    params = {"hub.mode": "subscribe", "hub.verify_token": "test-verify", "hub.challenge": "hello"}
    assert call(server, "GET", "/webhook?" + urlencode(params)) == (200, b"hello")
    params["hub.verify_token"] = "bad"
    assert call(server, "GET", "/webhook?" + urlencode(params))[0] == 403
    assert call(server, "POST", "/webhook", b"{}", {"X-Hub-Signature-256": "bad"})[0] == 403
    assert call(server, "POST", "/webhook", b"{}", {"X-Hub-Signature-256": "fake-valid"})[0] == 200
    assert gateway.received == [b"{}"]
    assert call(server, "POST", "/webhook", b"x" * (MAX_WEBHOOK_BYTES + 1), {"X-Hub-Signature-256": "fake-valid"})[0] == 413
    assert call(server, "GET", "/health")[0] == 503
    server.poll_at = time.monotonic(); server.poll_ok = True
    assert call(server, "GET", "/health") == (200, b"ok")
    assert call(server, "GET", "/other")[0] == 404


def test_poll_failure_and_recovery(running):
    server, gateway = running
    stop = threading.Event(); gateway.fail_poll = True
    thread = threading.Thread(target=poll_loop, args=(server, stop, 0.01)); thread.start()
    time.sleep(0.03)
    assert not server.poll_ok
    gateway.fail_poll = False
    time.sleep(0.03)
    stop.set(); thread.join(timeout=1)
    assert not thread.is_alive() and server.poll_ok and gateway.polls >= 2


def test_network_client_pinned_and_safe_errors():
    requests = []
    class Response:
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def read(self, size): return b'{"ok":true}'
    def fake(request, timeout):
        requests.append((request, timeout)); return Response()
    client = JsonHTTP("https://api.github.com", "fake-token", opener=fake)
    assert client.request("GET", "/repos/test/repo") == {"ok": True}
    assert requests[0][0].full_url == "https://api.github.com/repos/test/repo"
    with pytest.raises(ValueError): client.request("GET", "//evil.example")
    with pytest.raises(ValueError): JsonHTTP("https://evil.example")
    client.open = lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("fake-token"))
    with pytest.raises(RuntimeError, match="^external_request_failed$"):
        client.request("GET", "/repos/test/repo")


def test_config_reads_only_explicit_files(tmp_path):
    config = {"owner_id": 123, "user_id": "igor", "waba_id": "123", "phone_id": "456",
              "recipient": "+1 555 123", "sender": "421900000002", "api_version": "v22.0"}
    cfg = tmp_path / "config.json"; cfg.write_text(json.dumps(config))
    secrets = []
    for name in ("github", "meta", "app", "verify"):
        path = tmp_path / name; path.write_text("test-" + name); secrets.append(path)
    gateway, verify = load_runtime(cfg, tmp_path / "state", *secrets)
    assert gateway.sender == config["sender"] and verify == "test-verify"
    secrets[0].write_text("")
    with pytest.raises(ValueError, match="invalid_secret"):
        load_runtime(cfg, tmp_path / "state", *secrets)


def test_isolated_compose_contract():
    import yaml
    config = yaml.safe_load(Path("compose.whatsapp.yaml").read_text(encoding="utf-8"))
    service = config["services"]["gateway"]
    assert service["ports"][0].startswith("127.0.0.1:")
    assert service["read_only"] is True and service["cap_drop"] == ["ALL"]
    assert {mount["target"] for mount in service["volumes"]} == {"/state", "/run/config/whatsapp.json"}
    assert len(service["secrets"]) == 4 and "build" not in service
