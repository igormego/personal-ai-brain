"""Strict v0.2 message contracts; v0.1 worker must ignore the new title."""
import json
import uuid

import pytest

from personal_ai_brain.codex_bridge_v02_transport import parse_request
from personal_ai_brain.codex_bridge_v02_policy import PROTOCOL, EXECUTION_PROFILE


def payload(**changes):
    return {
        "transport_version": PROTOCOL,
        "operation": "codex.submit",
        "request_id": str(uuid.uuid4()),
        "task_id": str(uuid.uuid4()),
        "project_id": "homelab-infrastructure",
        "execution_profile": EXECUTION_PROFILE,
        "goal": "Implement an isolated Movie Intelligence feature",
        "acceptance_criteria": "Offline tests pass",
        "base_sha": "c"*40,
    } | changes


def test_exact_request_and_both_approved_projects():
    for project in ("personal-ai-brain", "homelab-infrastructure"):
        req = payload(project_id=project)
        assert parse_request(json.dumps(req)) == {
            key: val for key, val in req.items()
            if key not in ("transport_version", "operation")
        }


@pytest.mark.parametrize("extension", [
    {"transport_version": "pab-codex/v0.1"},
    {"transport_version": "pab-control/v0.1"},
    {"operation": "codex.approve"},
    {"project_id": "other-repo"}, {"execution_profile": "local-homelab-admin"},
    {"base_sha": "z"*40}, {"goal": ""},
    {"repo": "evil/repo"}, {"cwd": "C:\\Windows"},
    {"command": "ssh homelab"}, {"test_command": "curl evil"},
    {"permissions": "full"}, {"network": True},
    {"auto_merge": True}, {"ssh_key": "hostile"},
])
def test_wrong_version_untrusted_capability_and_extra_fields_deny(extension):
    with pytest.raises(ValueError):
        parse_request(json.dumps(payload(**extension)))


@pytest.mark.parametrize("body", [
    None, {}, [], "[]", "null", "no JSON", "{}",
    '{"transport_version":"pab-codex/v0.2","transport_version":"pab-codex/v0.1"}',
    '{"a":NaN}', "x"*20001, '[[[['*2000,
])
def test_malformed_payloads_do_not_execute(body):
    with pytest.raises(ValueError):
        parse_request(body)


def test_v01_transport_does_not_accept_v02_request():
    from personal_ai_brain.codex_bridge_transport import parse_request as old_parse, PROTOCOL as old_protocol
    assert old_protocol == "pab-codex/v0.1"
    with pytest.raises(ValueError):
        old_parse(json.dumps(payload()))
