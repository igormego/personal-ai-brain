"""Isolated, opt-in WhatsApp HTTP runtime. No network or secrets at import time."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .github_issues import REPOSITORY
from .whatsapp_gateway import Gateway, GatewayRejected, MAX_META_WEBHOOK_BYTES, MetaCloudClient, PrivateControlIssues, verify_challenge

MAX_RESPONSE = 2_000_000
TIMEOUT = 10


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class JsonHTTP:
    """One fixed HTTPS origin; no redirects or implicit retries."""
    def __init__(self, origin, token=None, opener=None):
        if origin not in {"https://api.github.com", "https://graph.facebook.com"}:
            raise ValueError("invalid_origin")
        self.origin, self.token = origin, token
        self.open = opener or build_opener(NoRedirect()).open

    def request(self, method, path, body=None, headers=None):
        if (method not in {"GET", "POST"} or not isinstance(path, str)
                or not path.startswith("/") or path.startswith("//") or "#" in path
                or "\\" in path or any(ord(c) < 32 for c in path)):
            raise ValueError("invalid_path")
        request_headers = {"Accept": "application/json", "User-Agent": "pab-whatsapp-gateway",
                           "Content-Type": "application/json"}
        if self.token:
            request_headers["Authorization"] = "Bearer " + self.token
            request_headers["X-GitHub-Api-Version"] = "2022-11-28"
        if headers:
            request_headers.update(headers)
        payload = json.dumps(body, separators=(",", ":")).encode() if body is not None else None
        request = Request(self.origin + path, data=payload, headers=request_headers, method=method)
        try:
            with self.open(request, timeout=TIMEOUT) as response:
                raw = response.read(MAX_RESPONSE + 1)
                if len(raw) > MAX_RESPONSE:
                    raise ValueError()
                return json.loads(raw)
        except Exception:
            raise RuntimeError("external_request_failed") from None

    def meta_post(self, url, headers, body):
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or parsed.netloc != "graph.facebook.com"
                or parsed.query or parsed.fragment):
            raise ValueError("invalid_origin")
        return self.request("POST", parsed.path, body, headers)


def _secret(path):
    # Paths are fixed by trusted runtime configuration, never by webhook data.
    data = Path(path).read_bytes()
    if not 1 <= len(data) <= 4096:
        raise ValueError("invalid_secret")
    value = data.decode("utf-8").strip()
    if not value or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError("invalid_secret")
    return value


def load_runtime(config_path, state_dir, github_secret, meta_secret, app_secret, verify_secret):
    raw = Path(config_path).read_bytes()
    if len(raw) > 8192:
        raise ValueError("invalid_configuration")
    config = json.loads(raw)
    fields = {"owner_id", "user_id", "waba_id", "phone_id", "recipient", "sender", "api_version"}
    if not isinstance(config, dict) or set(config) != fields:
        raise ValueError("invalid_configuration")
    github = JsonHTTP("https://api.github.com", _secret(github_secret))
    meta_http = JsonHTTP("https://graph.facebook.com")
    meta = MetaCloudClient(phone_id=config["phone_id"], access_token=_secret(meta_secret),
                           api_version=config["api_version"], http_post=meta_http.meta_post)
    gateway = Gateway(state_dir=state_dir, backend=PrivateControlIssues(github.request), meta=meta,
                      repository=REPOSITORY, owner_id=config["owner_id"], user_id=config["user_id"],
                      waba_id=config["waba_id"], phone_id=config["phone_id"], recipient=config["recipient"],
                      sender=config["sender"], app_secret=_secret(app_secret).encode())
    return gateway, _secret(verify_secret)


class Handler(BaseHTTPRequestHandler):
    server_version = "PAB"
    sys_version = ""
    protocol_version = "HTTP/1.0"

    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, *args):
        pass  # Never log query tokens, message content or external errors.

    def reply(self, status, body=b""):
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlsplit(self.path)
        if parsed.path == "/health" and not parsed.query:
            healthy = self.server.poll_ok and time.monotonic() - self.server.poll_at < 180
            self.reply(200 if healthy else 503, b"ok" if healthy else b"unavailable")
            return
        if parsed.path != "/webhook":
            self.reply(404); return
        try:
            params = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True, max_num_fields=8)
            if any(len(value) != 1 for value in params.values()):
                raise ValueError()
            challenge = verify_challenge({key: value[0] for key, value in params.items()}, self.server.verify_token)
            self.reply(200, challenge.encode("utf-8"))
        except (GatewayRejected, ValueError, UnicodeError):
            self.reply(403)

    def do_POST(self):
        if self.path != "/webhook":
            self.reply(404); return
        lengths = self.headers.get_all("Content-Length", [])
        signatures = self.headers.get_all("X-Hub-Signature-256", [])
        if (len(lengths) != 1 or len(lengths[0]) > 10 or not lengths[0].isascii()
                or not lengths[0].isdigit() or len(signatures) != 1
                or self.headers.get_all("Transfer-Encoding")):
            self.reply(400); return
        length = int(lengths[0])
        if length > MAX_META_WEBHOOK_BYTES:
            self.reply(413); return
        try:
            body = self.rfile.read(length)
            if len(body) != length:
                self.reply(400); return
            self.server.gateway.receive(body, signatures[0])
            self.reply(200, b"ok")
        except GatewayRejected as error:
            self.reply(503 if str(error) in {"gateway_busy", "ingest_unavailable", "ingest_capacity",
                                           "ingest_state_invalid", "state_or_transport_invalid"} else
                       413 if str(error) == "payload_size" else
                       400 if str(error) in {"invalid_payload", "ingest_batch"} else 403)
        except Exception:
            self.reply(503)


class RuntimeHTTPServer(HTTPServer):
    allow_reuse_address = True

    def __init__(self, address, gateway, verify_token):
        super().__init__(address, Handler)
        self.gateway, self.verify_token = gateway, verify_token
        self.poll_at, self.poll_ok = 0.0, False


def poll_loop(server, stop, interval=30):
    while not stop.is_set():
        try:
            server.gateway.work()
            server.poll_ok = True
        except Exception:
            server.poll_ok = False
        server.poll_at = time.monotonic()
        stop.wait(interval)


def serve(gateway, verify_token, address=("0.0.0.0", 8080)):
    server = RuntimeHTTPServer(address, gateway, verify_token)
    stop = threading.Event()
    poller = threading.Thread(target=poll_loop, args=(server, stop), daemon=True)
    poller.start()
    previous = {}
    def shutdown(*_):
        stop.set()
        threading.Thread(target=server.shutdown, daemon=True).start()
    try:
        for sig in (signal.SIGTERM, signal.SIGINT):
            previous[sig] = signal.signal(sig, shutdown)
        server.serve_forever(poll_interval=0.5)
    finally:
        stop.set()
        server.server_close()
        poller.join(timeout=TIMEOUT + 1)
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("serve", "health"))
    args = parser.parse_args()
    if args.action == "health":
        import urllib.request
        try:
            with urllib.request.urlopen("http://127.0.0.1:8080/health", timeout=3) as response:
                return 0 if response.status == 200 else 1
        except Exception:
            return 1
    try:
        gateway, token = load_runtime("/run/config/whatsapp.json", "/state", "/run/secrets/github_control_token",
                                      "/run/secrets/meta_token", "/run/secrets/meta_app_secret", "/run/secrets/meta_verify_token")
        serve(gateway, token)
    except Exception:
        print("gateway_start_or_runtime_failure", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
