"""Offline *integration* acceptance for Brain Decision -> existing control -> result.

A fake decision provider and an in-memory GitHub Issues adapter exercise the real
validation, bounded capability handler, durable worker and ChatGPT result codec.
This is NOT a real LLM, authenticated GitHub session, production Gateway or
live outbound delivery. No new runtime/service is introduced by these tests.
"""

from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
from uuid import uuid4

import pytest

from personal_ai_brain import runtime
from personal_ai_brain.assistant_contract import normalize_request
from personal_ai_brain.assistant_control_codec import (
    ControlCodecRejected, decode_project_status, encode_project_status,
)
from personal_ai_brain.assistant_decision import DecisionRejected, decide
from personal_ai_brain.assistant_decision_context import build_decision_context
from personal_ai_brain.assistant_grounded_reply import compose_project_reply
from personal_ai_brain.control import Redactor, build_registry
from personal_ai_brain.control_worker import Worker, load_json
from personal_ai_brain.core_agent import CoreSnapshot, StateRecord


AUTHOR_ID = 12345
TODAY = date.today()


class MemoryIssues:
    """No GitHub access: the same worker API over local in-memory messages."""

    def __init__(self):
        self.issues = {}
        self.results = []

    def add(self, title, body):
        number = len(self.issues) + 1
        self.issues[number] = {
            "number": number, "title": title, "body": body, "state": "open",
        }
        return number

    def list_page(self, page):
        return [issue for issue in self.issues.values() if issue["state"] == "open"]

    def get_issue(self, number):
        return self.issues[number]

    def post_result(self, number, payload):
        body = "Personal AI Brain control result\n" + chr(96) * 3 + "json\n"
        body += json.dumps(payload, ensure_ascii=True) + "\n" + chr(96) * 3
        self.results.append((number, payload, body))

    def close_issue(self, number):
        self.issues[number]["state"] = "closed"

    def result(self, number):
        matching = [item for item in self.results if item[0] == number]
        assert len(matching) == 1
        return matching[0]


class FakeReader:
    def __init__(self, failed=False):
        self.read_failures = int(failed)


def project_record(*, verified=TODAY, gate="Check the media stack", workstream=False):
    return StateRecord(
        project_id="homelab", project_name=(
            "Homelab / Media Automation" if workstream else "Homelab & Media Stack"
        ),
        scope="workstream" if workstream else "project",
        repository="not-model-visible",
        path=(".project/workstreams/media-automation.yaml"
              if workstream else ".project/state.yaml"),
        attention="active",
        status="active",
        phase="post-migration",
        last_verified=verified.isoformat(),
        sync_status="synced",
        current_gate=gate,
        exact_next_action="Verify the TV poster and report the result",
    )


def snapshot(*, verified=TODAY, gate="Check the media stack"):
    return CoreSnapshot(
        generated_on=TODAY.isoformat(),
        records=[
            project_record(verified=verified, gate=gate),
            project_record(verified=verified, workstream=True),
        ],
        findings=[],
    )


def request(message):
    now = datetime.now(timezone.utc)
    return normalize_request(
        {
            "schema_version": "0.1",
            "request_id": str(uuid4()),
            "message": message,
            "created_at": now.isoformat(),
        },
        trusted_user_id="igor",
        trusted_surface="chatgpt",
        trusted_reply_target="conversation.123",
        now=now,
    )


def simulated_provider(arguments):
    def select(_message, capabilities, context):
        # Model cannot choose a repository path, permission or handler.
        assert [hint.id for hint in capabilities] == [
            "project_os.status", "core.health",
        ]
        assert context is not None
        assert context.permits(arguments)
        return json.dumps({
            "action": "invoke_read_only",
            "capability": "project_os.status",
            "arguments": arguments,
        })
    return select


def system(monkeypatch, tmp_path, data, *, failed=False):
    """Real registry/handler and durable worker, synthetic trusted Core read."""
    reads = []
    def collect(reader):
        reads.append(reader)
        return None, data, []
    monkeypatch.setattr(runtime, "collect", collect)
    registry = build_registry(
        Path("/unused-core-state"),
        lambda: FakeReader(failed),
        Redactor(),
    )
    issues = MemoryIssues()
    worker = Worker(tmp_path / "control", issues, registry, Redactor())
    return registry, worker, issues, reads


