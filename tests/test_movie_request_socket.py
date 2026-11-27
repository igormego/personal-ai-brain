from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import pytest

from personal_ai_brain.approved_effect import EffectDenied, EffectUncertain
from personal_ai_brain.control import Redactor, Rejected, build_registry
from personal_ai_brain.movie_request_service import execute, reserve_movie, submit, verify_approval
from personal_ai_brain.movie_request_socket import PLAN, REQUEST, MovieSocketClient, checked_receipt


def write_request(request_id, tmdb=42):
    return {"operation": REQUEST, "arguments": {"tmdb_id": tmdb}, "request_id": request_id}


def test_read_never_touches_approval_ledger_or_submit():
    unused = Mock(side_effect=AssertionError("unexpected write path"))
    planner = Mock(return_value={"read": True})
    assert execute({"operation": PLAN, "arguments": {"title": "Fixture", "year": 2020},
                    "request_id": None}, planner=planner, get_approval=unused,
                   reserve=unused, submit_movie=unused) == {"read": True}
    planner.assert_called_once_with({"title": "Fixture", "year": 2020})
    unused.assert_not_called()


@pytest.mark.parametrize("change", [
    {"tmdb_id": True}, {"tmdb_id": 43}, {"request_id": "different"},
    {"issued_at": 101}, {"expires_at": 163}, {"expires_at": 1002}, {"extra": True},
])
def test_approval_binds_identity_request_and_remaining_deadline(change):
    key = str(uuid4())
    with pytest.raises(EffectDenied):
        verify_approval({"request_id": key, "tmdb_id": 42, "issued_at": 100,
                         "expires_at": 300, **change}, key, 42, 100)


def test_write_durably_reserves_before_submit_and_uncertainty_propagates():
    key = str(uuid4())
    audit = Mock()
    audit.approval.return_value = {"request_id": key, "tmdb_id": 42,
                                   "issued_at": 50, "expires_at": 300}
    audit.submit.side_effect = EffectUncertain("effect_outcome_uncertain")
    with pytest.raises(EffectUncertain):
        execute(write_request(key), planner=audit.plan, get_approval=audit.approval,
                reserve=audit.reserve, submit_movie=audit.submit, clock=lambda: 100)
    assert [c[0] for c in audit.mock_calls] == ["approval", "reserve", "submit"]
    audit.reserve.assert_called_once_with(key, 42)
    audit.submit.assert_called_once_with(42)


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX ledger")
def test_different_request_ids_cannot_reserve_the_same_movie(tmp_path):
    ledger = tmp_path / "requests"
    ledger.mkdir(mode=0o700)
    keys = [str(uuid4()), str(uuid4())]
    for key in keys:
        journal = ledger / f"{key}.json"
        journal.write_text(json.dumps({"status": "processing", "capability": REQUEST,
                                       "request_id": key, "arguments": {"tmdb_id": 42}}))
        journal.chmod(0o600)

    def reserve(key):
        try:
            reserve_movie(ledger, key, 42)
            return key
        except EffectUncertain:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        won = [key for key in pool.map(reserve, keys) if key is not None]
    assert len(won) == 1
    assert json.loads((ledger / ".movie-42.json").read_bytes()) == {
        "request_id": won[0], "tmdb_id": 42,
    }
    assert reserve(won[0]) is None
    with pytest.raises(EffectDenied):
        reserve_movie(ledger, won[0], 43)
    assert not (ledger / ".movie-43.json").exists()


def test_receipt_verifies_tmdb_status_and_effect():
    receipt = {"tmdb_id": 42, "seerr_request_id": 55, "request_status": 2,
               "effect_performed": True}
    assert checked_receipt(receipt, 42) == receipt
    for change in ({"tmdb_id": 43}, {"effect_performed": False}, {"request_status": 4}):
        with pytest.raises(EffectUncertain):
            checked_receipt({**receipt, **change}, 42)
    client = MovieSocketClient(Path("/never-used"))
    client._exchange = Mock(return_value={"contract_version": "0.1", "decision": "already_managed",
                                          "reason": "radarr_manages_movie", "identity": None,
                                          "evidence": None, "mutation_attempted": False})
    assert client.request({"tmdb_id": 42}, request_id=str(uuid4()))["effect_performed"] is False


