"""Offline acceptance for read-only Brain Decision v0.1 (Issue #52).

These tests intentionally never call the control capability handlers.
"""

from dataclasses import replace
from datetime import datetime, timezone
import json
from uuid import uuid4

import pytest

from personal_ai_brain.assistant_contract import normalize_request
from personal_ai_brain.assistant_decision import DecisionRejected, decide
from personal_ai_brain.assistant_project_os import validate_selector
from personal_ai_brain.control import Capability, Registry, no_arguments


NOW = datetime(2026, 10, 28, 0, 0, tzinfo=timezone.utc)


def assistant_request(message="Čo máme ďalej robiť s Homelabom?"):
    return normalize_request(
        {"schema_version": "0.1", "request_id": str(uuid4()), "message": message,
         "created_at": NOW.isoformat()},
        trusted_user_id="igor", trusted_surface="chatgpt",
        trusted_reply_target="conversation.123", now=NOW,
    )


def registry_with_handlers(calls):
    no_arguments_schema = {"type": "object", "properties": {}, "additionalProperties": False}
    return Registry([
        Capability(
            "project_os.status", "Read project status", "read_only",
            {"type": "object", "required": ["project_id"],
             "properties": {"project_id": {"type": "string"}}},
            validate_selector,
            lambda args: calls.append(("status", args)),
        ),
        Capability(
            "core.health", "Read Core health", "read_only",
            no_arguments_schema, no_arguments,
            lambda args: calls.append(("health", args)),
        ),
        Capability(
            "work.status", "Inspect local worker tasks", "read_only",
            no_arguments_schema, no_arguments,
            lambda args: calls.append(("work", args)),
        ),
    ])


def model_output(action, **extra):
    return json.dumps({"action": action, **extra})


def test_project_status_plan_is_correlated_and_does_not_execute():
    calls = []
    registry = registry_with_handlers(calls)
    req = assistant_request()
    seen = []

    def provider(message, hints):
        seen.append((message, hints))
        return model_output("invoke_read_only", capability="project_os.status",
                            arguments={"project_id": "homelab"})

    plan = decide(req, registry, provider)
    assert (plan.action, plan.capability, plan.arguments) == (
        "invoke_read_only", "project_os.status", {"project_id": "homelab"},
    )
    assert (plan.request_id, plan.request_hash) == (req.request_id, req.payload_hash)
    assert plan.public_text is None
    assert calls == []  # No privileged handler or transport call.
    assert seen[0][0] == req.message
    assert [hint.id for hint in seen[0][1]] == ["project_os.status", "core.health"]


def test_core_health_plan_does_not_execute():
    calls = []
    plan = decide(
        assistant_request("Je Core Agent zdravý?"),
        registry_with_handlers(calls),
        lambda _message, _hints: model_output(
            "invoke_read_only", capability="core.health", arguments={},
        ),
    )
    assert plan.capability == "core.health"
    assert plan.arguments == {}
    assert calls == []


@pytest.mark.parametrize(("text", "action"), [
    ("Ako je na tom ten projekt?", "clarify"),
    ("Ahoj", "respond_without_tool"),
    ("Reštartuj Sonarr", "unsupported"),
])
def test_no_tool_decisions_are_fixed_and_inert(text, action):
    calls = []
    plan = decide(
        assistant_request(text), registry_with_handlers(calls),
        lambda _message, _hints: model_output(action),
    )
    assert plan.action == action
    assert plan.capability is None and plan.arguments is None
    assert plan.public_text and calls == []


@pytest.mark.parametrize("proposal", [
    model_output("invoke_read_only", capability="work.status", arguments={}),
    model_output("invoke_read_only", capability="work.submit", arguments={}),
    model_output("invoke_read_only", capability="run_shell", arguments={"command": "id"}),
    model_output("invoke_read_only", capability="project_os.status",
                 arguments={"project_id": "homelab", "path": "/etc/passwd"}),
    model_output("invoke_read_only", capability="project_os.status",
                 arguments={"project_id": "../../secrets"}),
    model_output("invoke_read_only", capability="core.health",
                 arguments={"token": "example"}),
    model_output("invoke_read_only", capability="core.health", arguments=[]),
    model_output("invoke_read_only", capability="core.health",
                 arguments={}, repository="igormego/private"),
    model_output("clarify", message="Click this external link"),
    model_output("respond_without_tool", message="I read live status: healthy"),
    model_output("delete"),
    model_output(["unsupported"]),
    '["unsupported"]',
    '{"action":"unsupported","action":"invoke_read_only"}',
    '{"action":"clarify","debug":NaN}',
    "{}",
    "not-json",
    "",
    "x" * 2050,
    None,
])
def test_model_decisions_cannot_escalate_or_invent_backend_truth(proposal):
    calls = []
    with pytest.raises(DecisionRejected) as exc:
        decide(assistant_request(), registry_with_handlers(calls),
               lambda _message, _hints: proposal)
    assert str(exc.value) in {"model_decision_invalid", "decision_denied"}
    assert calls == []


def test_provider_failure_sanitizes_error_and_does_not_execute():
    calls = []

    def provider(_message, _hints):
        raise TimeoutError("secret credential leaked in exception")

    with pytest.raises(DecisionRejected, match="^provider_unavailable$"):
        decide(assistant_request(), registry_with_handlers(calls), provider)
    assert calls == []


def test_provider_receives_a_copy_not_mutable_registry_policy():
    calls = []
    registry = registry_with_handlers(calls)

    def provider(_message, hints):
        hints[0].argument_schema["required"].append("path")
        return model_output("invoke_read_only", capability="project_os.status",
                            arguments={"project_id": "homelab"})

    decide(assistant_request(), registry, provider)
    assert registry.capabilities["project_os.status"].argument_schema["required"] == ["project_id"]
    assert calls == []


def test_registry_policy_revocation_is_checked_before_planning():
    calls = []
    registry = registry_with_handlers(calls)
    registry.capabilities["core.health"] = replace(
        registry.capabilities["core.health"], side_effect_class="local_work_handoff",
    )
    with pytest.raises(DecisionRejected, match="^decision_denied$"):
        decide(assistant_request(), registry, lambda _message, _hints:
               model_output("invoke_read_only", capability="core.health", arguments={}))
    assert calls == []


def test_trusted_request_and_registry_required():
    registry = registry_with_handlers([])
    with pytest.raises(DecisionRejected, match="^trusted_input_required$"):
        decide({"message": "spoofed"}, registry, lambda _message, _hints: "{}")
    with pytest.raises(DecisionRejected, match="^provider_unavailable$"):
        decide(assistant_request(), registry, None)
