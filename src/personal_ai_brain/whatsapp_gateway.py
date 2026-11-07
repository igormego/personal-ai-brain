"""Source-only Meta WhatsApp adapter; clients and private state are operator supplied.

The backend client is an authenticated connection to one operator-configured private
control repository. It implements create_issue(title, body), get_issue(number), and
list_comments(number); returned identities must come from provider metadata. The
Meta client implements send_text(recipient, text). No server or network is started.
A single host owns the private state directory. Uncertain external effects are never
retried automatically, including after a process crash.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
from uuid import uuid4

from .assistant_contract import AssistantRequest, normalize_request, RequestRejected
from .assistant_control_codec import encode_project_status, decode_project_status, TITLE

MAX_WEBHOOK_BYTES = 65536
SELECTOR = {"project_id": "homelab"}
_ID = re.compile(r"[A-Za-z0-9_.=+/\-]{1,160}\Z", re.ASCII)
_PHONE = re.compile(r"[0-9]{1,32}\Z", re.ASCII)


class GatewayRejected(ValueError):
    """Fixed safe error codes, never include external payloads or secrets."""


def verify_challenge(query, verify_token):
    if (not isinstance(verify_token, str) or not verify_token
            or not isinstance(query, dict) or query.get("hub.mode") != "subscribe"
            or not isinstance(query.get("hub.verify_token"), str)
            or not hmac.compare_digest(query["hub.verify_token"].encode(), verify_token.encode())
            or not isinstance(query.get("hub.challenge"), str)
            or len(query["hub.challenge"]) > 1024):
        raise GatewayRejected("verification_failed")
    return query["hub.challenge"]


def _unique(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise GatewayRejected("invalid_payload")
        out[key] = value
    return out


def authenticated_events(raw, signature, secret, *, waba_id, phone_id, recipient, sender):
    """Verify exact raw bytes before parsing, and authenticate both endpoints."""
    if not isinstance(raw, bytes) or len(raw) > MAX_WEBHOOK_BYTES:
        raise GatewayRejected("payload_size")
    if (not isinstance(secret, bytes) or not secret or not isinstance(signature, str)
            or not re.fullmatch(r"sha256=[0-9a-f]{64}", signature, re.ASCII)):
        raise GatewayRejected("invalid_signature")
    expected = "sha256=" + hmac.new(secret, raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise GatewayRejected("invalid_signature")
    if any(not isinstance(x, str) or not _PHONE.fullmatch(x)
           for x in (waba_id, phone_id, sender)) or (not isinstance(recipient, str)
            or not re.fullmatch(r"[0-9+ ()-]{1,40}", recipient, re.ASCII)):
        raise GatewayRejected("invalid_configuration")
    try:
        data = json.loads(raw, object_pairs_hook=_unique,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        if not isinstance(data, dict) or data.get("object") != "whatsapp_business_account":
            raise ValueError()
        entries = data["entry"]
        if not isinstance(entries, list) or len(entries) > 100:
            raise ValueError()
        events = []
        for entry in entries:
            if not isinstance(entry, dict) or entry.get("id") != waba_id:
                raise GatewayRejected("untrusted_recipient")
            changes = entry["changes"]
            if not isinstance(changes, list) or len(changes) > 100:
                raise ValueError()
            for change in changes:
                if change.get("field") != "messages":
                    continue
                value = change["value"]
                meta = value["metadata"]
                if meta.get("phone_number_id") != phone_id or meta.get("display_phone_number") != recipient:
                    raise GatewayRejected("untrusted_recipient")
                messages = value.get("messages", [])
                if not isinstance(messages, list) or len(messages) > 100:
                    raise ValueError()
                for message in messages:
                    if message.get("from") != sender:
                        raise GatewayRejected("untrusted_sender")
                    if message.get("type") != "text" or not isinstance(message.get("text"), dict):
                        raise GatewayRejected("unsupported_message")
                    msg_id, stamp, body = message.get("id"), message.get("timestamp"), message["text"].get("body")
                    if not isinstance(msg_id, str) or not _ID.fullmatch(msg_id):
                        raise ValueError()
                    if not isinstance(stamp, str) or not re.fullmatch(r"[0-9]{1,12}", stamp, re.ASCII):
                        raise ValueError()
                    created = datetime.fromtimestamp(int(stamp), timezone.utc).isoformat()
                    events.append((msg_id, created, body))
        return events
    except GatewayRejected:
        raise
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError, OSError, RecursionError, UnicodeDecodeError):
        raise GatewayRejected("invalid_payload") from None


@contextmanager
def _lock(root):
    """An OS-held exclusive lock is released by the OS on process death."""
    with (root / "gateway.lock").open("a+b") as stream:
        if os.name == "nt":
            import msvcrt
            stream.seek(0)
            if not stream.read(1):
                stream.write(b"0"); stream.flush()
            stream.seek(0)
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                raise GatewayRejected("gateway_busy") from None
            try:
                yield
            finally:
                stream.seek(0); msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise GatewayRejected("gateway_busy") from None
            try:
                yield
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)


def _save(path, record):
    temp = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(record, stream, sort_keys=True, separators=(",", ":"))
            stream.flush(); os.fsync(stream.fileno())
        os.replace(temp, path)
        if os.name != "nt":
            fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    finally:
        temp.unlink(missing_ok=True)


class Gateway:
    def __init__(self, *, state_dir, backend, meta, repository, owner_id, user_id,
                 waba_id, phone_id, recipient, sender, app_secret):
        if (not isinstance(repository, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository)
                or type(owner_id) is not int or owner_id <= 0):
            raise GatewayRejected("invalid_configuration")
        self.root = Path(state_dir)
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.backend, self.meta, self.repository, self.owner_id = backend, meta, repository, owner_id
        self.user_id, self.waba_id, self.phone_id = user_id, waba_id, phone_id
        self.recipient, self.sender, self.app_secret = recipient, sender, app_secret
        self.binding = hashlib.sha256(json.dumps([repository, owner_id, user_id, waba_id, phone_id,
                                                  recipient, sender], separators=(",", ":")).encode()).hexdigest()

    def post(self, raw, signature, *, now=None):
        events = authenticated_events(raw, signature, self.app_secret, waba_id=self.waba_id,
                                      phone_id=self.phone_id, recipient=self.recipient, sender=self.sender)
        # Validate the whole batch before causing any external effects.
        for _, created, body in events:
            try:
                normalize_request({"schema_version": "0.1", "request_id": str(uuid4()),
                                   "message": body, "created_at": created}, trusted_user_id=self.user_id,
                                  trusted_surface="whatsapp", trusted_reply_target=self.sender, now=now)
            except RequestRejected:
                raise GatewayRejected("invalid_message") from None
        with _lock(self.root):
            return [self._process(mid, created, body, now) for mid, created, body in events]

    def _process(self, message_id, created, body, now):
        key = hashlib.sha256(message_id.encode("ascii")).hexdigest()
        path = self.root / (key + ".json")
        try:
            record = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
            request_id = record["request_id"] if record else str(uuid4())
            req = normalize_request({"schema_version": "0.1", "request_id": request_id,
                                     "message": body, "created_at": created},
                                    trusted_user_id=self.user_id, trusted_surface="whatsapp",
                                    trusted_reply_target=self.sender, now=now)
            if record and record["request_hash"] != req.payload_hash:
                raise GatewayRejected("message_conflict")
            if not record:
                record = {"request_id": req.request_id, "request_hash": req.payload_hash,
                          "control": "submitting", "delivery": "pending",
                          "created_at": req.created_at, "binding": self.binding}
                _save(path, record)  # reserve before creating an external issue
                submission = encode_project_status(req, SELECTOR)
                try:
                    number = self.backend.create_issue(submission.title, submission.body)
                except Exception:
                    record["control"] = "uncertain"; _save(path, record)
                    return "control_uncertain"
                if type(number) is not int or number < 1:
                    record["control"] = "uncertain"; _save(path, record)
                    return "control_uncertain"
                record.update(control="submitted", issue=number); _save(path, record)
            return self._advance(path, record, req)
        except GatewayRejected:
            raise
        except (KeyError, TypeError, ValueError, OSError):
            raise GatewayRejected("state_or_transport_invalid") from None

    def poll(self):
        """Resume submitted requests without storing or replaying private message text."""
        with _lock(self.root):
            results = []
            for path in sorted(self.root.glob("*.json")):
                if not re.fullmatch(r"[0-9a-f]{64}\.json", path.name):
                    raise GatewayRejected("state_or_transport_invalid")
                try:
                    record = json.loads(path.read_text(encoding="utf-8"))
                    if record["binding"] != self.binding:
                        raise GatewayRejected("state_binding_mismatch")
                    req = AssistantRequest("0.1", record["request_id"], self.user_id, "whatsapp", "",
                                           record["created_at"], self.sender, record["request_hash"])
                    results.append(self._advance(path, record, req))
                except GatewayRejected:
                    raise
                except (KeyError, TypeError, ValueError, OSError):
                    raise GatewayRejected("state_or_transport_invalid") from None
            return results

    def _advance(self, path, record, req):
        if record.get("binding") != self.binding:
            raise GatewayRejected("state_binding_mismatch")
        try:
            if record["control"] in {"submitting", "uncertain"}:
                return "control_uncertain"
            if record["delivery"] in {"sending", "uncertain"}:
                return "delivery_uncertain"
            if record["delivery"] in {"sent", "failed"}:
                return record["delivery"]
            number = record["issue"]
            try:
                issue = self.backend.get_issue(number)
                comments = self.backend.list_comments(number)
            except Exception:
                raise GatewayRejected("control_unavailable") from None
            if (not isinstance(issue, dict) or issue.get("number") != number
                    or issue.get("repository") != self.repository or issue.get("private") is not True
                    or issue.get("title") != TITLE or issue.get("author_id") != self.owner_id
                    or issue.get("body") != encode_project_status(req, SELECTOR).body
                    or issue.get("is_pull_request") is not False):
                raise GatewayRejected("untrusted_issue")
            if not isinstance(comments, list):
                raise GatewayRejected("invalid_result")
            owner_comments = [c for c in comments if isinstance(c, dict) and c.get("author_id") == self.owner_id
                              and isinstance(c.get("body"), str)
                              and c["body"].startswith("Personal AI Brain control result\n")]
            if not owner_comments:
                return "pending"
            if len(owner_comments) != 1:
                raise GatewayRejected("ambiguous_result")
            result = decode_project_status(req, SELECTOR, owner_comments[0]["body"],
                                           comment_author_id=owner_comments[0]["author_id"],
                                           authorized_author_id=self.owner_id)
            record["delivery"] = "sending"; _save(path, record)  # reserve before sending
            try:
                receipt = self.meta.send_text(self.sender, result.text)
            except Exception:
                record["delivery"] = "uncertain"; _save(path, record)
                return "delivery_uncertain"
            if not isinstance(receipt, str) or not _ID.fullmatch(receipt):
                record["delivery"] = "uncertain"; _save(path, record)
                return "delivery_uncertain"
            record.update(delivery="sent", provider_message_id=receipt); _save(path, record)
            return "sent"
        except GatewayRejected:
            raise
        except (KeyError, TypeError, ValueError, OSError):
            raise GatewayRejected("state_or_transport_invalid") from None


class MetaCloudClient:
    """Build one constrained Cloud API call through an explicitly supplied HTTP client.

    http_post(url, headers, json_body) returns parsed JSON or raises. The caller
    provides protected credentials and the HTTP client; no default network exists.
    """
    def __init__(self, *, phone_id, access_token, api_version, http_post):
        if (not isinstance(phone_id, str) or not _PHONE.fullmatch(phone_id)
                or not isinstance(api_version, str) or not re.fullmatch(r"v[0-9]{1,3}\.[0-9]{1,3}", api_version)
                or not isinstance(access_token, str) or not access_token or not callable(http_post)):
            raise GatewayRejected("invalid_configuration")
        self.phone_id, self._token, self.version, self._post = phone_id, access_token, api_version, http_post

    def send_text(self, recipient, text):
        if (not isinstance(recipient, str) or not _PHONE.fullmatch(recipient)
                or not isinstance(text, str) or not text.strip() or len(text.encode("utf-8")) > 3072):
            raise GatewayRejected("invalid_delivery")
        response = self._post(f"https://graph.facebook.com/{self.version}/{self.phone_id}/messages",
                              {"Authorization": "Bearer " + self._token},
                              {"messaging_product": "whatsapp", "to": recipient,
                               "type": "text", "text": {"body": text}})
        try:
            ids = response["messages"]
            if not isinstance(ids, list) or len(ids) != 1:
                raise ValueError()
            return ids[0]["id"]
        except (KeyError, IndexError, TypeError, ValueError):
            raise GatewayRejected("invalid_delivery_receipt") from None

class PrivateControlIssues:
    """Fixed private Issues transport using an operator-authenticated API callable.

    api(method, path, payload) must obtain data from GitHub's authenticated API,
    reject redirects and raise on HTTP/JSON errors. Its credential must already
    have the narrowly scoped Issues permissions; this adapter grants none.
    """
    def __init__(self, api):
        if not callable(api):
            raise GatewayRejected("invalid_configuration")
        self._api = api
        from .github_issues import REPOSITORY
        self.repository = REPOSITORY
        self._base = "/repos/" + REPOSITORY

    def _private(self):
        repo = self._api("GET", self._base, None)
        if (not isinstance(repo, dict) or repo.get("private") is not True
                or repo.get("full_name") != self.repository or repo.get("has_issues") is not True):
            raise GatewayRejected("untrusted_repository")

    def create_issue(self, title, body):
        self._private()
        if title != TITLE or not isinstance(body, str) or len(body.encode()) > 8192:
            raise GatewayRejected("invalid_submission")
        result = self._api("POST", self._base + "/issues", {"title": title, "body": body})
        return result["number"]

    def get_issue(self, number):
        self._private()
        if type(number) is not int or number < 1:
            raise GatewayRejected("invalid_issue")
        issue = self._api("GET", self._base + f"/issues/{number}", None)
        return {"number": issue.get("number"), "repository": self.repository, "private": True,
                "title": issue.get("title"), "body": issue.get("body"),
                "author_id": (issue.get("user") or {}).get("id"),
                "is_pull_request": "pull_request" in issue}

    def list_comments(self, number):
        self._private()
        if type(number) is not int or number < 1:
            raise GatewayRejected("invalid_issue")
        comments = self._api("GET", self._base + f"/issues/{number}/comments?per_page=100", None)
        if not isinstance(comments, list) or len(comments) >= 100:
            raise GatewayRejected("invalid_result")
        return [{"author_id": (c.get("user") or {}).get("id"), "body": c.get("body")}
                for c in comments if isinstance(c, dict)]
