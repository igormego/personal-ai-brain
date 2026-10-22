"""Protocol ordering tests: exactly one reader, independent response/event delivery."""
import json
import queue
import threading
from types import SimpleNamespace
import pytest
from personal_ai_brain.codex_bridge_client import AppServer

class Pipe:
    def __init__(self):
        self.lines = queue.Queue()
    def readline(self, size):
        return self.lines.get(timeout=3)
    def put(self, value):
        self.lines.put(json.dumps(value) + "\n")

def client():
    obj = AppServer.__new__(AppServer)
    pipe = Pipe()
    obj.process = SimpleNamespace(stdout=pipe)
    obj.messages = queue.Queue(maxsize=256)
    obj.pending = {}
    obj.pending_lock = threading.Lock()
    obj.send_lock = threading.Lock()
    obj.server_request_handler = None
    obj.disconnected = False
    obj.serial = 0
    reader = threading.Thread(target=obj._read, daemon=True)
    reader.start()
    return obj, pipe, reader

@pytest.mark.parametrize("before,after", [
    ([], []),
    ([{"method":"progress"}], []),
    ([], [{"method":"progress"}]),
    ([{"method":"a"}, {"method":"b"}], []),
    ([{"id":90,"method":"item/commandExecution/requestApproval","params":{}}], []),
    ([], [{"id":90,"method":"item/commandExecution/requestApproval","params":{}}]),
    ([{"method":7}], [{"method":"turn/completed"}]),
    ([{"method":"turn/completed","params":{"turn":{"status":"interrupted"}}}], []),
])
def test_ordering(before, after):
    obj, pipe, reader = client()
    approvals = []
    obj.server_request_handler = approvals.append
    def send(message):
        for event in before:
            pipe.put(event)
        pipe.put({"id":message["id"],"result":{"ok":True}})
        for event in after:
            pipe.put(event)
    obj.send = send
    assert obj.rpc("thread/read", {}) == {"ok":True}
    from personal_ai_brain.codex_bridge_client import classify
    valid = [e for e in before + after if classify(e) != "malformed"]
    events = [obj.event(2) for _ in valid]
    assert len(events) == len(valid)
    assert len(approvals) == sum("id" in e and "method" in e for e in before+after)
    pipe.lines.put("")
    reader.join(2)
    assert not reader.is_alive()

def test_eof_wakes_pending_rpc():
    obj, pipe, reader = client()
    obj.send = lambda message: pipe.lines.put("")
    with pytest.raises(RuntimeError, match="disconnected"):
        obj.rpc("thread/read", {})
    reader.join(2)

def test_concurrent_responses_are_correlated():
    obj, pipe, reader = client()
    requests = queue.Queue()
    obj.send = requests.put
    results = {}
    def invoke(key):
        results[key] = obj.rpc("thread/read", {"key":key})
    threads = [threading.Thread(target=invoke,args=(key,)) for key in ("a","b")]
    for thread in threads:
        thread.start()
    first, second = requests.get(timeout=2), requests.get(timeout=2)
    pipe.put({"method":"progress"})
    for message in (second,first):
        pipe.put({"id":message["id"],"result":message["params"]["key"]})
    for thread in threads:
        thread.join(2)
    assert results == {"a":"a","b":"b"}
    assert obj.event(2)["method"] == "progress"
    pipe.lines.put("")
    reader.join(2)

