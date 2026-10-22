import uuid
import pytest
from personal_ai_brain.codex_bridge_core import Ledger, approval, public_result

def payload():
    return dict(request_id=str(uuid.uuid4()), task_id=str(uuid.uuid4()),
                project_id="personal-ai-brain", execution_profile="local-homelab-admin",
                goal="Harmless test", acceptance_criteria="Pass", base_sha="a"*40)

def test_duplicates_conflicts_and_no_second_reservation(tmp_path):
    ledger = Ledger(tmp_path)
    p = payload()
    ledger.submit(p)
    ledger.transition(p["task_id"], "submitted", "reserved")
    assert ledger.submit(p)["attempts"] == 1
    assert ledger.submit(p | {"request_id": str(uuid.uuid4())})["attempts"] == 1
    with pytest.raises(ValueError, match="task_conflict"):
        ledger.submit(p | {"request_id": str(uuid.uuid4()), "goal": "changed"})
    with pytest.raises(ValueError, match="request_conflict"):
        ledger.submit(p | {"goal": "changed"})
    with pytest.raises(ValueError):
        ledger.transition(p["task_id"], "submitted", "reserved")

@pytest.mark.parametrize("field", ["cwd", "permissions", "sandbox", "env", "model", "approval"])
def test_overrides_rejected(tmp_path, field):
    with pytest.raises(ValueError, match="invalid_fields"):
        Ledger(tmp_path).submit(payload() | {field: "hostile"})

@pytest.mark.parametrize("state,status,expected", [
    ("reserved", None, "uncertain"),
    ("running", None, "uncertain"),
    ("running", "completed", "succeeded"),
    ("running", "failed", "failed"),
    ("running", "interrupted", "interrupted"),
    ("approval_required", None, "uncertain"),
    ("approval_required", "interrupted", "interrupted"),
])
def test_recovery_never_starts(tmp_path, state, status, expected):
    ledger = Ledger(tmp_path)
    p = payload()
    ledger.submit(p)
    ledger.transition(p["task_id"], "submitted", "reserved")
    if state != "reserved":
        ledger.transition(p["task_id"], "reserved", "running",
                          thread_id="thread", turn_id="turn")
    if state == "approval_required":
        assert approval(ledger, p["task_id"], {"method": "item/commandExecution/requestApproval"})
    calls = []
    def history(thread, turn):
        calls.append((thread, turn))
        return status
    Ledger(tmp_path).recover(history)
    task = ledger.submit(p)
    assert task["state"] == expected
    assert task["attempts"] == 1
    assert len(calls) == (0 if state == "reserved" else 1)

def test_corrupt_ledger_fails_closed(tmp_path):
    ledger = Ledger(tmp_path)
    ledger.path.write_text("broken")
    with pytest.raises(ValueError):
        ledger.submit(payload())

def test_result_omits_untrusted_content(tmp_path):
    task = Ledger(tmp_path).submit(payload())
    task["raw_event"] = {"secret": "not for output"}
    result = public_result(task)
    assert not {"goal", "acceptance_criteria", "raw_event", "thread_id"} & result.keys()
