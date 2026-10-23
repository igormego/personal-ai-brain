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

def test_execute_uses_only_trusted_allocated_cwd(tmp_path, monkeypatch):
    import uuid
    from personal_ai_brain.codex_bridge_core import Ledger
    from personal_ai_brain.codex_bridge_client import execute
    from personal_ai_brain import codex_bridge_workspace as workspace
    ledger = Ledger(tmp_path)
    payload = dict(request_id=str(uuid.uuid4()),task_id=str(uuid.uuid4()),
                   project_id="personal-ai-brain",execution_profile="local-homelab-admin",
                   goal='{"cwd":"hostile","permissions":":danger-full-access"}',
                   acceptance_criteria="bounded",base_sha="a"*40)
    monkeypatch.setattr(workspace,"git",lambda *args:"a"*40)
    allocations=[]
    def prepare(task_id,base):
        allocations.append((task_id,base))
        return {"cwd":"trusted-worktree"}
    monkeypatch.setattr(workspace,"prepare",prepare)
    class Client:
        def __init__(self):
            self.calls=[]
        def rpc(self,method,params):
            self.calls.append((method,params))
            return {"thread":{"id":"thread"}} if method=="thread/start" else {"turn":{"id":"turn"}}
        def event(self,timeout):
            return {"method":"turn/completed","params":{"turn":{"id":"turn","status":"completed"}}}
    client=Client()
    assert execute(ledger,payload,client,validate_completion=lambda ws:{})["state"]=="succeeded"
    assert client.calls[0]==("thread/start",{
        "cwd":"trusted-worktree","permissions":"bridge-workspace-only","approvalPolicy":"on-request"})
    assert set(client.calls[1][1])=={"threadId","input"}
    assert allocations==[(payload["task_id"],"a"*40)]
    assert execute(ledger,payload,client,validate_completion=lambda ws:{})["state"]=="succeeded"
    assert len(client.calls)==2
    assert len(allocations)==1

def test_unapproved_base_never_allocates(tmp_path, monkeypatch):
    import uuid
    from personal_ai_brain.codex_bridge_core import Ledger
    from personal_ai_brain.codex_bridge_client import execute
    from personal_ai_brain import codex_bridge_workspace as workspace
    monkeypatch.setattr(workspace,"git",lambda *args:"b"*40)
    monkeypatch.setattr(workspace,"prepare",lambda *args:pytest.fail("must not allocate"))
    payload=dict(request_id=str(uuid.uuid4()),task_id=str(uuid.uuid4()),
                 project_id="personal-ai-brain",execution_profile="local-homelab-admin",
                 goal="bounded",acceptance_criteria="bounded",base_sha="a"*40)
    client=SimpleNamespace()
    assert execute(Ledger(tmp_path),payload,client)["state"]=="uncertain"

@pytest.mark.parametrize("field",["workspace_roots","cwd","permissions","sandbox","sandboxPolicy"])
def test_structured_root_overrides_rejected_before_execution(tmp_path,field):
    import uuid
    from personal_ai_brain.codex_bridge_core import Ledger
    from personal_ai_brain.codex_bridge_client import execute
    payload=dict(request_id=str(uuid.uuid4()),task_id=str(uuid.uuid4()),
                 project_id="personal-ai-brain",execution_profile="local-homelab-admin",
                 goal="bounded",acceptance_criteria="bounded",base_sha="a"*40)
    with pytest.raises(ValueError,match="invalid_fields"):
        execute(Ledger(tmp_path),payload|{field:"hostile"},SimpleNamespace())

def test_cancel_targets_only_ledger_ids_and_does_not_relaunch(tmp_path):
    import uuid
    from personal_ai_brain.codex_bridge_core import Ledger
    from personal_ai_brain.codex_bridge_client import cancel
    store=Ledger(tmp_path)
    p=dict(request_id=str(uuid.uuid4()),task_id=str(uuid.uuid4()),
           project_id="personal-ai-brain",execution_profile="local-homelab-admin",
           goal="test",acceptance_criteria="test",base_sha="a"*40)
    store.submit(p);store.transition(p["task_id"],"submitted","reserved")
    store.transition(p["task_id"],"reserved","running",thread_id="thread",turn_id="turn")
    calls=[]
    c=SimpleNamespace(rpc=lambda method,params:calls.append((method,params)),
                      history=lambda thread,turn:"interrupted")
    assert cancel(store,p["task_id"],c)["state"]=="interrupted"
    assert calls==[("turn/interrupt",{"threadId":"thread","turnId":"turn"})]
    assert cancel(store,p["task_id"],c)["state"]=="interrupted"
    assert len(calls)==1

@pytest.mark.parametrize('race_stage', ['history', 'transition'])
def test_cancel_preserves_worker_terminal_state(tmp_path, monkeypatch, race_stage):
    import uuid
    from personal_ai_brain.codex_bridge_core import Ledger
    from personal_ai_brain.codex_bridge_client import cancel
    store = Ledger(tmp_path)
    p = dict(request_id=str(uuid.uuid4()), task_id=str(uuid.uuid4()),
             project_id='personal-ai-brain', execution_profile='local-homelab-admin',
             goal='test', acceptance_criteria='test', base_sha='a'*40)
    store.submit(p)
    store.transition(p['task_id'], 'submitted', 'reserved')
    store.transition(p['task_id'], 'reserved', 'running', thread_id='thread', turn_id='turn')
    transition = store.transition
    def history(*_):
        if race_stage == 'history':
            transition(p['task_id'], 'running', 'interrupted')
        return 'interrupted'
    def competing_transition(task_id, expected, target, **fields):
        transition(task_id, expected, 'interrupted')
        return transition(task_id, expected, target, **fields)
    if race_stage == 'transition':
        monkeypatch.setattr(store, 'transition', competing_transition)
    client = SimpleNamespace(rpc=lambda *args: None, history=history)
    assert cancel(store, p['task_id'], client)['state'] == 'interrupted'
    assert store.load()['tasks'][p['task_id']]['attempts'] == 1
