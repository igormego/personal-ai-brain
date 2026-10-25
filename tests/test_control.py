from datetime import datetime, timezone, timedelta
import io
import json
import multiprocessing
from pathlib import Path
import time
from urllib.error import HTTPError
from uuid import uuid4

import pytest

from personal_ai_brain.control import Capability, Redactor, Rejected, Registry, build_registry, no_arguments, parse_request
from personal_ai_brain.control_worker import Worker, bounded_result, durable_json, health
from personal_ai_brain.github_issues import GitHubIssues, REPOSITORY, TITLE, TransportError


def request(**changes):
    data = dict(schema_version="0.1", request_id=str(uuid4()), capability="runtime.health",
                arguments={}, requested_by="test", requested_at=datetime.now(timezone.utc).isoformat())
    data.update(changes)
    return data


class FakeTransport:
    def __init__(self, bodies):
        self.issues = {n: dict(number=n, title=TITLE, state="open", body=body) for n, body in enumerate(bodies, 1)}
        self.comments = []
        self.fail_post = False

    def list_page(self, page):
        return [v for v in self.issues.values() if v["state"] == "open"]

    def get_issue(self, number):
        return self.issues[number]

    def post_result(self, number, result):
        if self.fail_post:
            raise TransportError("transport_unavailable")
        self.comments.append((number, result))

    def close_issue(self, number):
        self.issues[number]["state"] = "closed"


def make_worker(tmp_path, bodies, handler=lambda _: {"ok": True}):
    cap = Capability("runtime.health", "test", "read_only", {}, no_arguments, handler)
    transport = FakeTransport(bodies)
    return Worker(tmp_path, transport, Registry([cap]), Redactor(["private-secret"])), transport


def test_valid_protocol():
    data = request()
    assert parse_request(json.dumps(data)) == data


@pytest.mark.parametrize("body", ["{}", "[]", "null", "not-json", "x" * 8193,
                                  '{"a":1,"a":2}', '{"a":NaN}', '[[[' * 3000])
def test_malformed_protocol(body):
    with pytest.raises(Rejected):
        parse_request(body)


@pytest.mark.parametrize("change", [dict(schema_version="1"), dict(request_id="../other"),
    dict(arguments=[]), dict(requested_by="root;exec"), dict(requested_at="2026-09-25"),
    dict(extra=True), dict(requested_at=(datetime.now(timezone.utc) - timedelta(days=8)).isoformat()),
    dict(requested_at=(datetime.now(timezone.utc) + timedelta(days=1)).isoformat())])
def test_strict_schema(change):
    with pytest.raises(Rejected):
        parse_request(json.dumps(request(**change)))


def test_unknown_capability_and_invalid_arguments_rejected(tmp_path):
    worker, transport = make_worker(tmp_path, [json.dumps(request(capability="run_shell")), json.dumps(request(arguments={"command": "id"}))])
    worker.tick()
    assert [r[1]["status"] for r in transport.comments] == ["request_rejected"] * 2
    assert not list((tmp_path / "requests").iterdir())


def test_duplicate_id_and_completed_issue_never_execute_again(tmp_path):
    body = json.dumps(request())
    calls = []
    worker, transport = make_worker(tmp_path, [body, body], lambda args: calls.append(args))
    worker.tick()
    assert len(calls) == 1
    assert transport.comments[1][1]["status"] == "duplicate"
    transport.issues[1]["state"] = "open"
    worker.tick()
    assert len(calls) == 1 and len(transport.comments) == 2


def test_failed_delivery_retries_result_without_reexecution(tmp_path):
    calls = []
    worker, transport = make_worker(tmp_path, [json.dumps(request())], lambda args: calls.append(args))
    transport.fail_post = True
    with pytest.raises(TransportError):
        worker.tick()
    transport.fail_post = False
    worker.tick()
    assert len(calls) == 1 and len(transport.comments) == 1


