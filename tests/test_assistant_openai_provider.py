"""Offline contract tests for one-shot Responses decision adapter.

The tests use fake network bytes only. No paid API calls or stored secrets.
"""

from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest

from personal_ai_brain.assistant_contract import normalize_request
from personal_ai_brain.assistant_decision import DecisionRejected, decide
from personal_ai_brain.assistant_decision_context import build_decision_context
from personal_ai_brain.assistant_openai_provider import (
    MODEL, ENDPOINT, MAX_INPUT_BYTES, MAX_OUTPUT_TOKENS, ModelUnavailable,
    OpenAIDecisionProvider, _request, parse_response, read_api_key,
)
from personal_ai_brain.assistant_project_os import validate_selector
from personal_ai_brain.control import Capability, Registry, no_arguments
from personal_ai_brain.core_agent import CoreSnapshot, StateRecord


TODAY = date(2026, 10, 29)


def context():
    data = CoreSnapshot(generated_on=TODAY.isoformat(), records=[
        StateRecord(
            project_id=project_id, project_name=label, scope=scope,
            repository="secret/never-send", path=path,
            attention="active", status="active", phase="build",
            last_verified=TODAY.isoformat(), sync_status="synced",
            current_gate="private-not-for-model", exact_next_action="private",
        )
        for project_id, label, scope, path in (
            ("homelab", "Homelab", "project", ".project/state.yaml"),
            ("homelab", "Media Automation", "workstream", ".project/workstreams/media-automation.yaml"),
            ("recipe-intelligence", "Recipe Intelligence", "project", ".project/state.yaml"),
        )
    ])
    return build_decision_context(data, today=TODAY)


def request(message="Čo ďalej s Homelabom?"):
    now = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)
    return normalize_request(
        {"schema_version": "0.1", "request_id": str(uuid4()),
         "message": message, "created_at": now.isoformat()},
        trusted_user_id="igor", trusted_surface="chatgpt",
        trusted_reply_target="conversation.123", now=now,
    )


def registry(calls):
    return Registry([
        Capability("project_os.status", "read status", "read_only", {},
                   validate_selector, lambda args: calls.append(args)),
        Capability("core.health", "core health", "read_only", {},
                   no_arguments, lambda args: calls.append(args)),
    ])


def response(action="invoke_read_only", capability="project_os.status",
             project_id="homelab", workstream_id=None, *, status="completed",
             usage=None, content_type="output_text"):
    decision = {"action": action, "capability": capability,
                "project_id": project_id, "workstream_id": workstream_id}
    return json.dumps({
        "status": status,
        "output": [{"type": "message", "status": "completed",
                    "content": [{"type": content_type, "text": json.dumps(decision)}]}],
        "usage": usage if usage is not None else {"input_tokens": 450, "output_tokens": 46},
    }).encode()


def key_file(tmp_path):
    path = tmp_path / "api-key"
    path.write_text("sk-test-fixture-do-not-use\n")
    path.chmod(0o600)
    return path


def test_request_is_strict_compact_private_and_only_allows_model_selection():
    calls = []
    hints = __import__("personal_ai_brain.assistant_decision", fromlist=["_catalog"])._catalog(
        registry(calls))
    raw = _request("Čo máme ďalej robiť s Homelabom?", hints, context())
    payload = json.loads(raw)
    assert payload["model"] == MODEL == "gpt-6-luna"
    assert ENDPOINT.startswith("https://api.openai.com/")
    assert payload["store"] is False
    assert payload["stream"] is False
    assert payload["reasoning"]["effort"] == "none"
    assert payload["max_output_tokens"] == MAX_OUTPUT_TOKENS
    assert payload["text"]["format"]["strict"] is True
    schema = payload["text"]["format"]["schema"]
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])
    assert len(raw) <= MAX_INPUT_BYTES
    user_payload = json.loads(payload["input"][0]["content"])
    assert len(user_payload["project_catalog"]) == 3
    assert "media-automation" in str(user_payload)
    assert "secret/never-send" not in raw.decode()
    assert "private-not-for-model" not in raw.decode()
    assert "private" not in json.dumps(user_payload)
    assert "api-key" not in raw.decode()
    assert payload.get("tools") is None
    assert calls == []


