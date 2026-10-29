"""Offline acceptance of genuine conversational Responses tool loop (Brain v0.5).

Fake provider results and trusted synthetic Core snapshots. No real key, real
Project OS read, Gateway, deployment or paid network call in CI.
"""

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
import json
from uuid import uuid4

import pytest

from personal_ai_brain.assistant_contract import normalize_request
from personal_ai_brain.assistant_conversation import (
    ChatMessage, ConversationRejected, ModelStep, ModelToolCall,
    MAX_TOOL_CALLS, run_conversation,
)
from personal_ai_brain.assistant_decision_context import build_decision_context
from personal_ai_brain.assistant_openai_conversation import (
    MAX_OUTPUT_TOKENS, MAX_PROVIDER_REQUESTS, OpenAIConversationProvider,
    _build_request, _parse_step,
)
from personal_ai_brain.assistant_openai_provider import ModelUnavailable
from personal_ai_brain.assistant_project_os import answer_project_status, validate_selector
from personal_ai_brain.assistant_result import build_result
from personal_ai_brain.control import Capability, Registry
from personal_ai_brain.core_agent import CoreSnapshot, StateRecord


TODAY = date.today()


def request(message="Čo ďalej s Homelabom?"):
    now = datetime.now(timezone.utc)
    return normalize_request(
        {"schema_version": "0.1", "request_id": str(uuid4()),
         "message": message, "created_at": now.isoformat()},
        trusted_user_id="igor", trusted_surface="chatgpt",
        trusted_reply_target="conversation.123", now=now,
    )


def snapshot(*, stale=False):
    verified = TODAY - timedelta(days=15) if stale else TODAY
    return CoreSnapshot(generated_on=TODAY.isoformat(), records=[
        StateRecord(
            project_id=pid, project_name=name, scope="project",
            repository="fixture/not-for-model", path=".project/state.yaml",
            attention="active", status="active", phase="test-phase",
            last_verified=verified.isoformat(), sync_status="synced",
            current_gate="Review source evidence",
            exact_next_action=f"Verify {pid} milestone",
        )
        for pid, name in (
            ("homelab", "Homelab & Media Stack"),
            ("recipe-intelligence", "Recipe Intelligence"),
        )
    ])


def registry(calls):
    return Registry([Capability(
        "project_os.status", "recorded project status", "read_only", {},
        validate_selector, lambda args: calls.append(args),
    )])


def fake_reader(req, source, calls):
    def read(request_, selector):
        calls.append(selector)
        return answer_project_status(request_, source, selector, today=TODAY)
    return read


def call(cid="call_001", project="homelab", workstream=None):
    args = {"project_id": project, "workstream_id": workstream}
    return ModelToolCall(cid, "project_os_status", args)


def toolstep(*calls):
    return ModelStep(
        None, tuple(calls),
        tuple({
            "type": "function_call", "call_id": c.call_id,
            "name": c.name,
            "arguments": json.dumps(c.arguments),
        } for c in calls),
    )


def final(text="Podľa Project OS by som najprv preveril ďalší míľnik."):
    return ModelStep(text, (), ())


def scripted(*steps):
    queue = list(steps)
    captured = []
    def provider(items, context):
        captured.append((items, context))
        assert queue, "extra unexpected model call"
        return queue.pop(0)
    provider.captured = captured
    return provider


def response(*, calls=(), answer=None, status="completed", usage=None):
    output = []
    for c in calls:
        output.append({
            "type": "function_call", "status": "completed",
            "name": c.name, "call_id": c.call_id,
            "arguments": json.dumps(c.arguments),
        })
    if answer is not None:
        output.append({
            "type": "message", "role": "assistant", "status": "completed",
            "content": [{"type": "output_text", "text": answer}],
        })
    return json.dumps({
        "status": status, "output": output,
        "usage": usage if usage is not None else {
            "input_tokens": 500, "output_tokens": 40,
        },
    }).encode()


def key_file(tmp_path):
    path = tmp_path / "api-key"
    path.write_text("sk-test-fixture-not-real\n")
    path.chmod(0o600)
    return path