def test_interrupted_reservation_is_not_reexecuted(tmp_path):
    data = request()
    calls = []
    worker, transport = make_worker(tmp_path, [json.dumps(data)], lambda args: calls.append(args))
    durable_json(tmp_path / "requests" / (data["request_id"] + ".json"),
                 {"status": "processing", "transport_issue": "previous", "request_id": data["request_id"]})
    worker.tick()
    assert not calls
    assert transport.comments[0][1]["original_status"] == "capability_execution_failed"


def test_exception_sanitized_worker_continues_and_correlates(tmp_path):
    calls = []
    def handler(_):
        calls.append(1)
        if len(calls) == 1:
            raise ValueError("private-secret")
        return {"text": "private-secret"}
    first, second = request(), request()
    worker, transport = make_worker(tmp_path, [json.dumps(first), json.dumps(second)], handler)
    worker.tick()
    assert [r[1]["status"] for r in transport.comments] == ["capability_execution_failed", "capability_completed"]
    assert transport.comments[0][1]["request_id"] == first["request_id"]
    for path in tmp_path.rglob("*.json"):
        assert "private-secret" not in path.read_text()
    assert "private-secret" not in json.dumps(transport.comments)


def test_registry_policy_and_duplicate_ids():
    cap = Capability("example.read", "test", "read_only", {}, no_arguments, lambda _: None)
    registry = Registry([cap])
    with pytest.raises(Rejected):
        registry.register(cap)
    with pytest.raises(Rejected):
        registry.register(Capability("example.write", "test", "low_risk_write", {}, no_arguments, lambda _: None))


def test_bounded_redacted_logs_and_no_paths(tmp_path):
    (tmp_path / "service.log").write_text(("private-secret github_pat_fake Bearer abc\n" * 500))
    registry = build_registry(tmp_path, None, Redactor(["private-secret"]))
    cap, args = registry.resolve("runtime.logs_tail", {"lines": 100})
    result = cap.handler(args)
    assert len(result["text"].encode()) <= 4096
    assert len(result["text"].splitlines()) <= 100
    assert "private-secret" not in result["text"] and "github_pat_fake" not in result["text"]
    for arguments in ({"path": "/etc/passwd"}, {"lines": True}, {"lines": 101}, {"lines": 0}):
        with pytest.raises(Rejected):
            registry.resolve("runtime.logs_tail", arguments)


def test_result_size_bound_after_redaction():
    value = bounded_result({"text": "private-secret" * 5000}, Redactor(["private-secret"]))
    assert value["truncated"] and len(json.dumps(value)) < 16000
    assert "private-secret" not in json.dumps(value)


@pytest.mark.parametrize("status", ["healthy", "transport_unavailable", "authentication_permission_failure"])
def test_worker_liveness_distinct_from_transport(tmp_path, status):
    beat = tmp_path / "beat.json"
    durable_json(beat, {"time": time.time()})
    durable_json(tmp_path / "status.json", {"transport": status, "last_request_status": "request_rejected"})
    data, code = health(tmp_path, beat)
    assert data["runtime"] == "healthy"
    assert code == (0 if status == "healthy" else 3)
    beat.unlink()
    assert health(tmp_path, beat)[0]["runtime"] == "unavailable"


@pytest.mark.parametrize("code,expected", [(401, "authentication_permission_failure"), (403, "authentication_permission_failure"),
    (404, "authentication_permission_failure"), (429, "transport_unavailable"), (500, "transport_unavailable")])
def test_transport_failure_sanitization(code, expected):
    def fail(req, **kwargs):
        raise HTTPError(req.full_url, code, "private-secret", {}, None)
    transport = GitHubIssues("private-secret", opener=fail)
    with pytest.raises(TransportError) as caught:
        transport.verify_private()
    assert caught.value.status == expected
    assert "private-secret" not in str(caught.value)


def test_rate_limit_waits_for_server_reset():
    def fail(req, **kwargs):
        raise HTTPError(req.full_url, 403, "rate", {"X-RateLimit-Remaining": "0", "Retry-After": "1200"}, None)
    with pytest.raises(TransportError) as caught:
        GitHubIssues("test", opener=fail).verify_private()
    assert caught.value.retry_after >= 1200


