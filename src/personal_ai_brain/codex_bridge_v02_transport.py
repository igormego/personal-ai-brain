"""Pure v0.2 outbound Issues contract; never installs a worker or runs Codex.

The existing v0.1 transport continues to require title pab-codex/v0.1.
The separate v0.2 title cannot be mistaken for v0.1, and caller text
cannot supply filesystem, git or privilege arguments.
"""

import json

from .codex_bridge_v02_policy import PROTOCOL, MAX_BODY, validate_request


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def parse_request(body: object) -> dict:
    if type(body) is not str:
        raise ValueError("invalid_body")
    try:
        if len(body.encode("utf-8")) > MAX_BODY:
            raise ValueError("invalid_body")
        envelope = json.loads(
            body, object_pairs_hook=_unique,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("invalid_number")),
        )
    except (ValueError, UnicodeError, RecursionError, TypeError):
        raise ValueError("invalid_body") from None

    if (type(envelope) is not dict or
            set(envelope) != {
                "transport_version", "operation", "request_id", "task_id",
                "project_id", "execution_profile", "goal",
                "acceptance_criteria", "base_sha",
            } or envelope["transport_version"] != PROTOCOL or
            envelope["operation"] != "codex.submit"):
        raise ValueError("invalid_protocol")
    return validate_request({
        key: value for key, value in envelope.items()
        if key not in {"transport_version", "operation"}
    })
