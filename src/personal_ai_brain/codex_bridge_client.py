"""Private stdio client. Exposes no generic command or remote transport interface."""
import json
import queue
import subprocess
import threading
import time

from .codex_bridge_core import CWD, PROFILE, approval

OVERRIDES = [
    'windows.sandbox="mxc"',
    'default_permissions="bridge-workspace-only"',
    'permissions.bridge-workspace-only.extends=":workspace"',
    'permissions.bridge-workspace-only.filesystem={":root"="deny",":minimal"="read",":tmpdir"="deny",":slash_tmp"="deny"}',
    'permissions.bridge-workspace-only.network.enabled=false',
]

class AppServer:
    def __init__(self, executable):
        args = [str(executable), "app-server", "--listen", "stdio://"]
        for setting in OVERRIDES:
            args.extend(["-c", setting])
        self.process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, text=True, encoding="utf-8")
        self.messages = queue.Queue(maxsize=256)
        self.serial = 0
        self.pending = {}
        self.pending_lock = threading.Lock()
        self.send_lock = threading.Lock()
        self.server_request_handler = None
        self.disconnected = False
        threading.Thread(target=self._read, daemon=True).start()
        self.rpc("initialize", {"clientInfo": {"name": "pab_bridge_core", "version": "0.1"},
                               "capabilities": {"experimentalApi": True}})
        self.send({"method": "initialized"})

    def _read(self):
        failure = "app_server_disconnected"
        try:
            while True:
                line = self.process.stdout.readline(1024 * 1024 + 1)
                if not line:
                    break
                if len(line) > 1024 * 1024:
                    raise ValueError("event_too_large")
                try:
                    message = json.loads(line)
                    if not isinstance(message, dict):
                        raise ValueError("invalid_message")
                except (ValueError, TypeError):
                    self.messages.put({"client_diagnostic": "malformed_message"}, timeout=5)
                    continue
                if "method" in message:
                    if not isinstance(message["method"], str):
                        self.messages.put({"client_diagnostic": "malformed_message"}, timeout=5)
                        continue
                    if "id" in message and self.server_request_handler:
                        self.server_request_handler(message)
                    self.messages.put(message, timeout=5)
                elif "id" in message and ("result" in message or "error" in message):
                    with self.pending_lock:
                        waiter = self.pending.get(message["id"])
                    if waiter:
                        waiter.put(message, timeout=5)
                    else:
                        self.messages.put({"client_diagnostic": "unmatched_response"}, timeout=5)
                else:
                    self.messages.put({"client_diagnostic": "malformed_message"}, timeout=5)
        except Exception as exc:
            failure = "dispatcher_failed_" + type(exc).__name__
        finally:
            self.disconnected = True
            with self.pending_lock:
                waiters = list(self.pending.values())
            for waiter in waiters:
                try:
                    waiter.put_nowait({"client_failure": failure})
                except queue.Full:
                    pass
            try:
                self.messages.put_nowait({"client_failure": failure})
            except queue.Full:
                pass

    def send(self, message):
        with self.send_lock:
            self.process.stdin.write(json.dumps(message) + "\n")
            self.process.stdin.flush()

    def event(self, timeout=30):
        value = self.messages.get(timeout=timeout)
        if value.get("client_failure"):
            raise RuntimeError(value["client_failure"])
        return value

    def rpc(self, method, params):
        if method not in {"initialize", "thread/start", "turn/start", "turn/interrupt",
                          "thread/read", "thread/turns/list", "thread/items/list"}:
            raise ValueError("method_forbidden")
        with self.pending_lock:
            if self.disconnected:
                raise RuntimeError("app_server_disconnected")
            self.serial += 1
            request = self.serial
            waiter = queue.Queue(maxsize=1)
            self.pending[request] = waiter
        try:
            self.send({"id": request, "method": method, "params": params})
            message = waiter.get(timeout=30)
            if "client_failure" in message:
                raise RuntimeError(message["client_failure"])
            if "error" in message:
                raise RuntimeError("app_server_rpc_failed")
            return message["result"]
        finally:
            with self.pending_lock:
                self.pending.pop(request, None)

    def history(self, thread_id, turn_id):
        cursor = None
        for _ in range(100):
            params = {"threadId": thread_id, "limit": 50, "itemsView": "notLoaded"}
            if cursor:
                params["cursor"] = cursor
            response = self.rpc("thread/turns/list", params)
            for turn in response["data"]:
                if turn["id"] == turn_id:
                    return turn["status"]
            cursor = response.get("nextCursor")
            if not cursor:
                return None
        return None

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=10)
        for stream in (self.process.stdin, self.process.stdout):
            if stream:
                stream.close()

def execute(ledger, payload, client, timeout=120):
    """Reserve exactly once. Caller-supplied factory/bind are trusted local code."""
    task = ledger.submit(payload)
    if task["state"] != "submitted":
        return task
    task_id = task["task_id"]
    ledger.transition(task_id, "submitted", "reserved")
    def server_request(event):
        if event.get("method") in {
            "item/commandExecution/requestApproval", "item/fileChange/requestApproval",
            "item/permissions/requestApproval",
        }:
            current = ledger.submit(payload)
            params = event.get("params", {})
            if current["state"] == "running" and params.get("threadId") == current["thread_id"]:
                if current["turn_id"] is None and params.get("turnId"):
                    ledger.bind(task_id, current["thread_id"], params["turnId"])
                approval(ledger, task_id, event)
    client.server_request_handler = server_request
    try:
        result = client.rpc("thread/start", {
            "cwd": CWD, "permissions": PROFILE, "approvalPolicy": "on-request"})
        thread_id = result["thread"]["id"]
        ledger.bind(task_id, thread_id, None)
        # Persist running BEFORE the potentially ambiguous turn/start RPC.
        ledger.transition(task_id, "reserved", "running")
        result = client.rpc("turn/start", {
            "threadId": thread_id, "input": [{"type": "text", "text":
                task["goal"] + "\nAcceptance criteria:\n" + task["acceptance_criteria"]}]})
        turn_id = result["turn"]["id"]
        ledger.bind(task_id, thread_id, turn_id)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            event = client.event(min(30, max(.01, deadline - time.monotonic())))
            if ledger.submit(payload)["state"] == "approval_required":
                client.close()
                return ledger.submit(payload)
            if event.get("method") == "turn/completed":
                turn = event.get("params", {}).get("turn", {})
                if turn.get("id") != turn_id:
                    continue
                target = {"completed": "succeeded", "failed": "failed",
                          "interrupted": "interrupted"}.get(turn["status"], "uncertain")
                return ledger.transition(task_id, "running", target)
        client.rpc("turn/interrupt", {"threadId": thread_id, "turnId": turn_id})
    except Exception:
        pass
    current = ledger.submit(payload)
    if current["state"] in {"reserved", "running"}:
        return ledger.transition(task_id, current["state"], "uncertain",
                                 failure_class="execution_truth_uncertain")
    return current