def test_only_issues_comment_and_close_writes_are_possible():
    seen = []
    def opener(req, **kwargs):
        seen.append((req.method, req.full_url, json.loads(req.data) if req.data else None))
        return io.BytesIO(json.dumps({"private": True, "has_issues": True, "full_name": REPOSITORY}).encode())
    transport = GitHubIssues("test", opener=opener, sleep=lambda _: None)
    transport.post_result(1, {"request_id": "test", "status": "capability_completed"})
    transport.close_issue(1)
    assert [v[0] for v in seen] == ["GET", "POST", "PATCH"]
    for method, path, payload in [("PUT", "/contents/state.yaml", {}), ("POST", "/issues", {}),
                                   ("PATCH", "/issues/1", {"body": "edit"}), ("GET", "/contents/private", None)]:
        with pytest.raises(TransportError):
            transport._request(method, path, payload)
    assert len(seen) == 3


def test_public_transport_denied_before_post():
    seen = []
    def opener(req, **kwargs):
        seen.append(req.method)
        return io.BytesIO(json.dumps({"private": False, "has_issues": True, "full_name": REPOSITORY}).encode())
    with pytest.raises(TransportError):
        GitHubIssues("test", opener=opener).post_result(1, {})
    assert seen == ["GET"]


def competing_worker(root, body, counter):
    def handler(_):
        with counter.get_lock():
            counter.value += 1
        time.sleep(0.2)
        return {}
    worker, _ = make_worker(Path(root), [body], handler)
    worker.tick()


def test_concurrent_processes_execute_only_once(tmp_path):
    ctx = multiprocessing.get_context("fork")
    counter = ctx.Value("i", 0)
    body = json.dumps(request())
    processes = [ctx.Process(target=competing_worker, args=(str(tmp_path), body, counter)) for _ in range(2)]
    for process in processes:
        process.start()
    for process in processes:
        process.join(5)
        assert process.exitcode == 0
    assert counter.value == 1


def test_corrupt_ledger_fails_closed(tmp_path):
    data = request()
    calls = []
    worker, _ = make_worker(tmp_path, [json.dumps(data)], lambda _: calls.append(1))
    (tmp_path / "requests" / (data["request_id"] + ".json")).write_text("broken")
    with pytest.raises(ValueError):
        worker.tick()
    assert not calls


def test_core_handlers_read_only_and_findings_are_success(tmp_path, monkeypatch):
    from personal_ai_brain import runtime
    from personal_ai_brain.core_agent import CoreAgent, CoreSnapshot, Finding
    snapshot = CoreSnapshot("2026-09-25", findings=[Finding("error", "missing", "test", "field")])
    class Reader:
        read_failures = 0
    monkeypatch.setattr(runtime, "collect", lambda reader: (CoreAgent(reader), snapshot, []))
    registry = build_registry(tmp_path, Reader, Redactor())
    for name in ("core.health", "core.snapshot", "core.report", "core.rollup_proposals"):
        cap, args = registry.resolve(name, {})
        result = cap.handler(args)
        assert result["source_status"] == "findings" and result["health"]["errors"] == 1
    assert list(tmp_path.iterdir()) == []


def test_canonical_transport_identity_and_redirect_boundary():
    from personal_ai_brain.github_issues import NoRedirect
    seen = []

    def opener(req, **kwargs):
        seen.append(req.full_url)
        return io.BytesIO(json.dumps({
            "private": True, "has_issues": True,
            "full_name": "igormego/personal-project-brain",
        }).encode())

    GitHubIssues("test", opener=opener).verify_private()
    assert seen == ["https://api.github.com/repos/igormego/personal-project-brain"]
    assert NoRedirect().redirect_request(
        None, None, 301, "Moved", {}, "https://api.github.com/repos/other/repo"
    ) is None


def test_homelab_ignores_windows_bridge_protocol(tmp_path):
    calls = []
    worker, transport = make_worker(tmp_path, [json.dumps(request())], lambda args: calls.append(args))
    transport.issues[1]['title'] = 'pab-codex/v0.1'
    worker.tick()
    assert not calls and not transport.comments
    assert transport.issues[1]['state'] == 'open'
    assert not list((tmp_path / 'requests').iterdir())