def test_one_paid_attempt_is_explicit_and_planner_remains_policy_authority(monkeypatch, tmp_path):
    key = key_file(tmp_path)
    called = []
    def fake_http(raw, secret):
        called.append((json.loads(raw), secret))
        return response(workstream_id="media-automation")
    import personal_ai_brain.assistant_openai_provider as module
    monkeypatch.setattr(module, "_post", fake_http)
    provider = OpenAIDecisionProvider(key, allow_paid_call=True)
    calls = []
    plan = decide(request(), registry(calls), provider, context=context())
    assert plan.action == "invoke_read_only"
    assert plan.arguments == {"project_id": "homelab", "workstream_id": "media-automation"}
    assert provider.usage.input_tokens == 450 and provider.usage.output_tokens == 46
    assert len(called) == 1
    assert called[0][1].startswith("sk-test")
    assert calls == []
    with pytest.raises(DecisionRejected, match="provider_unavailable"):
        decide(request(), registry(calls), provider, context=context())
    assert len(called) == 1


def test_no_paid_call_without_explicit_opt_in(monkeypatch, tmp_path):
    import personal_ai_brain.assistant_openai_provider as module
    monkeypatch.setattr(module, "_post", lambda *_args: pytest.fail("network"))
    with pytest.raises(ModelUnavailable, match="model_call_not_authorized"):
        OpenAIDecisionProvider(key_file(tmp_path))("ahoj", tuple(), context())


@pytest.mark.parametrize(("action", "cap", "project", "ws", "expected"), [
    ("invoke_read_only", "project_os.status", "homelab", None, "invoke_read_only"),
    ("invoke_read_only", "core.health", None, None, "invoke_read_only"),
    ("clarify", None, None, None, "clarify"),
    ("respond_without_tool", None, None, None, "respond_without_tool"),
    ("unsupported", None, None, None, "unsupported"),
])
def test_structured_response_normalization(action, cap, project, ws, expected):
    value, usage = parse_response(response(action, cap, project, ws))
    assert json.loads(value)["action"] == expected
    assert usage.input_tokens == 450
    if cap == "core.health":
        assert json.loads(value)["arguments"] == {}


@pytest.mark.parametrize("raw", [
    response(status="incomplete"),
    response(content_type="refusal"),
    response("invoke_read_only", "project_os.status", None),
    response("invoke_read_only", "project_os.status", "homelab", 42),
    response("invoke_read_only", "core.health", "homelab"),
    response("unsupported", "core.health", None),
    response("clarify", None, "homelab"),
    response(usage={"input_tokens": True, "output_tokens": 42}),
    response(usage={"input_tokens": 44}),
    b"garbage",
    b"{}",
    b'{"status":"completed","status":"completed"}',
    b"x" * 16400,
])
def test_incomplete_refusal_invalid_format_and_usage_fail_closed(raw):
    with pytest.raises(ModelUnavailable, match="model_response_invalid"):
        parse_response(raw)


def test_hallucinated_selector_is_rejected_even_if_api_returned_valid_json(monkeypatch, tmp_path):
    import personal_ai_brain.assistant_openai_provider as module
    monkeypatch.setattr(module, "_post", lambda *_: response(project_id="nonexistent"))
    calls = []
    provider = OpenAIDecisionProvider(key_file(tmp_path), allow_paid_call=True)
    with pytest.raises(DecisionRejected, match="decision_denied"):
        decide(request(), registry(calls), provider, context=context())
    assert calls == []


def test_private_key_validation_and_missing_file_are_safe(tmp_path):
    file = key_file(tmp_path)
    assert read_api_key(file).startswith("sk-test")
    file.chmod(0o644)
    with pytest.raises(ModelUnavailable, match="model_auth_unavailable"):
        read_api_key(file)
    file.unlink()
    with pytest.raises(ModelUnavailable, match="model_auth_unavailable"):
        read_api_key(file)
    file = key_file(tmp_path)
    link = tmp_path / "symlink"
    link.symlink_to(file)
    with pytest.raises(ModelUnavailable, match="model_auth_unavailable"):
        read_api_key(link)


def test_no_retry_after_network_timeout(monkeypatch, tmp_path):
    import personal_ai_brain.assistant_openai_provider as module
    attempts = []
    def timeout(*_args):
        attempts.append(True)
        raise ModelUnavailable("model_timeout")
    monkeypatch.setattr(module, "_post", timeout)
    provider = OpenAIDecisionProvider(key_file(tmp_path), allow_paid_call=True)
    with pytest.raises(ModelUnavailable, match="model_timeout"):
        provider("Ahoj", tuple(), None)
    with pytest.raises(ModelUnavailable, match="model_call_not_authorized"):
        provider("Ahoj", tuple(), None)
    assert len(attempts) == 1