def plan_and_send(req, catalog, registry, issues):
    ctx = build_decision_context(catalog, today=TODAY)
    desired = {"project_id": "homelab", "workstream_id": "media-automation"}
    decision = decide(req, registry, simulated_provider(desired), context=ctx)
    assert decision.request_id == req.request_id
    assert decision.request_hash == req.payload_hash
    assert decision.action == "invoke_read_only"
    # Gateway still owns the actual authentication and dispatch integration.
    submission = encode_project_status(req, decision.arguments)
    number = issues.add(submission.title, submission.body)
    return number, decision


def decoded(req, decision, issues, number, *, author=AUTHOR_ID):
    _issue, _payload, comment = issues.result(number)
    return decode_project_status(
        req, decision.arguments, comment,
        comment_author_id=author,
        authorized_author_id=AUTHOR_ID,
    )


def test_status_message_reaches_actual_handler_and_correlated_result(monkeypatch, tmp_path):
    source = snapshot()
    registry, worker, issues, reads = system(monkeypatch, tmp_path, source)
    req = request("Čo máme ďalej robiť s Homelabom, konkrétne media automation?")
    issue, decision = plan_and_send(req, source, registry, issues)

    assert worker.tick()["last_request_status"] == "capability_completed"
    assert len(reads) == 1
    assert issues.issues[issue]["state"] == "closed"
    answer = decoded(req, decision, issues, issue)
    assert answer.status == "completed"
    assert answer.capability == "project_os.status"
    assert answer.surface == "chatgpt"
    assert answer.user_id == "igor"
    assert answer.reply_target == "conversation.123"
    assert answer.request_id == req.request_id
    assert answer.request_hash == req.payload_hash
    assert answer.freshness == "current"
    assert issues.result(issue)[1]["result"]["workstream_id"] == "media-automation"
    assert "Homelab / Media Automation" in answer.text
    assert "Verify the TV poster" in answer.text
    assert "not-model-visible" not in answer.text
    audit = load_json(tmp_path / "control" / "requests" / (req.request_id + ".json"))
    assert audit["status"] == "capability_completed"
    assert len(audit["payload_hash"]) == 64


def test_complete_offline_cycle_returns_grounded_slovak_answer_without_extra_execution(monkeypatch, tmp_path):
    """Authenticated result codec -> fact-only model focus -> final Slovak reply."""
    source = snapshot(gate="Check the media stack before a change")
    registry, worker, issues, reads = system(monkeypatch, tmp_path, source)
    req = request("Kde sme skončili s Media Automation a čo ďalej?")
    issue, decision = plan_and_send(req, source, registry, issues)
    assert worker.tick()["last_request_status"] == "capability_completed"
    verified_result = decoded(req, decision, issues, issue)

    focus_calls = []
    def focus(message, facts):
        focus_calls.append((message, facts.project_id))
        return "next_action"

    reply = compose_project_reply(
        req, decision, verified_result, focus_provider=focus,
    )
    assert reply.used_model
    assert reply.focus == "next_action"
    assert reply.request_id == req.request_id
    assert reply.reply_target == req.reply_target
    assert reply.freshness == "current"
    assert "Verify the TV poster and report the result" in reply.text
    assert "Check the media stack before a change" not in reply.text  # workstream gate
    assert "not-model-visible" not in reply.text
    assert "Posledné overenie" in reply.text
    assert len(reads) == 1
    assert focus_calls == [(req.message, "homelab")]