def test_registry_defaults_off_and_accepts_only_a_tmdb_id(tmp_path):
    normal = build_registry(tmp_path, Mock(), Redactor())
    assert {PLAN, REQUEST}.isdisjoint(normal.capabilities)
    registry = build_registry(tmp_path, Mock(), Redactor(), media_plan_provider=Mock(),
                              movie_request_provider=Mock())
    assert registry.resolve(REQUEST, {"tmdb_id": 42})[1] == {"tmdb_id": 42}
    with pytest.raises(Rejected):
        registry.resolve(REQUEST, {"tmdb_id": 42, "execute": True})


def test_existing_request_cli_called_once_and_only_matching_readback_exits(monkeypatch):
    upstream = {
        "contract_version": "0.1", "decision": "requested", "identity": {"tmdb_id": 42},
        "request": {"tmdb_id": 42, "id": 7, "status": 2, "is4k": False},
        "audit": {"decision": "requested", "mutation_attempted": True, "tmdb_id": 42,
                  "request_id": 7, "request_status": 2},
        "intended_action": {"private": "never returned"},
    }
    proc = Mock(returncode=0, stdout=json.dumps(upstream).encode())
    run = Mock(return_value=proc)
    monkeypatch.setattr("personal_ai_brain.movie_request_service.subprocess.run", run)
    assert submit(42, {}) == {
        "tmdb_id": 42, "seerr_request_id": 7, "request_status": 2, "effect_performed": True,
    }
    run.assert_called_once()
    call = run.call_args.args[0]
    assert call[1].endswith("/controller/request-cli.mjs")
    assert call[2:] == ["--tmdb-id", "42", "--execute"]
    upstream["request"]["tmdb_id"] = 43
    proc.stdout = json.dumps(upstream).encode()
    with pytest.raises(EffectUncertain):
        submit(42, {})
    proc.stdout = json.dumps({
        **upstream, "decision": "already_managed", "reason": "radarr_manages_movie",
        "evidence": None, "identity": {"tmdb_id": 42, "title": "Fixture", "year": 2020},
        "audit": {"decision": "already_managed", "mutation_attempted": False},
    }).encode()
    result = submit(42, {})
    assert result["decision"] == "already_managed"
    assert "intended_action" not in result


@pytest.mark.skipif(os.name != "posix", reason="Control worker uses flock and SIGALRM")
@pytest.mark.parametrize("author,expected", [({"id": 1, "login": "someone"}, "request_rejected"),
                                            ({"id": 55916200, "login": "igormego"}, "capability_completed")])
def test_worker_uses_authenticated_author_and_passes_uuid(tmp_path, author, expected):
    from personal_ai_brain.control_worker import Worker
    from personal_ai_brain.github_issues import TITLE

    provider = Mock(return_value={"effect_performed": True})
    registry = build_registry(tmp_path, Mock(), Redactor(), movie_request_provider=provider)
    request_id = str(uuid4())
    transport = Mock()
    transport.get_issue.return_value = {
        "number": 1, "state": "open", "title": TITLE, "user": author,
        "body": json.dumps({"schema_version": "0.1", "request_id": request_id,
                            "capability": REQUEST, "arguments": {"tmdb_id": 42},
                            "requested_by": "owner", "requested_at": datetime.now(timezone.utc).isoformat()}),
    }
    worker = Worker(tmp_path, transport, registry, Redactor())
    assert worker.process({"number": 1}) == expected
    if expected == "request_rejected":
        provider.assert_not_called()
    else:
        provider.assert_called_once_with({"tmdb_id": 42}, request_id=request_id)
