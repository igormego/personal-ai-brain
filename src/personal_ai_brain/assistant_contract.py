"""Pure, transport-independent Assistant v0.1 ingress contract.

No authentication, persistence, routing, transport or execution lives here.
The caller must establish trusted identity, surface and reply target independently
before normalizing untrusted message fields. Not installed in a live adapter yet.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from typing import Mapping
from uuid import UUID


SCHEMA_VERSION = "0.1"
SURFACES = frozenset({"chatgpt", "whatsapp"})
MAX_MESSAGE_BYTES = 4096
MAX_REPLY_TARGET_LENGTH = 160
MAX_USER_ID_LENGTH = 64
MAX_REQUEST_BYTES = 4608
_ID_PATTERN = re.compile(r"[A-Za-z0-9_.-]+\Z", re.ASCII)
_TARGET_PATTERN = re.compile(r"[A-Za-z0-9_.:-]+\Z", re.ASCII)
_DATE_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})\Z")


class RequestRejected(ValueError):
    """Safe fixed rejection codes; never interpolate caller-supplied values."""


@dataclass(frozen=True, slots=True)
class AssistantRequest:
    schema_version: str
    request_id: str
    user_id: str
    surface: str
    message: str
    created_at: str
    reply_target: str
    payload_hash: str


def normalize_request(
    raw: object,
    *,
    trusted_user_id: str,
    trusted_surface: str,
    trusted_reply_target: str,
    now: datetime | None = None,
) -> AssistantRequest:
    """Validate a request with only user message/ID/timestamp as untrusted input.

    Caller MUST authenticate the user and derive surface/reply target from its
    verified adapter context. Hash is a stable conflict signal, NOT an execution
    ledger. A retry must retain the original request fields and trusted metadata.
    """
    if not isinstance(raw, Mapping) or set(raw) != {"schema_version", "request_id", "message", "created_at"}:
        raise RequestRejected("request_schema")
    if raw["schema_version"] != SCHEMA_VERSION:
        raise RequestRejected("request_version")

    request_id = raw["request_id"]
    if not isinstance(request_id, str):
        raise RequestRejected("request_id")
    try:
        uuid = UUID(request_id)
    except (ValueError, AttributeError):
        raise RequestRejected("request_id") from None
    if str(uuid) != request_id or uuid.version != 4:
        raise RequestRejected("request_id")

    if not isinstance(trusted_user_id, str) or not 1 <= len(trusted_user_id) <= MAX_USER_ID_LENGTH or not _ID_PATTERN.fullmatch(trusted_user_id):
        raise RequestRejected("untrusted_identity")
    if not isinstance(trusted_surface, str) or trusted_surface not in SURFACES:
        raise RequestRejected("untrusted_surface")
    if not isinstance(trusted_reply_target, str) or not 1 <= len(trusted_reply_target) <= MAX_REPLY_TARGET_LENGTH or not _TARGET_PATTERN.fullmatch(trusted_reply_target):
        raise RequestRejected("untrusted_reply_target")

    message = raw["message"]
    if not isinstance(message, str) or not message.strip() or "\x00" in message:
        raise RequestRejected("message")
    try:
        if len(message.encode("utf-8")) > MAX_MESSAGE_BYTES:
            raise RequestRejected("message_size")
    except UnicodeEncodeError:
        raise RequestRejected("message") from None

    created_at = raw["created_at"]
    if not isinstance(created_at, str) or not _DATE_PATTERN.fullmatch(created_at):
        raise RequestRejected("created_at")
    try:
        stamp = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except ValueError:
        raise RequestRejected("created_at") from None
    if stamp.tzinfo is None:
        raise RequestRejected("created_at")
    utc_stamp = stamp.astimezone(timezone.utc)
    reference = now if now is not None else datetime.now(timezone.utc)
    if not isinstance(reference, datetime) or reference.tzinfo is None:
        raise RequestRejected("clock")
    delta = reference.astimezone(timezone.utc) - utc_stamp
    if not -timedelta(minutes=5) <= delta <= timedelta(days=7):
        raise RequestRejected("request_expired_or_future")

    canonical = {
        "schema_version": SCHEMA_VERSION,
        "request_id": request_id,
        "user_id": trusted_user_id,
        "surface": trusted_surface,
        "message": message,
        "created_at": utc_stamp.isoformat(timespec="microseconds"),
        "reply_target": trusted_reply_target,
    }
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if len(encoded) > MAX_REQUEST_BYTES:
        raise RequestRejected("request_size")
    return AssistantRequest(**canonical, payload_hash=hashlib.sha256(encoded).hexdigest())
