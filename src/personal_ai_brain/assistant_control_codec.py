"""Pure ChatGPT <-> existing private PAB control protocol v0.1 codec.

This module NEVER contacts GitHub, validates connector permissions, or sends replies.
A real ChatGPT adapter must authenticate the connection and check that a result
comment is from the authorized transport owner, in the intended private issue.
The Issues transport owns durable request IDs, conflict handling and audit.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Mapping

from .assistant_contract import AssistantRequest
from .assistant_project_os import StatusRejected, validate_selector
from .assistant_result import AssistantResult, MAX_TEXT_BYTES, build_result
from .control import Redactor


TITLE = "pab-control/v0.1"
CAPABILITY = "project_os.status"
_RESULT_PREFIX = "Personal AI Brain control result\n" + chr(96) * 3 + "json\n"
_RESULT_SUFFIX = "\n" + chr(96) * 3
_MAX_COMMENT_BYTES = 20000
_MAX_FIELD_CHARS = 1250


class ControlCodecRejected(ValueError):
    """Safe rejection codes; never include untrusted response text."""


@dataclass(frozen=True, slots=True)
class ControlSubmission:
    title: str
    body: str
    request_id: str


def encode_project_status(request: AssistantRequest, selector: object) -> ControlSubmission:
    """Encode an authenticated, normalized ChatGPT request to the existing worker.

    The actual caller must establish the GitHub identity/private repository
    permission separately; 'requested_by' is *not* authorization.
    """
    if not isinstance(request, AssistantRequest) or request.surface != "chatgpt":
        raise ControlCodecRejected("trusted_chatgpt_request_required")
    try:
        normalized = validate_selector(selector)
    except StatusRejected:
        raise ControlCodecRejected("invalid_project_selector") from None
    wire = {
        "schema_version": "0.1",
        "request_id": request.request_id,
        "capability": CAPABILITY,
        "arguments": normalized,
        "requested_by": "chatgpt_assistant",
        "requested_at": request.created_at,
    }
    body = json.dumps(wire, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return ControlSubmission(TITLE, body, request.request_id)


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    out: dict[str, object] = {}
    for key, value in pairs:
        if key in out:
            raise ControlCodecRejected("result_schema")
        out[key] = value
    return out


def _safe(value: object) -> str:
    if not isinstance(value, str):
        raise ControlCodecRejected("result_schema")
    if len(value) > _MAX_FIELD_CHARS:
        raise ControlCodecRejected("result_size")
    redacted = Redactor().text(value)
    return "".join(" " if ord(c) < 32 or ord(c) == 127 else c for c in redacted)


def decode_project_status(
    request: AssistantRequest,
    selector: object,
    comment_body: str,
    *,
    comment_author_id: int,
    authorized_author_id: int,
) -> AssistantResult:
    """Decode an authenticated transport comment into the existing result shape.

    The connector/caller must obtain author IDs from GitHub metadata, not from
    the comment body, and verify the issue/repository is the requested one.
    Exactly one authoritative owner-produced result must be selected; this
    function does not discover or send Issues.
    """
    if not isinstance(request, AssistantRequest) or request.surface != "chatgpt":
        raise ControlCodecRejected("trusted_chatgpt_request_required")
    if (type(comment_author_id) is not int or type(authorized_author_id) is not int
            or authorized_author_id <= 0 or comment_author_id != authorized_author_id):
        raise ControlCodecRejected("untrusted_result_author")
    try:
        selected = validate_selector(selector)
    except StatusRejected:
        raise ControlCodecRejected("invalid_project_selector") from None
    if not isinstance(comment_body, str):
        raise ControlCodecRejected("result_schema")
    try:
        if len(comment_body.encode("utf-8")) > _MAX_COMMENT_BYTES:
            raise ControlCodecRejected("result_size")
    except UnicodeEncodeError:
        raise ControlCodecRejected("result_schema") from None
    if not comment_body.startswith(_RESULT_PREFIX) or not comment_body.endswith(_RESULT_SUFFIX):
        raise ControlCodecRejected("result_schema")
    raw = comment_body[len(_RESULT_PREFIX):-len(_RESULT_SUFFIX)]
    try:
        envelope = json.loads(raw, object_pairs_hook=_unique,
                              parse_constant=lambda _: (_ for _ in ()).throw(ControlCodecRejected("result_schema")))
    except (ValueError, TypeError, RecursionError):
        raise ControlCodecRejected("result_schema") from None
    if not isinstance(envelope, dict):
        raise ControlCodecRejected("result_schema")
    if (envelope.get("schema_version") != "0.1"
            or envelope.get("request_id") != request.request_id
            or envelope.get("capability") != CAPABILITY):
        raise ControlCodecRejected("result_correlation")
    status = envelope.get("status")
    if status == "request_rejected":
        # Keep the failure fixed; never relay backend error details.
        return build_result(request, status="rejected", freshness="unknown",
                            error_code="request_rejected")
    if status == "capability_execution_failed":
        return build_result(request, status="unavailable", freshness="unknown",
                            error_code="source_unavailable")
    if status not in {"capability_completed", "duplicate"}:
        raise ControlCodecRejected("result_status")
    if status == "duplicate" and envelope.get("original_status") != "capability_completed":
        return build_result(request, status="unavailable", freshness="unknown",
                            error_code="source_unavailable")
    source = envelope.get("result")
    if not isinstance(source, Mapping) or source.get("truncated") is True:
        return build_result(request, status="unavailable", freshness="unknown",
                            error_code="source_unavailable")
    if source.get("project_id") != selected["project_id"] or source.get("workstream_id") != selected.get("workstream_id"):
        raise ControlCodecRejected("result_correlation")
    metadata = source.get("verification")
    if not isinstance(metadata, Mapping) or metadata.get("freshness") not in {"current", "stale", "unknown", "conflicting"}:
        raise ControlCodecRejected("result_schema")

    freshness = metadata["freshness"]
    verified = metadata.get("last_verified")
    if verified is not None:
        verified = _safe(verified)
    sync = _safe(metadata.get("sync_status"))
    title = _safe(source.get("project_name"))
    phase = _safe(source.get("phase"))
    project_status = _safe(source.get("status"))
    gate = _safe(source.get("current_gate"))
    next_action = _safe(source.get("exact_next_action"))
    lines = [
        f"Project: {title} ({selected['project_id']})",
        f"Status: {project_status}; phase: {phase}",
        f"Last verified: {verified or 'unknown'}; freshness: {freshness}; sync: {sync}",
        f"Gate: {gate or 'Not recorded'}",
        f"Next action: {next_action or 'Not recorded'}",
    ]
    codes = metadata.get("finding_codes")
    if not isinstance(codes, list) or len(codes) > 12 or any(
            not isinstance(code, str) or len(code) > 80
            or not code.replace("_", "").isalnum() for code in codes):
        raise ControlCodecRejected("result_schema")
    if codes:
        lines.append("Source findings: " + ", ".join(codes))
    public_text = "\n".join(lines)
    if len(public_text.encode("utf-8")) > MAX_TEXT_BYTES:
        return build_result(request, status="unavailable", freshness="unknown",
                            error_code="source_unavailable")
    return build_result(request, status="completed", freshness=freshness,
                        public_text=public_text)