def test_approval_callback_persists_while_rpc_waits(tmp_path):
    from personal_ai_brain.codex_bridge_core import Ledger, approval
    import uuid
    ledger = Ledger(tmp_path)
    payload = dict(request_id=str(uuid.uuid4()), task_id=str(uuid.uuid4()),
                   project_id="personal-ai-brain", execution_profile="local-homelab-admin",
                   goal="test", acceptance_criteria="test", base_sha="a"*40)
    ledger.submit(payload)
    ledger.transition(payload["task_id"], "submitted", "reserved")
    ledger.transition(payload["task_id"], "reserved", "running",
                      thread_id="thread", turn_id="turn")
    obj, pipe, reader = client()
    obj.server_request_handler = lambda event: approval(ledger, payload["task_id"], event)
    def send(message):
        pipe.put({"id":99,"method":"item/commandExecution/requestApproval",
                  "params":{"threadId":"thread","turnId":"turn"}})
        pipe.put({"id":message["id"],"result":{"ok":True}})
    obj.send = send
    assert obj.rpc("thread/read", {}) == {"ok":True}
    assert ledger.submit(payload)["state"] == "approval_required"
    assert obj.event(2)["id"] == 99
    pipe.lines.put("")
    reader.join(2)

@pytest.mark.parametrize("bad", [
    {"id": [], "result": {}}, {"id": {}, "result": {}},
    {"id": True, "result": {}}, {"id": 1.0, "result": {}},
    {"id": None, "result": {}}, {"result": {}},
    {"id": 1, "result": {}, "error": {"code":1,"message":"x"}},
    {"id": 1, "error": None}, {"id": 1, "error": {"code":True,"message":"x"}},
    [], "untrusted", 9, {"method": []}, {"method":"x","params":"bad"},
    {"id": [], "method":"x"}, {"id":False,"method":"x"},
])
def test_fault_between_valid_events_and_response(bad):
    obj, pipe, reader = client()
    approvals = []
    obj.server_request_handler = approvals.append
    def send(request):
        pipe.put({"method":"before"})
        pipe.put(bad)
        pipe.put({"id":900,"method":"item/commandExecution/requestApproval","params":{}})
        pipe.put({"method":"turn/completed","params":{"turn":{"status":"interrupted"}}})
        pipe.put({"id":request["id"],"result":{"ok":True}})
    obj.send = send
    assert obj.rpc("thread/read", {}) == {"ok":True}
    assert [obj.event(2)["method"] for _ in range(3)] == [
        "before","item/commandExecution/requestApproval","turn/completed"]
    assert len(approvals) == 1
    assert reader.is_alive()
    assert len(obj.diagnostics) == 1
    pipe.lines.put("")
    reader.join(2)

def test_unknown_duplicate_and_late_response_are_isolated():
    obj, pipe, reader = client()
    def send(request):
        pipe.put({"id":999,"result":"unknown"})
        pipe.put({"id":request["id"],"result":"first"})
        pipe.put({"id":request["id"],"result":"duplicate"})
        pipe.put({"method":"after"})
    obj.send=send
    assert obj.rpc("thread/read", {}) == "first"
    assert obj.event(2)["method"] == "after"
    assert reader.is_alive()
    pipe.lines.put("")
    reader.join(2)

def test_fault_flood_is_bounded():
    obj, pipe, reader = client()
    def send(request):
        for _ in range(200):
            pipe.put({"id":[],"result":"private"})
        pipe.put({"id":request["id"],"result":"ok"})
    obj.send=send
    assert obj.rpc("thread/read", {}) == "ok"
    assert len(obj.diagnostics) == 64
    assert all("private" not in str(entry) for entry in obj.diagnostics)
    assert reader.is_alive()
    pipe.lines.put("")
    reader.join(2)

def test_server_handler_failure_preserves_event_and_response():
    obj, pipe, reader = client()
    def handler(event):
        raise ValueError("private details")
    obj.server_request_handler=handler
    def send(request):
        pipe.put({"id":"server-1","method":"benign","params":{}})
        pipe.put({"method":"turn/completed"})
        pipe.put({"id":request["id"],"result":"ok"})
    obj.send=send
    assert obj.rpc("thread/read", {}) == "ok"
    assert obj.event(2)["method"] == "benign"
    assert obj.event(2)["method"] == "turn/completed"
    assert obj.diagnostics[-1]["protocol_error"] == "server_request_handler_failed"
    assert reader.is_alive()
    pipe.lines.put("")
    reader.join(2)