def test_original_slovenian_request_gets_a_freeform_grounded_answer():
    req = request()
    registry_calls = []
    reads = []
    provider = scripted(
        toolstep(call()),
        final("Podľa overeného stavu je ďalším krokom overenie Homelabu. "
              "Ja by som najprv skontroloval dostupnosť médií, potom monitoring."),
    )
    answer = run_conversation(
        req, registry(registry_calls), build_decision_context(snapshot(), today=TODAY),
        provider, fake_reader(req, snapshot(), reads),
    )
    assert answer.request_hash == req.payload_hash
    assert answer.surface == "chatgpt" and answer.reply_target == req.reply_target
    assert answer.model_steps == 2 and answer.tool_calls == 1
    assert "Ja by som" in answer.text  # Not deterministically templated
    assert "Podklady (Project OS" in answer.text
    assert "homelab: completed, current" in answer.text
    assert answer.sources[0].project_id == "homelab"
    assert answer.sources[0].freshness == "current"
    assert answer.history[-2:] == (
        ChatMessage("user", req.message),
        ChatMessage("assistant", "Podľa overeného stavu je ďalším krokom overenie Homelabu. "
                    "Ja by som najprv skontroloval dostupnosť médií, potom monitoring."),
    )
    assert registry_calls == []  # Never runs registry handler.
    assert reads == [{"project_id": "homelab"}]
    replay = provider.captured[1][0]
    assert replay[-2]["type"] == "function_call"
    assert replay[-1]["type"] == "function_call_output"
    body = json.loads(replay[-1]["output"])
    assert body["exact_next_action"] == "Verify homelab milestone"
    assert body["verification"]["freshness"] == "current"
    assert "fixture/not-for-model" not in str(replay)


def test_two_projects_are_read_and_model_compares_them_without_extra_tools():
    req = request("Porovnaj Homelab a Recipe Intelligence")
    provider = scripted(
        toolstep(call("call_a"), call("call_b", "recipe-intelligence")),
        final("Homelab má iné ďalšie kroky ako recepty. Odporúčam začať jedným."),
    )
    reads = []
    result = run_conversation(
        req, registry([]), build_decision_context(snapshot(), today=TODAY),
        provider, fake_reader(req, snapshot(), reads),
    )
    assert len(result.sources) == 2 and len(reads) == 2
    assert result.tool_calls == 2 and result.model_steps == 2
    assert "homelab: completed" in result.text
    assert "recipe-intelligence: completed" in result.text
    assert [p["type"] for p in provider.captured[1][0][-4:]] == [
        "function_call", "function_call", "function_call_output", "function_call_output"
    ]


def test_context_sensitive_followup_receives_bounded_history():
    req = request("A čo by si teda navrhoval ako ďalšie?")
    history = (
        ChatMessage("user", "Riešime Homelab."),
        ChatMessage("assistant", "Dobre, najprv si pozrime Project OS."),
    )
    provider = scripted(toolstep(call()), final("Na základe stavu by som volil tento krok."))
    answer = run_conversation(
        req, registry([]), build_decision_context(snapshot(), today=TODAY),
        provider, fake_reader(req, snapshot(), []), history=history,
    )
    first = provider.captured[0][0]
    assert first[0]["role"] == "user"
    assert first[1]["role"] == "assistant"
    assert first[2]["content"] == req.message
    assert len(answer.history) == 4
    assert answer.history[0] == history[0]


def test_large_conversation_is_trimmed_by_pairs_without_breaking_followup():
    ctx = build_decision_context(snapshot(), today=TODAY)
    conversation = ()
    for i in range(4):
        req = request(("O" * 3900) + f" question {i}")
        response_ = run_conversation(
            req, registry([]), ctx, scripted(final("A" * 3000)),
            lambda *_: pytest.fail("no tools expected"), history=conversation,
        )
        conversation = response_.history
        assert len(conversation) % 2 == 0
        assert sum(len(msg.text.encode("utf-8")) for msg in conversation) <= 8192
        assert conversation[-1].text == "A" * 3000
        assert conversation[-2].role == "user"


def test_direct_chat_works_without_project_lookup():
    req = request("Ahoj! Čo dnes vymyslíme?")
    called = []
    answer = run_conversation(
        req, registry([]), build_decision_context(snapshot(), today=TODAY),
        scripted(final("Ahoj! Môžeme pokračovať v našich projektoch. 🙂")),
        lambda *_: called.append(True),
    )
    assert "Ahoj!" in answer.text and answer.tool_calls == 0
    assert answer.sources == () and called == []


