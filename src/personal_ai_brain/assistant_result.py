"""Read-only Assistant v0.1 result envelope, independent of both chat surfaces.

This is a pure contract: it neither authorizes capability execution nor delivers
messages. The eventual capability must supply explicitly allowlisted public text;
never pass raw exceptions, logs, secrets, or arbitrary backend output.
"""

from __future__ import annotations

from dataclasses import dataclass

from .assistant_contract import AssistantRequest, SCHEMA_VERSION


CAPABILITY = "project_os.status"
FRESHNESS = frozenset({"current", "stale", "unknown", "conflicting"})
MAX_TEXT_BYTES = 3072
ERROR_MESSAGES = {
    "source_unavailable": "The project state could not be retrieved.",
    "source_conflict": "The available project states conflict; no current answer is confirmed.",
    "request_rejected": "This request cannot be processed.",
    "execution_failed": "The project status lookup did not complete.",
}


class ResultRejected(ValueError):
    """Fixed rejection reasons; never include untrusted result contents."""


@dataclass(frozen=True, slots=True)
class AssistantResult:
    schema_version: str
    request_id: str
    request_hash: str
    user_id: str
    surface: str
    reply_target: str
    capability: str
    status: str
    freshness: str
    text: str
    error_code: str | None


def build_result(
    request: AssistantRequest,
    *,
    status: str,
    freshness: str,
    public_text: str | None = None,
    error_code: str | None = None,
) -> AssistantResult:
    """Correlate a bounded read-only result with a previously normalized request.

    A trusted Project OS capability owns classification of source freshness and
    sanitization of successful public text; this function cannot prove either.
    A surface adapter must authenticate outbound delivery and check durable
    request/delivery truth before emitting this result.
    """
    if not isinstance(request, AssistantRequest):
        raise ResultRejected("request_required")
    if request.schema_version != SCHEMA_VERSION:
        raise ResultRejected("request_version")
    if not isinstance(freshness, str) or freshness not in FRESHNESS:
        raise ResultRejected("freshness")

    if status == "completed":
        if error_code is not None:
            raise ResultRejected("result_schema")
        if not isinstance(public_text, str) or not public_text.strip():
            raise ResultRejected("result_text")
        if any(ord(char) < 32 and char not in "\n\t" for char in public_text):
            raise ResultRejected("result_text")
        try:
            if len(public_text.encode("utf-8")) > MAX_TEXT_BYTES:
                raise ResultRejected("result_size")
        except UnicodeEncodeError:
            raise ResultRejected("result_text") from None
        text = public_text
        safe_error = None
    elif status in {"unavailable", "rejected", "failed"}:
        # Only fixed public messages can accompany failures; suppress raw details.
        if public_text is not None or not isinstance(error_code, str) or error_code not in ERROR_MESSAGES:
            raise ResultRejected("result_schema")
        allowed = {
            "unavailable": {"source_unavailable", "source_conflict"},
            "rejected": {"request_rejected"},
            "failed": {"execution_failed"},
        }
        if error_code not in allowed[status] or freshness not in {"unknown", "conflicting"}:
            raise ResultRejected("result_schema")
        text = ERROR_MESSAGES[error_code]
        safe_error = error_code
    else:
        raise ResultRejected("result_status")

    return AssistantResult(
        schema_version=SCHEMA_VERSION,
        request_id=request.request_id,
        request_hash=request.payload_hash,
        user_id=request.user_id,
        surface=request.surface,
        reply_target=request.reply_target,
        capability=CAPABILITY,
        status=status,
        freshness=freshness,
        text=text,
        error_code=safe_error,
    )
