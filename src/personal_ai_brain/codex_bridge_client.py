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
        threading.Thread(target=self._read, daemon=True).start()
        self.rpc("initialize", {"clientInfo": {"name": "pab_bridge_core", "version": "0.1"},
                               "capabilities": {"experimentalApi": True}})
        self.send({"method": "initialized"})

    def _read(self):
        try:
            for line in self.process.stdout:
                if len(line) > 1024 * 1024:
                    raise ValueError("event_too_large")
                self.messages.put(json.loads(line), timeout=5)
        finally:
            try:
                self.messages.put({"client_disconnected": True}, timeout=1)
            except queue.Full:
                pass

    def send(self, message):
        self.process.stdin.write(json.dumps(message) + "\n")
        self.process.stdin.flush()

    def event(self, timeout=30):
        value = self.messages.get(timeout=timeout)
        if value.get("client_disconnected"):
            raise RuntimeError("app_server_disconnected")
        return value

    def rpc(self, method, params):
        # Only methods owned by this adapter; no shellCommand or command/exec.
        if method not in {"initialize", "thread/start", "turn/start", "turn/interrupt",
                          "thread/read", "thread/turns/list", "thread/items/list"}:
            raise ValueError("method_forbidden")
        self.serial += 1
        request = self.serial
        self.send({"id": request, "method": method, "params": params})
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            message = self.event(max(.01, deadline - time.monotonic()))
            if message.get("id") == request and "method" not in message:
                if "error" in message:
                    raise RuntimeError("app_server_rpc_failed")
                return message["result"]
            if "id" in message and "method" in message:
                raise RuntimeError("approval_required")
        raise TimeoutError("rpc_timeout")

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
            if approval(ledger, task_id, event):
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