def test_stale_source_cannot_be_silently_labeled_current_in_provenance():
    req = request()
    old = snapshot(stale=True)
    result = run_conversation(
        req, registry([]), build_decision_context(old, today=TODAY),
        scripted(toolstep(call()), final("Toto je moje odporúčanie na kontrolu.")),
        fake_reader(req, old, []),
    )
    assert result.sources[0].freshness == "stale"
    assert "stale" in result.text
    assert "nie je aktuálne potvrdený" in result.text


def test_unavailable_source_is_explicit_and_never_fabricated():
    req = request()
    read = lambda r, selector: build_result(
        r, status="unavailable", freshness="unknown", error_code="source_unavailable",
    )
    provider = scripted(toolstep(call()), final("Záznam je momentálne nedostupný."))
    result = run_conversation(
        req, registry([]), build_decision_context(snapshot(), today=TODAY),
        provider, read,
    )
    assert result.sources[0].status == "unavailable"
    assert "unknown" in result.text
    assert "exact_next_action" not in provider.captured[1][0][-1]["output"]


@pytest.mark.parametrize("bad", [
    call(project="invented"),
    call(project="../../etc"),
    call(workstream="invented"),
    ModelToolCall("call_x", "shell_exec", {"command": "id"}),
    ModelToolCall("call_x", "project_os_status",
                  {"project_id": "homelab", "workstream_id": None,
                   "path": "/etc/passwd"}),
])
def test_model_cannot_expand_capabilities_or_invent_project(bad):
    req = request()
    called = []
    with pytest.raises(ConversationRejected, match="tool_denied"):
        run_conversation(
            req, registry([]), build_decision_context(snapshot(), today=TODAY),
            scripted(toolstep(bad), final()), lambda *_: called.append(True),
        )
    assert called == []


def test_all_proposed_calls_validated_before_any_real_read():
    req = request()
    called = []
    with pytest.raises(ConversationRejected, match="tool_denied"):
        run_conversation(
            req, registry([]), build_decision_context(snapshot(), today=TODAY),
            scripted(toolstep(call("call_ok"), call("call_bad", "unknown"))),
            lambda *_: called.append(True),
        )
    assert called == []


def test_bad_correlation_cannot_enter_model_context():
    req = request()
    called = []
    def wrong(r, selector):
        called.append(True)
        return replace(answer_project_status(r, snapshot(), selector, today=TODAY),
                       reply_target="another-conversation")
    provider = scripted(toolstep(call()), final())
    with pytest.raises(ConversationRejected, match="result_correlation"):
        run_conversation(
            req, registry([]), build_decision_context(snapshot(), today=TODAY),
            provider, wrong,
        )
    assert called == [True] and len(provider.captured) == 1


def test_duplicate_source_selector_read_only_once():
    req = request()
    reads = []
    provider = scripted(
        toolstep(call("call_one")), toolstep(call("call_two")),
        final("Zostali sme pri tej istej úlohe."),
    )
    result = run_conversation(
        req, registry([]), build_decision_context(snapshot(), today=TODAY),
        provider, fake_reader(req, snapshot(), reads),
    )
    assert result.model_steps == 3 and result.tool_calls == 2
    assert len(reads) == 1


def test_extra_model_tool_call_fails_closed_before_execution():
    req = request()
    called = []
    provider = scripted(
        toolstep(call("call_one"), call("call_two", "recipe-intelligence")),
        toolstep(call("call_three")),
    )
    with pytest.raises(ConversationRejected, match="tool_budget_exceeded"):
        run_conversation(
            req, registry([]), build_decision_context(snapshot(), today=TODAY),
            provider, lambda r, selector: (
                called.append(selector) or
                answer_project_status(r, snapshot(), selector, today=TODAY)
            ),
        )
    assert len(called) == MAX_TOOL_CALLS


def test_reject_invalid_history_or_erroring_model_without_raw_errors():
    req = request()
    bad_history = (ChatMessage("assistant", "untrusted"),)
    with pytest.raises(ConversationRejected, match="history_invalid"):
        run_conversation(
            req, registry([]), build_decision_context(snapshot(), today=TODAY),
            scripted(final()), lambda *_: None, history=bad_history,
        )
    def fail(*_args):
        raise RuntimeError("secret-credential-in-exception")
    with pytest.raises(ConversationRejected, match="^model_unavailable$"):
        run_conversation(
            req, registry([]), build_decision_context(snapshot(), today=TODAY),
            fail, lambda *_: None,
        )