def test_replay_and_conflict_after_worker_restart_do_not_reexecute(monkeypatch, tmp_path):
    source = snapshot()
    registry, worker, issues, reads = system(monkeypatch, tmp_path, source)
    req = request("Kde sme skončili s media automation?")
    first, decision = plan_and_send(req, source, registry, issues)
    assert worker.tick()["last_request_status"] == "capability_completed"
    assert decoded(req, decision, issues, first).status == "completed"
    assert len(reads) == 1
    original = issues.issues[first]["body"]

    replay = issues.add("pab-control/v0.1", original)
    restarted = Worker(tmp_path / "control", issues, registry, Redactor())
    assert restarted.tick()["last_request_status"] == "duplicate"
    assert decoded(req, decision, issues, replay) == decoded(req, decision, issues, first)
    assert len(reads) == 1

    changed = json.loads(original)
    changed["requested_by"] = "different_requester"
    conflict = issues.add("pab-control/v0.1", json.dumps(changed))
    assert restarted.tick()["last_request_status"] == "request_rejected"
    assert issues.result(conflict)[1]["error"] == "request_conflict"
    assert decoded(req, decision, issues, conflict).status == "rejected"
    assert len(reads) == 1
    assert issues.issues[conflict]["state"] == "closed"


def test_stale_source_truth_is_not_promoted_to_current(monkeypatch, tmp_path):
    source = snapshot(verified=TODAY - timedelta(days=11))
    registry, worker, issues, reads = system(monkeypatch, tmp_path, source)
    req = request("Aký je stav media automation?")
    issue, decision = plan_and_send(req, source, registry, issues)
    worker.tick()
    answer = decoded(req, decision, issues, issue)
    assert answer.status == "completed"
    assert answer.freshness == "stale"
    assert "freshness: stale" in answer.text
    assert len(reads) == 1


def test_source_unavailable_returns_safe_failure_not_fabricated_status(monkeypatch, tmp_path):
    source = snapshot()
    registry, worker, issues, reads = system(monkeypatch, tmp_path, source, failed=True)
    req = request("Čo ďalej s media automation?")
    issue, decision = plan_and_send(req, source, registry, issues)
    assert worker.tick()["last_request_status"] == "capability_execution_failed"
    answer = decoded(req, decision, issues, issue)
    assert answer.status == "unavailable" and answer.freshness == "unknown"
    assert "Verify the TV poster" not in answer.text
    assert len(reads) == 1


@pytest.mark.parametrize("reply", [
    {"action": "clarify"},
    {"action": "respond_without_tool"},
    {"action": "unsupported"},
])
def test_no_tool_decisions_never_dispatch(monkeypatch, tmp_path, reply):
    source = snapshot()
    registry, worker, issues, reads = system(monkeypatch, tmp_path, source)
    req = request("Ahoj, ako je na tom ten projekt?")
    decision = decide(req, registry, lambda _m, _c, _ctx: json.dumps(reply))
    assert decision.capability is None
    assert decision.public_text
    assert issues.issues == {} and issues.results == []
    assert reads == []


@pytest.mark.parametrize("proposal", [
    {"action": "invoke_read_only", "capability": "project_os.status",
     "arguments": {"project_id": "invented"}},
    {"action": "invoke_read_only", "capability": "project_os.status",
     "arguments": {"project_id": "homelab", "path": "/etc/passwd"}},
    {"action": "invoke_read_only", "capability": "work.submit", "arguments": {}},
])
def test_forbidden_model_proposals_never_reach_transport(monkeypatch, tmp_path, proposal):
    source = snapshot()
    registry, worker, issues, reads = system(monkeypatch, tmp_path, source)
    req = request("Reštartuj Sonarr a zapis výsledok")
    ctx = build_decision_context(source, today=TODAY)
    with pytest.raises(DecisionRejected):
        decide(req, registry, lambda _m, _c, _ctx: json.dumps(proposal), context=ctx)
    assert issues.issues == {}
    assert reads == []


def test_untrusted_result_author_is_rejected(monkeypatch, tmp_path):
    source = snapshot()
    registry, worker, issues, reads = system(monkeypatch, tmp_path, source)
    req = request("Kde sme s media automation?")
    issue, decision = plan_and_send(req, source, registry, issues)
    worker.tick()
    with pytest.raises(ControlCodecRejected, match="untrusted_result_author"):
        decoded(req, decision, issues, issue, author=AUTHOR_ID + 1)
    assert len(reads) == 1