def test_native_responses_schema_stateless_tool_and_text_support():
    ctx = build_decision_context(snapshot(), today=TODAY)
    body = json.loads(_build_request(({"role": "user", "content": "Čo ďalej s homelabom?"},), ctx))
    assert body["store"] is False
    assert body["reasoning"]["effort"] == "none"
    assert body["max_output_tokens"] == MAX_OUTPUT_TOKENS
    assert body["tools"][0]["strict"] is True
    assert body["tools"][0]["name"] == "project_os_status"
    assert set(body["tools"][0]["parameters"]["required"]) == {
        "project_id", "workstream_id",
    }
    assert body["tools"][0]["parameters"]["additionalProperties"] is False
    assert body["tool_choice"] == "auto"
    assert "fixture/not-for-model" not in str(body)
    tool, usage = _parse_step(response(calls=(call(),)))
    assert tool.text is None and len(tool.tool_calls) == 1
    assert tool.replay_items[0]["type"] == "function_call"
    assert usage.input_tokens == 500
    text, _ = _parse_step(response(answer="Ahoj!"))
    assert text.text == "Ahoj!" and text.tool_calls == ()


@pytest.mark.parametrize("raw", [
    b"bad",
    b"{}",
    response(status="incomplete", answer="half done"),
    response(answer=""),
    response(calls=(ModelToolCall("call_a", "shell_exec", {"command": "rm"}),)),
    response(calls=(call(),), answer="mixed outputs are forbidden"),
    response(answer="text", usage={"input_tokens": True, "output_tokens": 2}),
    response(answer="text", usage={"input_tokens": 1}),
    b'{"status":"completed","status":"completed"}',
    response(answer="x" * 3100),
])
def test_bad_or_refusal_mixed_model_outputs_denied(raw):
    with pytest.raises(ModelUnavailable, match="model_response_invalid"):
        _parse_step(raw)


def test_paid_adapter_explicit_opt_in_and_bounded_calls(monkeypatch, tmp_path):
    import personal_ai_brain.assistant_openai_conversation as module
    attempts = []
    def fake_post(raw, secret):
        payload = json.loads(raw)
        attempts.append(payload)
        assert secret.startswith("sk-test")
        return response(calls=(call(),)) if len(attempts) == 1 else response(answer="Hotovo.")
    monkeypatch.setattr(module, "_post", fake_post)
    key = key_file(tmp_path)
    context = build_decision_context(snapshot(), today=TODAY)
    items = ({"role": "user", "content": "Čo ďalej s Homelabom?"},)
    with pytest.raises(ModelUnavailable, match="model_call_not_authorized"):
        OpenAIConversationProvider(key)(items, context)
    assert attempts == []

    provider = OpenAIConversationProvider(key, allow_paid_call=True)
    first = provider(items, context)
    followup = (*items, *first.replay_items,
                {"type": "function_call_output", "call_id": "call_001", "output": "{}"})
    second = provider(followup, context)
    assert len(attempts) == 2 and second.text == "Hotovo."
    assert provider.total_input_tokens == 1000
    assert provider.total_output_tokens == 80
    assert all(p["store"] is False for p in attempts)
    assert attempts[1]["input"][-1]["call_id"] == "call_001"
    provider.attempts = MAX_PROVIDER_REQUESTS
    with pytest.raises(ModelUnavailable, match="model_call_not_authorized"):
        provider(items, context)
    assert len(attempts) == 2


def test_network_timeout_is_never_automatically_retried(monkeypatch, tmp_path):
    import personal_ai_brain.assistant_openai_conversation as module
    tries = []
    def fail(*_):
        tries.append(True)
        raise ModelUnavailable("model_timeout")
    monkeypatch.setattr(module, "_post", fail)
    provider = OpenAIConversationProvider(key_file(tmp_path), allow_paid_call=True)
    with pytest.raises(ModelUnavailable, match="model_timeout"):
        provider(({"role": "user", "content": "Ahoj"},),
                 build_decision_context(snapshot(), today=TODAY))
    assert provider.attempts == 1 and len(tries) == 1
